"""Orquestación del flujo de pedidos por WhatsApp.

Conecta interpret_message (interpretación del LLM), construir_resumen_pedido
(precios reales desde la BD) y draft_store (borrador de pedido en curso por
cliente) para decidir qué responder en cada mensaje entrante.

Reparto de responsabilidades:
- El LLM solo EXTRAE datos del mensaje (productos, aclaraciones, nombre,
  dirección, rechazo de ubicación, etc.) y redacta respuestas para consultas
  de precio/pedidos o preguntas sobre productos.
- El CÓDIGO decide qué dato falta y qué se le pregunta al cliente en cada
  turno (ver _datos_faltantes), de a un paso, según el draft y si el cliente
  ya existe en la tabla cliente (identificado por teléfono, ver
  cliente_lookup.py). Así reglas como "nunca pedir el nombre a un cliente
  existente" o "nunca preguntar '¿algo más?' sin productos" no dependen del
  prompt.
- La tabla cliente solo se escribe al confirmar el pedido, en la misma
  transacción que crea el pedido (ver _confirmar_pedido).
"""

import json
import logging
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Auditoria, Cliente, DetallePedido, Pedido, Producto
from app.models.enums import EstadoPedido
from app.services.agent_service import (
    CATALOGO_NOMBRES,
    _formatear_clp,
    construir_resumen_pedido,
    interpret_message,
)
from app.services.auditoria_service import construir_snapshot
from app.services.cliente_lookup import buscar_clientes_por_telefono, normalizar_telefono
from app.services.conversacion_bot_service import marcar_inactiva
from app.services.draft_store import clear_draft, get_draft, get_lock, save_draft
from app.services.horario_atencion import en_horario_atencion, mensaje_derivacion_ejecutiva
from app.services.notificacion_ejecutiva import clasificar_clientes, texto_notificacion_ejecutiva
from app.services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)

# Estados del draft una vez que ya se mostró el resumen. Solo desde
# "esperando_confirmacion" (el último mensaje del bot fue el resumen) un sí
# explícito confirma el pedido; tras un "no" se pasa a
# "esperando_modificacion", donde un "sí" NO confirma (ver procesar_mensaje).
ESTADO_ESPERANDO_CONFIRMACION = "esperando_confirmacion"
ESTADO_ESPERANDO_MODIFICACION = "esperando_modificacion"

# Únicas respuestas que confirman el pedido (texto normalizado, sin tildes).
# Pueden ir acompañadas de cortesía ("sí, gracias"), nada más: "ok", "ya" o
# "bueno" no confirman.
CONFIRMACIONES = {"si", "confirmo", "confirmar", "dale"}
_CORTESIA = {"por", "favor", "porfa", "gracias"}

# Negativa "pura" ante el resumen ("no", "no gracias", "todavía no"). Un "no"
# con más contenido ("no, agrega un bidón") pasa al LLM como modificación.
_NEGACIONES_SIMPLES = {"no", "nop", "nope", "nel", "negativo"}
_PALABRAS_NEGATIVA = _NEGACIONES_SIMPLES | {
    "gracias", "todavia", "aun", "mejor", "por", "ahora", "asi", "lo", "confirmo", "quiero",
}

# Valor de auditoria.usuario para los cambios en cliente hechos por el bot
# (los endpoints del panel usan el email del usuario autenticado).
USUARIO_AUDITORIA_BOT = "bot_whatsapp"

PREGUNTA_PRODUCTO = (
    "¿Qué producto y qué cantidad quieres pedir? Tenemos bidones de 12L y 20L "
    "(nuevos o de recarga), dispensadores y promociones."
)

PREGUNTA_ALGO_MAS = "¿Deseas agregar algo más a tu pedido?"

PREGUNTA_QUE_MAS = "¡Claro! ¿Qué otro producto y qué cantidad quieres agregar?"

MENSAJE_PEDIR_NOMBRE = "¿A nombre de quién registramos tu pedido?"

PREGUNTA_CONFIRMAR_DIRECCION = "¿Despachamos a {direccion}?"

PREGUNTA_DIRECCION_Y_UBICACION = (
    "¿Cuál es la dirección de despacho (calle y número)? Si puedes, compártela "
    "también como ubicación de WhatsApp: usa el clip 📎 y selecciona 'Ubicación'."
)

PREGUNTA_DIRECCION_NUEVA_Y_UBICACION = (
    "Entendido. ¿Cuál es la nueva dirección de despacho (calle y número)? Si puedes, "
    "compártela también como ubicación de WhatsApp: usa el clip 📎 y selecciona 'Ubicación'."
)

# Sin mencionar la ubicación de WhatsApp: se usa cuando la ubicación ya se
# recibió o el cliente ya la rechazó (camino de solo texto).
PREGUNTA_DIRECCION_TEXTO = "¿Me escribes la dirección de despacho (calle y número)?"

PREGUNTA_UBICACION = (
    "¿Me compartes tu ubicación de WhatsApp? Usa el clip 📎 y selecciona 'Ubicación'. "
    "Si no puedes, avísame y seguimos solo con la dirección que me diste."
)

PREGUNTA_CAPACIDAD_PROMO = "¿Prefieres que los bidones de la promo sean de 12L o de 20L?"

PREGUNTA_CAPACIDAD_BIDON = "¿{bidones} quieres de {capacidades}?"

PREGUNTA_MODELO = "¿Cuál {familia} quieres? Tenemos estas opciones:\n{opciones}"

MENSAJE_NO_ENCONTRADO = "No encontré {producto} en nuestro catálogo."

PREGUNTA_ACLARACION_BIDON = (
    "¿{cantidad} bidón(es) de {capacidad}L nuevo(s) (con envase) o de recarga "
    "(solo el agua, entregando tu bidón vacío)?"
)

# Varios bidones pendientes de distinta capacidad (ver
# _pregunta_variantes_bidones).
PREGUNTA_ACLARACION_BIDONES = (
    "¿{bidones} los quieres nuevos (con envase) o de recarga (solo el agua, "
    "entregando tu bidón vacío)? Puedes responder por capacidad, por ejemplo: «{ejemplo}»."
)

MENSAJE_UBICACION_RECIBIDA ="¡Gracias, recibí tu ubicación!"

MENSAJE_ERROR_PEDIDO = (
    "Hubo un problema al registrar tu pedido, por favor intenta de nuevo o contacta a un ejecutivo."
)

MENSAJE_PEDIDO_CANCELADO = (
    "Listo, cancelé tu pedido en curso. Si quieres hacer un pedido nuevo, cuéntame qué necesitas."
)

MENSAJE_NO_CONFIRMADO = "Entendido, no lo confirmo. ¿Qué quieres cambiar o prefieres cancelar el pedido?"

PREGUNTA_CANCELAR = "¿Quieres cancelar el pedido? Responde CANCELAR, o dime qué quieres cambiar."

PREFIJO_REPETIR_RESUMEN = "Antes de confirmar, revisemos tu pedido una vez más."

PREGUNTA_OPCIONES_PEDIDO = "¿Quieres cambiar algo, confirmar el pedido o cancelarlo?"

MENSAJE_CORRECCION_APLICADA = "Listo, hice el cambio."

# Después del resumen, "quiero el dispensador básico" con un dispensador en
# el pedido: puede ser un cambio o algo que se suma, se pregunta (ver
# _cambio_atributo_simple y _responder_a_correccion).
PREGUNTA_CAMBIAR_O_AGREGAR = "¿Quieres cambiar el {actual} por el {nuevo}, o agregarlo al pedido?"

MENSAJE_PRODUCTO_AGREGADO = "Listo, lo agregué."

# Respuestas a PREGUNTA_CAMBIAR_O_AGREGAR (texto normalizado).
_PATRON_RESPUESTA_CAMBIAR = re.compile(r"\b(cambi\w*|reemplaz\w*)\b")
_PATRON_RESPUESTA_AGREGAR = re.compile(r"\b(agreg\w*|sum\w*|anad\w*|los dos|las dos|ambos|ambas)\b")

# Pedido explícito de cambio que no modificó nada del pedido (ver
# _aplicar_resultado_llm): nunca se vuelve a mostrar el mismo resumen como si
# el cambio se hubiera hecho.
MENSAJE_CAMBIO_NO_APLICADO = (
    "No pude aplicar el cambio. ¿Me dices qué producto y cuántas unidades quieres? "
    "Tengo anotado: {pedido}."
)

# Igual que el anterior, pero cuando el cliente no dijo que era un cambio
# ("quiero el dispensador básico" después del resumen, ver
# correccion_implicita en _aplicar_resultado_llm): el texto no asume que lo
# pidió.
MENSAJE_MODIFICACION_NO_ENTENDIDA = (
    "No entendí qué quieres modificar. ¿Me dices qué producto y cuántas unidades quieres? "
    "Tengo anotado: {pedido}."
)

MENSAJE_CORRECCION_RECHAZADA = (
    "Entendido, lo dejo como está. ¿Qué quieres cambiar, o prefieres confirmar o cancelar el pedido?"
)

MENSAJE_DUDA_GENERICA = "Perdón por la confusión. Tengo anotado en tu pedido: {pedido}."

MENSAJE_DUDA_SIN_PRODUCTOS = "Perdón por la confusión."

MENSAJE_PRODUCTOS_NO_REGISTRADOS = (
    "Antes de mostrarte el resumen, revisemos tu pedido: tengo anotado {registrados}, "
    "pero también mencionaste {faltantes}, que no quedó registrado. ¿Qué quieres agregar? "
    "Si no quieres agregar nada, responde NO."
)

# Intenciones cuya "respuesta_sugerida" se envía tal cual (la arma el backend
# desde la BD o es el rechazo de un tema fuera de alcance), sin reemplazarla
# por la pregunta del paso pendiente.
_INTENCIONES_RESPUESTA_LLM = ("consulta_precio", "consulta_pedidos", "fuera_de_alcance")

# Palabras que indican una cancelación EXPLÍCITA del pedido en curso. Es la
# única forma de vaciar un draft con productos ya confirmados (ver
# _aplicar_resultado_llm): un simple saludo o mensaje ambiguo nunca debe
# borrar el pedido, pero esto sí. Se comparan con similitud difusa para
# tolerar erratas ("canelar", "cancelr"), ver _intencion_cancelar.
_PALABRAS_CANCELACION = (
    "cancela", "cancelar", "cancelo", "cancele", "cancelen", "cancelalo", "cancelarlo",
    "anula", "anular", "anulo", "anulalo", "anularlo",
)

# Umbrales de SequenceMatcher.ratio() contra _PALABRAS_CANCELACION, medidos
# con erratas y palabras parecidas: una errata de una letra ("canelar",
# "cacelar", "cancelr", "cancear") da 0,93; "canela"/"canelo" 0,92;
# "canelos"/"candela" 0,86; "manuela"/"ancla" 0,83; "cancha" 0,77;
# "celular"/"calle" 0,67.
UMBRAL_CANCELACION = 0.93
UMBRAL_DUDA_CANCELACION = 0.8

# Una errata solo cancela directamente en mensajes cortos ("canelar",
# "canelar el pedido"); en uno más largo se pregunta.
MAX_PALABRAS_CANCELACION_DIFUSA = 3

# Pasos en que el cliente escribe texto libre (nombres, calles): ahí la
# similitud difusa nunca cancela por sí sola ("Los Canelos 345", "Candela").
_PASOS_TEXTO_LIBRE = ("nombre", "direccion", "ubicacion", "confirmar_direccion")

# Negaciones que, antes de la palabra de cancelación y en la misma frase,
# indican que el cliente NO quiere cancelar ("no cancelen mi pedido").
_NEGACIONES = {"no", "nunca", "ni", "tampoco", "jamas"}

# Palabras de cada tipo de producto, para saber si un mensaje menciona
# productos (texto normalizado, ver _normalizar_texto).
_PATRONES_TIPO_PRODUCTO = {
    "bidon": re.compile(r"\b(bidon\w*|recargas?|nuev[oa]s?|litros?|envases?|12|20|12l|20l)\b"),
    "dispensador": re.compile(r"\b(dispensador\w*|usb|basico\w*|maquina\w*)\b"),
    "promo": re.compile(r"\b(promo\w*|combo\w*)\b"),
}

# Pedido EXPLÍCITO de cambiar o quitar algo ya pedido ("cambia", "mejor
# dos", "quita el dispensador"). Sin esto, una línea del pedido nunca se
# reemplaza ni se elimina: lo que extrae el LLM solo se suma.
_PATRON_MODIFICACION = re.compile(
    r"\b(cambi\w*|mejor|quit\w*|saca\w*|elimin\w*|borr\w*|reemplaz\w*|correg\w*|corrig\w*"
    r"|solo|sin|deja\w*|sean|en vez|en lugar|ya no|no quiero)\b"
)

# Palabras que indican que el cliente quiere SUMAR un producto ("quiero
# también un dispensador", "agrega otro bidón"), no corregir uno ya pedido.
# Sin ellas, después del resumen "quiero el dispensador básico" puede ser
# cualquiera de las dos cosas (ver correccion_implicita en
# _aplicar_resultado_llm). "quiero" NO va en _PATRON_MODIFICACION: en "¿algo
# más?", "sí, quiero un dispensador" agrega (bug del 2026-10-03).
_PATRON_ADICION = re.compile(r"\b(tambien|ademas|otr[oa]s?|agreg\w*|sum\w*|mas)\b")

_NUMEROS_TEXTO = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}

# Palabras que pueden ir entre una cantidad y la variante del bidón
# ("2 bidones de 20 recarga").
_RELLENO_CANTIDAD_VARIANTE = {"bidon", "bidones", "de", "l", "litro", "litros"}

# Frases (no solo palabras sueltas) que indican que el texto está PIDIENDO o
# volviendo a CONFIRMAR dirección/ubicación al cliente. Se usan para no dejar
# pasar una respuesta del LLM que salta a otro paso cuando lo pendiente es
# el producto (ver _respuesta_es_de_producto).
_FRASES_REABREN_DIRECCION_UBICACION = (
    "confirmas tu dirección",
    "confirmas tu direccion",
    "confirmes tu dirección",
    "confirmes tu direccion",
    "confirmar tu dirección",
    "confirmar tu direccion",
    "indicar una distinta",
    "indicar tu dirección",
    "indicar tu direccion",
    "indica tu dirección",
    "indica tu direccion",
    "cuál es tu dirección",
    "cual es tu direccion",
    "dirección de despacho",
    "direccion de despacho",
    "compartas tu ubicación",
    "compartas tu ubicacion",
    "compartas la ubicación",
    "compartas la ubicacion",
    "compartir tu ubicación",
    "compartir tu ubicacion",
    "comparte tu ubicación",
    "comparte tu ubicacion",
    "necesito tu ubicación",
    "necesito tu ubicacion",
    "necesito que compartas",
)

_PALABRAS_PRODUCTO = (
    "bidón",
    "bidon",
    "dispensador",
    "promo",
    "recarga",
    "nuevo",
    "12l",
    "20l",
    "12 l",
    "20 l",
    "litros",
    "producto",
)

_AFIRMATIVAS = (
    "si",
    "dale",
    "ok",
    "okay",
    "correcto",
    "confirmo",
    "exacto",
    "claro",
    "perfecto",
    "ya",
    "bueno",
    "esa",
    "esa misma",
    "la misma",
    "la de siempre",
    "de acuerdo",
    "afirmativo",
)

_INDICADORES_DIRECCION_DISTINTA = ("otra", "distinta", "diferente", "cambi", "nueva")

_INDICADORES_NADA_MAS = (
    "nada mas",
    "eso es todo",
    "es todo",
    "solo eso",
    "ya esta",
    "eso seria",
    "eso nomas",
    "nada",
)

_PREFIJOS_NOMBRE = ("a nombre de", "mi nombre es", "me llamo", "soy")


def _normalizar_texto(texto: str | None) -> str:
    """Minúsculas, sin tildes ni puntuación, espacios colapsados."""
    texto = unicodedata.normalize("NFKD", (texto or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s]", " ", texto)
    return " ".join(texto.split())


def _empieza_con(texto_normalizado: str, frases: tuple[str, ...]) -> bool:
    return any(
        texto_normalizado == frase or texto_normalizado.startswith(frase + " ")
        for frase in frases
    )


def _es_afirmativa(texto: str | None) -> bool:
    return _empieza_con(_normalizar_texto(texto), _AFIRMATIVAS)


def _pide_direccion_distinta(texto: str | None) -> bool:
    """Respuesta negativa a "¿Despachamos a {dirección}?" ("no", "no, es
    otra", "quiero cambiarla", etc.)."""
    texto_normalizado = _normalizar_texto(texto)
    return _empieza_con(texto_normalizado, ("no",)) or any(
        indicador in texto_normalizado for indicador in _INDICADORES_DIRECCION_DISTINTA
    )


def _no_quiere_nada_mas(texto: str | None) -> bool:
    """Respuesta negativa a "¿Deseas agregar algo más?"."""
    texto_normalizado = _normalizar_texto(texto)
    return _empieza_con(texto_normalizado, ("no",)) or any(
        indicador in texto_normalizado for indicador in _INDICADORES_NADA_MAS
    )


def _nombre_desde_texto(texto: str | None) -> str | None:
    """Fallback en código para el paso "nombre": si el LLM no extrajo el
    nombre pero el mensaje es claramente solo un nombre de persona (pocas
    palabras, sin dígitos), se toma tal cual."""
    nombre = (texto or "").strip().strip(".!¡")
    nombre_normalizado = _normalizar_texto(nombre)
    for prefijo in _PREFIJOS_NOMBRE:
        if nombre_normalizado.startswith(prefijo + " "):
            nombre = " ".join(nombre.split()[len(prefijo.split()):])
            break
    palabras = nombre.split()
    if not 1 <= len(palabras) <= 4 or any(ch.isdigit() for ch in nombre):
        return None
    if not all(palabra.replace("-", "").replace("'", "").isalpha() for palabra in palabras):
        return None
    return nombre


def _es_confirmacion_explicita(texto: str | None) -> bool:
    tokens = _normalizar_texto(texto).split()
    return any(t in CONFIRMACIONES for t in tokens) and all(
        t in CONFIRMACIONES or t in _CORTESIA for t in tokens
    )


def _es_negativa_simple(texto: str | None) -> bool:
    tokens = _normalizar_texto(texto).split()
    return any(t in _NEGACIONES_SIMPLES for t in tokens) and all(
        t in _PALABRAS_NEGATIVA for t in tokens
    )


def _tipo_producto(nombre: str) -> str:
    if nombre.startswith("Promo"):
        return "promo"
    if nombre.startswith("Dispensador"):
        return "dispensador"
    return "bidon"


def _menciona_tipo(tipo: str, texto_normalizado: str) -> bool:
    return bool(_PATRONES_TIPO_PRODUCTO[tipo].search(texto_normalizado))


def _menciona_algun_producto(texto_normalizado: str) -> bool:
    return any(_menciona_tipo(tipo, texto_normalizado) for tipo in _PATRONES_TIPO_PRODUCTO)


_PATRON_TEMA_PEDIDO = re.compile(
    r"\b(pedido\w*|total|cobr\w*|asum\w*|supus\w*|entreg\w*|despach\w*|resumen|envio)\b"
)


_PATRON_RECLAMO = re.compile(
    r"\b(por que|porque|asum\w*|supus\w*|error\w*|equivoc\w*|no (te )?(pedi|dije)|reclam\w*|mal)\b"
)


def _es_pregunta_o_reclamo(mensaje: str | None) -> bool:
    """El mensaje pregunta o reclama ("¿por qué asumes...?", "eso está
    mal"), a diferencia de una respuesta ("2 recargas y 2 nuevos"), que no
    es una duda aunque hable de productos."""
    return "?" in (mensaje or "") or bool(_PATRON_RECLAMO.search(_normalizar_texto(mensaje)))


def _habla_del_pedido(texto_normalizado: str) -> bool:
    """El mensaje habla del pedido, sus productos o la entrega (y por lo
    tanto no está fuera de alcance aunque el LLM lo marque así): "¿por qué
    asumes que quiero bidones de 12?". "¿Cuánto cuesta una pizza?" no."""
    return _menciona_algun_producto(texto_normalizado) or bool(_PATRON_TEMA_PEDIDO.search(texto_normalizado))


def _intencion_cancelar(texto: str | None, texto_libre: bool = False) -> str | None:
    """"cancelar" si el mensaje cancela el pedido en curso, "duda" si podría
    querer cancelar pero no es claro (hay que preguntarle), None si no.

    Detección por palabra clave, tolerante a erratas ("canelar"). RIESGO de
    falsos positivos: una palabra parecida a "cancelar" no siempre significa
    "cancela el pedido", y cancelar borra todo el draft. Por eso:
    - Frases con una negación hasta 3 palabras antes, en la misma frase, NO
      cancelan: "no cancelen mi pedido", "no me lo cancelen". La frase se
      corta en la puntuación, así que "no, cancela" sí cancela.
    - Si la frase menciona productos ("cancela el dispensador") puede querer
      quitar solo ese producto: es "duda", no se cancela todo.
    - Solo cancelan directamente las formas exactas (empiezan con "cancel" o
      "anul") y las erratas de una letra en mensajes cortos. Una similitud
      menor ("canelos", "candela", "manuela") o una errata dentro de un
      mensaje largo es "duda".
    - En pasos de texto libre (nombre, dirección) la similitud difusa nunca
      cancela, y solo pregunta si el mensaje es una sola palabra con una
      errata de una letra ("canelar"): "Los Canelos 345" es una dirección y
      "Manuela" un nombre, no cancelaciones.
    - Palabras de menos de 5 letras no se evalúan ("nulo").
    Si aparece un falso positivo nuevo, conviene sumar la palabra a una
    lista de excepciones en vez de subir los umbrales.
    """
    frases = re.split(r"[,.;:!?¿¡\n]+", texto or "")
    total_palabras = len(_normalizar_texto(texto).split())
    resultado = None
    for frase in frases:
        tokens = _normalizar_texto(frase).split()
        for i, token in enumerate(tokens):
            if len(token) < 5:
                continue
            exacta = token.startswith(("cancel", "anul"))
            similitud = 1.0 if exacta else max(
                SequenceMatcher(None, token, palabra).ratio() for palabra in _PALABRAS_CANCELACION
            )
            if similitud < UMBRAL_DUDA_CANCELACION:
                continue
            if any(t in _NEGACIONES for t in tokens[max(0, i - 3):i]):
                continue
            if texto_libre and not exacta and (
                total_palabras > 1 or similitud < UMBRAL_CANCELACION
            ):
                continue
            menciona_producto = _menciona_algun_producto(" ".join(tokens))
            if exacta and not menciona_producto:
                return "cancelar"
            if (
                similitud >= UMBRAL_CANCELACION
                and not texto_libre
                and not menciona_producto
                and total_palabras <= MAX_PALABRAS_CANCELACION_DIFUSA
            ):
                return "cancelar"
            resultado = "duda"
    return resultado


def _cantidad_valida(valor) -> int:
    """Cantidad entera de un ítem; sin cantidad es 1 (regla del prompt)."""
    if valor is None:
        return 1
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# Productos: familias, atributos y menciones en el mensaje
#
# Cada producto del catálogo pertenece a una familia (primera palabra del
# nombre: bidón, dispensador, promo) y tiene atributos que lo distinguen de
# los demás de su familia (las otras palabras: "20l", "recarga", "usb",
# "basico", "2"...). Una mención del cliente ("5 bidones", "dispensador",
# "promo usb") calza con los productos de su familia que tienen todos los
# atributos mencionados: si calza con uno solo se agrega; si calza con
# varios queda PENDIENTE y se pregunta; si no calza con ninguno se le dice
# que no existe. Esto se decide en código y desde el catálogo de la BD, sin
# depender de que el LLM pregunte (bug del 2026-10-03 en la noche: con
# "quiero 5 bidones" el LLM asumió 12L, y "dispensador" sin modelo se
# perdió en silencio).
# --------------------------------------------------------------------------

_FAMILIAS = {"bidon": "Bidón", "dispensador": "Dispensador", "promo": "Promo"}

# Palabras del nombre que nombran la familia y no distinguen un producto.
_PALABRAS_FAMILIA = {"bidon", "bidones", "dispensador", "promo"}

# Capacidades de bidón del catálogo (12 y 20).
_CAPACIDADES = sorted({int(c) for c in re.findall(r"(\d+)L\b", " ".join(CATALOGO_NOMBRES))})

# Palabras que solo indican el fin de la mención de una promo ("promo usb y
# 3 bidones de 20" son dos productos).
_SEPARADORES_MENCION = {"y", "mas", "tambien", "ademas"}


def _familia(nombre: str) -> str:
    tokens = _normalizar_texto(nombre).split()
    return _FAMILIAS.get(tokens[0], nombre) if tokens else nombre


def _atributos(nombre: str) -> frozenset[str]:
    """"Bidón 20L Recarga" → {"20l", "recarga"}; "Promo Dispensador USB + 2
    Bidones" → {"usb", "2"}."""
    return frozenset(t for t in _normalizar_texto(nombre).split() if t not in _PALABRAS_FAMILIA)


def _atributo_de(token: str) -> str | None:
    """Forma canónica de un atributo escrito por el cliente ("nuevos",
    "envase" → "nuevo"; "recargas" → "recarga"; "básica" → "basico")."""
    if re.fullmatch(r"nuev[oa]s?|envases?", token):
        return "nuevo"
    if re.fullmatch(r"recargas?", token):
        return "recarga"
    if re.fullmatch(r"basic[oa]s?", token):
        return "basico"
    if token == "usb":
        return "usb"
    return None


def _categoria(nombre: str) -> str:
    """Agrupa las variantes de un mismo bidón ("Bidón 20L Recarga" y "Bidón
    20L Nuevo" son "Bidón 20L"); el resto de productos es su propio nombre."""
    coincidencia = re.match(r"Bidón (\d+)L\b", nombre)
    return f"Bidón {coincidencia.group(1)}L" if coincidencia else nombre


def _capacidad_en(tokens: list[str], j: int) -> int | None:
    """Capacidad si tokens[j] la indica: "20l", "20 litros", o "12"/"20"
    después de "de" ("de 20"). Puede no existir en el catálogo ("5 litros")."""
    coincidencia = re.fullmatch(r"(\d+)(l|lt|lts)", tokens[j])
    if coincidencia:
        return int(coincidencia.group(1))
    if tokens[j].isdigit():
        siguiente = tokens[j + 1] if j + 1 < len(tokens) else ""
        if siguiente in ("l", "lt", "lts", "litro", "litros"):
            return int(tokens[j])
        if int(tokens[j]) in _CAPACIDADES and j > 0 and tokens[j - 1] in ("de", "a", "por"):
            return int(tokens[j])
    return None


def _cantidad_antes_de(tokens: list[str], indice: int) -> int | None:
    """Cantidad escrita justo antes de una palabra ("5 bidones", "dos
    dispensadores", "una promo")."""
    j = indice - 1
    while j >= 0 and tokens[j] in ("el", "la", "los", "las"):
        j -= 1
    if j < 0:
        return None
    if tokens[j].isdigit():
        return int(tokens[j])
    return _NUMEROS_TEXTO.get(tokens[j])


_UNIDADES_LITRO = ("l", "lt", "lts", "litro", "litros")

# Palabras que pueden ir entre una cantidad y la capacidad de un bidón sin
# variante ("3 bidones de 20", "1 botellón de 12 litros").
_RELLENO_LINEA_BIDON = _RELLENO_CANTIDAD_VARIANTE | {"botellon", "botellones"}


def _expandir_multiplicacion(texto_normalizado: str) -> str:
    """"3x20" / "3 x 20l" → "3 de 20" / "3 de 20l", solo si es una capacidad
    del catálogo o lleva unidad (así lo entiende el resto del parser)."""

    def reemplazo(coincidencia: re.Match) -> str:
        cantidad, capacidad, unidad = coincidencia.group(1), coincidencia.group(2), coincidencia.group(3) or ""
        if unidad or int(capacidad) in _CAPACIDADES:
            return f"{cantidad} de {capacidad}{unidad}"
        return coincidencia.group(0)

    return re.sub(r"\b(\d+) ?x ?(\d+)(l|lt|lts)?\b", reemplazo, texto_normalizado)


def _mencion_bidon_rango(tokens: list[str], indice: int) -> tuple[int | None, int | None, int, int]:
    """(cantidad, capacidad, primer token, último token) de la mención de
    bidón alrededor de la variante en tokens[indice]: "2 recargas", "dos
    nuevos", "2 bidones de 20 litros recarga", "1 de 12 nuevo", "2 recargas
    de 20". Cantidad y capacidad son None si no aparecen."""
    cantidad = capacidad = None
    inicio = fin = indice
    j = indice - 1
    while j >= 0:
        token = tokens[j]
        capacidad_token = _capacidad_en(tokens, j)
        if capacidad_token is not None:
            capacidad = capacidad or capacidad_token
        elif token.isdigit():
            cantidad, inicio = int(token), j
            break
        elif token in _NUMEROS_TEXTO:
            cantidad, inicio = _NUMEROS_TEXTO[token], j
            break
        elif token not in _RELLENO_CANTIDAD_VARIANTE:
            break
        inicio = j
        j -= 1
    if capacidad is None and indice + 2 < len(tokens) and tokens[indice + 1] == "de":
        capacidad = _capacidad_en(tokens, indice + 2)
        fin = indice + 2 if capacidad is not None else fin
    if capacidad is None and indice + 1 < len(tokens):
        capacidad = _capacidad_en(tokens, indice + 1)
        fin = indice + 1 if capacidad is not None else fin
    if fin > indice and fin + 1 < len(tokens) and tokens[fin + 1] in _UNIDADES_LITRO:
        fin += 1
    return cantidad, capacidad, inicio, fin


def _mencion_bidon(tokens: list[str], indice: int) -> tuple[int | None, int | None]:
    """(cantidad, capacidad) de la mención en tokens[indice], ver
    _mencion_bidon_rango."""
    return _mencion_bidon_rango(tokens, indice)[:2]


def _menciones_bidon_con_rango(texto_normalizado: str) -> list[tuple[str, int | None, int | None, int, int]]:
    """[(variante, cantidad, capacidad, primer token, último token)] de cada
    "recarga"/"nuevo" del mensaje, en orden. "nuevo" solo cuenta si el
    mensaje habla de bidones (dice "bidón", una capacidad o "recarga"): "un
    pedido nuevo" o "es una dirección nueva" no son bidones."""
    tokens = _expandir_multiplicacion(texto_normalizado).split()
    habla_de_bidones = any(
        re.fullmatch(r"bidon(es)?|recargas?", t) or _capacidad_en(tokens, j) is not None
        for j, t in enumerate(tokens)
    )
    menciones = []
    for i, token in enumerate(tokens):
        if re.fullmatch(r"recargas?", token):
            variante = "Recarga"
        elif re.fullmatch(r"nuev[oa]s?", token) and habla_de_bidones:
            variante = "Nuevo"
        else:
            continue
        menciones.append((variante, *_mencion_bidon_rango(tokens, i)))
    return menciones


def _menciones_bidon(texto_normalizado: str) -> list[tuple[str, int | None, int | None]]:
    """[(variante, cantidad, capacidad)] de cada "recarga"/"nuevo" del
    mensaje, en orden (ver _menciones_bidon_con_rango)."""
    return [mencion[:3] for mencion in _menciones_bidon_con_rango(texto_normalizado)]


def _lineas_bidon(texto_normalizado: str, desglose: bool = True) -> tuple[list[dict], set[int]]:
    """Todas las líneas de bidón del mensaje, detectadas en código, en orden:
    [{"variante", "cantidad", "capacidad", "inicio"}], y los índices de los
    tokens que usan.

    - Con variante: "3 de 20 recarga", "2 recargas", "1 de 12 nuevo".
    - Sin variante, por su capacidad: "3 de 20", "uno de 12", "3 bidones de
      20 litros", "2 de 20L", "3x20" (bug del 2026-10-08: "3 de 20 y 1 de 12"
      no detectaba ninguna línea y "3 bidones de 20 y 1 de 12" perdía la de
      12L). Una capacidad cuenta solo si _capacidad_en la reconoce (del
      catálogo después de "de", o con unidad) y lleva cantidad o la palabra
      "bidón": "el 5 de octubre", "a las 12" o "a 20 cuadras" no son bidones.

    Si alguna variante va sin capacidad ("4 de 20: 2 recargas y 2 nuevos")
    puede ser el desglose de una línea por capacidad: en ese caso no se
    agregan las líneas sin variante, para no contar dos veces. Con
    desglose=False (al responder una aclaración, donde la variante sin
    capacidad es la respuesta a lo pendiente) sí se agregan.
    """
    tokens = _expandir_multiplicacion(texto_normalizado).split()
    con_variante = _menciones_bidon_con_rango(" ".join(tokens))
    lineas = [
        {"variante": variante, "cantidad": cantidad, "capacidad": capacidad, "inicio": inicio}
        for variante, cantidad, capacidad, inicio, _ in con_variante
    ]
    usados = {k for *_, inicio, fin in con_variante for k in range(inicio, fin + 1)}
    if desglose and any(capacidad is None for _, _, capacidad, _, _ in con_variante):
        return lineas, usados

    for j in range(len(tokens)):
        if j in usados:
            continue
        capacidad = _capacidad_en(tokens, j)
        if capacidad is None:
            continue
        k = j - 1
        menciona_bidon = False
        while k >= 0 and k not in usados and tokens[k] in _RELLENO_LINEA_BIDON:
            menciona_bidon = menciona_bidon or bool(re.fullmatch(r"bidon(es)?|botellon(es)?", tokens[k]))
            k -= 1
        cantidad = _cantidad_antes_de(tokens, k + 1) if k >= 0 and k not in usados else None
        if cantidad is None and not menciona_bidon:
            continue
        inicio = k if cantidad is not None else k + 1
        fin = j + 1 if j + 1 < len(tokens) and tokens[j + 1] in _UNIDADES_LITRO else j
        usados.update(range(inicio, fin + 1))
        lineas.append({"variante": None, "cantidad": cantidad, "capacidad": capacidad, "inicio": inicio})
    lineas.sort(key=lambda linea: linea["inicio"])
    return lineas, usados


def _agrupar_variantes(menciones: list[tuple[str, int | None, int | None]]) -> dict[str, int | None]:
    """{"Recarga": n, "Nuevo": m} sumando las menciones de cada variante, en
    el orden en que se mencionan (None = mencionada sin cantidad)."""
    cantidades: dict[str, int | None] = {}
    for variante, cantidad, _ in menciones:
        if cantidades.get(variante) is not None and cantidad is not None:
            cantidades[variante] += cantidad
        elif cantidad is not None or variante not in cantidades:
            cantidades[variante] = cantidad
    return cantidades


def _cantidades_por_variante(texto_normalizado: str, capacidad: int | None) -> dict[str, int | None]:
    """{"Recarga": n, "Nuevo": m} según lo que el mensaje dice de cada
    variante de esa capacidad (o sin capacidad explícita), en el orden en que
    se mencionan (None = mencionada sin cantidad)."""
    return _agrupar_variantes(
        [m for m in _menciones_bidon(texto_normalizado) if m[2] in (None, capacidad)]
    )


def _menciones_productos(texto_normalizado: str) -> list[dict]:
    """Productos que el mensaje menciona, detectados en código: [{"familia",
    "cantidad", "atributos", "variante"?, "capacidad"?}]. Primero las promos
    (que contienen "dispensador" y "bidones" en su nombre), luego los
    dispensadores y al final los bidones (todas sus líneas, ver
    _lineas_bidon)."""
    tokens = _expandir_multiplicacion(texto_normalizado).split()
    usados: set[int] = set()
    menciones = []

    for i, token in enumerate(tokens):
        if not re.fullmatch(r"promo\w*|combo\w*", token):
            continue
        fin = i + 1
        while fin < min(len(tokens), i + 8) and tokens[fin] not in _SEPARADORES_MENCION:
            fin += 1
        ventana = tokens[i:fin]
        atributos = {a for t in ventana if (a := _atributo_de(t)) in ("usb", "basico")}
        bidones = re.search(r"\b(1|un|uno|una|2|dos) bidon(es)?\b", " ".join(ventana))
        if bidones:
            atributos.add("2" if bidones.group(1) in ("2", "dos") else "1")
        usados.update(range(i, fin))
        menciones.append({"familia": "Promo", "cantidad": _cantidad_antes_de(tokens, i), "atributos": atributos})

    for i, token in enumerate(tokens):
        if i in usados or not re.fullmatch(r"dispensador\w*|maquina\w*", token):
            continue
        usados.add(i)
        atributos = set()
        for j in range(max(0, i - 1), min(len(tokens), i + 4)):
            atributo = _atributo_de(tokens[j])
            if j not in usados and atributo in ("usb", "basico"):
                atributos.add(atributo)
                usados.add(j)
            elif j > i and re.fullmatch(r"dispensador\w*", tokens[j]):
                usados.add(j)  # "máquina dispensadora": una sola mención
        menciones.append(
            {"familia": "Dispensador", "cantidad": _cantidad_antes_de(tokens, i), "atributos": atributos}
        )

    libres = [t if i not in usados else "_" for i, t in enumerate(tokens)]
    lineas, usados_bidon = _lineas_bidon(" ".join(libres))
    for linea in lineas:
        variante, capacidad = linea["variante"], linea["capacidad"]
        atributos = ({variante.lower()} if variante else set()) | ({f"{capacidad}l"} if capacidad else set())
        menciones.append(
            {
                "familia": "Bidón",
                "cantidad": linea["cantidad"],
                "atributos": atributos,
                "variante": variante,
                "capacidad": capacidad,
            }
        )
    # "5 bidones" (sin capacidad ni variante). No si alguna variante va sin
    # capacidad: puede ser el desglose de esos bidones ("4 bidones, 2
    # recargas y 2 nuevos").
    if not any(linea["variante"] and linea["capacidad"] is None for linea in lineas):
        for i, token in enumerate(libres):
            if i in usados_bidon or not re.fullmatch(r"bidon(es)?|botellon(es)?", token):
                continue
            capacidad = next(
                (c for j in range(i + 1, min(len(libres), i + 4)) if (c := _capacidad_en(libres, j)) is not None),
                None,
            )
            menciones.append(
                {
                    "familia": "Bidón",
                    "cantidad": _cantidad_antes_de(libres, i),
                    "atributos": {f"{capacidad}l"} if capacidad else set(),
                    "variante": None,
                    "capacidad": capacidad,
                }
            )
    return menciones


def _atributos_mensaje(texto_normalizado: str) -> set[str]:
    """Todos los atributos de producto que el mensaje dice explícitamente."""
    tokens = texto_normalizado.split()
    atributos = {a for t in tokens if (a := _atributo_de(t))}
    atributos |= {f"{c}l" for j in range(len(tokens)) if (c := _capacidad_en(tokens, j)) is not None}
    for mencion in _menciones_productos(texto_normalizado):
        atributos |= mencion["atributos"]
    return atributos


def _candidatos(familia: str, atributos: set[str], catalogo: list[dict]) -> list[dict]:
    return [
        producto
        for producto in catalogo
        if _familia(producto["nombre"]) == familia and set(atributos) <= _atributos(producto["nombre"])
    ]


def _opciones(productos: list[dict]) -> list[dict]:
    return [{"nombre": p["nombre"], "precio": float(p["precio"])} for p in productos]


def _descripcion_mencion(mencion: dict) -> str:
    if mencion["familia"] == "Bidón" and mencion.get("capacidad"):
        return f"bidones de {mencion['capacidad']}L"
    return " ".join([mencion["familia"].lower(), *sorted(mencion["atributos"])])


def _sugerencias(texto: str, familia: str | None, catalogo: list[dict]) -> list[dict]:
    """Productos que podrían ser lo que el cliente quiso decir: los de la
    misma familia, o los de nombre parecido."""
    if familia:
        return _opciones([p for p in catalogo if _familia(p["nombre"]) == familia])
    nombres = get_close_matches(texto, [p["nombre"] for p in catalogo], n=3, cutoff=0.4)
    return _opciones([p for p in catalogo if p["nombre"] in nombres])


def _sumar_linea(productos: list[dict], nombre: str, cantidad: int) -> None:
    for item in productos:
        if item.get("nombre_producto") == nombre:
            item["cantidad"] = _cantidad_valida(item.get("cantidad")) + cantidad
            return
    productos.append({"nombre_producto": nombre, "cantidad": cantidad})


# En un pedido de cambio, palabras que indican quitar, sumar o reemplazar, no
# fijar una cantidad: "quita un bidón", "mejor agrega 2 más", "cambia 2
# bidones por un dispensador". Esos los sigue resolviendo el LLM.
_PATRON_NO_FIJAR = re.compile(
    r"\b(quit\w*|saca\w*|elimin\w*|borr\w*|sin|ya no|no quiero|menos|mas|agreg\w*|sum\w*"
    r"|otr[oa]s?|por(?! favor)|reemplaz\w*|en vez|en lugar)\b"
)


def _cantidades_mensaje(texto_normalizado: str) -> list[int]:
    """Cantidades que trae el mensaje ("2", "dos"). No cuentan las
    capacidades ("de 12", "20 litros") ni los números negados ("no 3")."""
    tokens = texto_normalizado.split()
    return [
        int(t) if t.isdigit() else _NUMEROS_TEXTO[t]
        for j, t in enumerate(tokens)
        if (t.isdigit() or t in _NUMEROS_TEXTO)
        and _capacidad_en(tokens, j) is None
        and not (j > 0 and tokens[j - 1] in _NEGACIONES)
    ]


def _cambio_cantidad_simple(productos: list[dict], texto_normalizado: str) -> tuple[int, int] | None:
    """(índice de la línea, cantidad nueva) si el mensaje es un cambio de
    cantidad simple que se puede resolver sin el LLM: "mejor que sean 2
    bidones, no 3", "que sean dos bidones", "mejor 2 dispensadores" (bug del
    2026-10-07: el LLM no devolvió el "fijar" y el cambio se perdió en
    silencio). Debe traer un solo número (no cuentan los negados, "no 3", ni
    una capacidad, "de 12"), mencionar un solo tipo de producto, y ese tipo
    debe tener exactamente una línea en el pedido. Si algo es ambiguo (dos
    líneas de bidones, un atributo distinto al de la línea, quitar o sumar),
    devuelve None y no se adivina. Con varias líneas de ese tipo, vale si los
    atributos que dice el mensaje identifican una sola ("mejor 2 de 20" con
    3x 20L y 1x 12L cambia solo la de 20L)."""
    if not _PATRON_MODIFICACION.search(texto_normalizado) or _PATRON_NO_FIJAR.search(texto_normalizado):
        return None
    cantidades = _cantidades_mensaje(texto_normalizado)
    tipos = [tipo for tipo in _PATRONES_TIPO_PRODUCTO if _menciona_tipo(tipo, texto_normalizado)]
    if len(cantidades) != 1 or cantidades[0] < 1 or len(tipos) != 1:
        return None
    lineas = [i for i, p in enumerate(productos) if _tipo_producto(p.get("nombre_producto") or "") == tipos[0]]
    # "mejor 2 bidones de 20" con la línea de 12L cambia la capacidad, no
    # solo la cantidad.
    atributos = _atributos_mensaje(texto_normalizado)
    if len(lineas) > 1 and atributos:
        lineas = [i for i in lineas if atributos <= _atributos(productos[i]["nombre_producto"])]
    if len(lineas) != 1:
        return None
    if not atributos <= _atributos(productos[lineas[0]]["nombre_producto"]):
        return None
    return lineas[0], cantidades[0]


def _fusionar_productos(
    previos: list[dict],
    extraidos: list[dict],
    mensaje: str | None,
    excluir_familias: frozenset[str] = frozenset(),
) -> tuple[list[dict], set[str]]:
    """Fusiona lo que extrajo el LLM en este turno con los productos del
    draft. Devuelve (productos, familias que el cliente cambió o quitó
    explícitamente).

    - "agregar" (por defecto) suma: misma línea (producto y variante) suma
      cantidad, producto distinto agrega una línea.
    - "fijar"/"quitar" solo se aplican si el mensaje pide explícitamente un
      cambio (_PATRON_MODIFICACION): nunca se reemplaza ni borra una línea
      porque el LLM devolvió una lista distinta (bug del 2026-10-03: "si, un
      dispensador usb" dejó el pedido solo con el dispensador).
    - Un ítem "agregar" de un tipo de producto que el mensaje no menciona se
      descarta: es el LLM repitiendo productos del contexto (ej. responde
      "sí" a la dirección y devuelve los bidones de nuevo), y sumarlo los
      duplicaría.
    - Los ítems ya validados por _validar_extraidos (catálogo y atributos);
      las cantidades < 1 se descartan.
    """
    texto = _normalizar_texto(mensaje)
    modificacion_explicita = bool(_PATRON_MODIFICACION.search(texto))
    productos = [dict(item) for item in previos]
    familias_modificadas: set[str] = set()

    for item in extraidos:
        nombre = item.get("nombre_producto")
        if _familia(nombre) in excluir_familias:
            continue
        operacion = item.get("operacion") or "agregar"
        indice = next(
            (i for i, p in enumerate(productos) if p.get("nombre_producto") == nombre), None
        )

        if operacion in ("fijar", "quitar") and not modificacion_explicita:
            if operacion == "quitar" or indice is not None:
                logger.info("[order_flow] '%s' de %s ignorado: el cliente no pidió un cambio", operacion, nombre)
                continue
            operacion = "agregar"

        if operacion == "agregar" and not _menciona_tipo(_tipo_producto(nombre), texto):
            logger.info("[order_flow] %s descartado: el mensaje no menciona ese producto (eco del contexto)", nombre)
            continue

        if operacion == "quitar":
            if indice is None:
                # "quita el dispensador" cuando el LLM eligió el otro modelo:
                # si hay una sola línea de ese tipo, es esa.
                del_tipo = [
                    i for i, p in enumerate(productos)
                    if _tipo_producto(p.get("nombre_producto") or "") == _tipo_producto(nombre)
                ]
                indice = del_tipo[0] if len(del_tipo) == 1 else None
            if indice is not None:
                familias_modificadas.add(_familia(productos[indice]["nombre_producto"]))
                productos.pop(indice)
            continue

        cantidad = _cantidad_valida(item.get("cantidad"))
        if cantidad < 1:
            continue
        if operacion == "fijar":
            familias_modificadas.add(_familia(nombre))
            if indice is not None:
                productos[indice] = {**productos[indice], "cantidad": cantidad}
                continue
        _sumar_linea(productos, nombre, cantidad)

    return productos, familias_modificadas


def _validar_extraidos(
    extraidos: list[dict], mensaje: str | None, productos_previos: list[dict], catalogo: list[dict]
) -> tuple[list[dict], list[str]]:
    """Separa lo que extrajo el LLM en (ítems aceptados, nombres que no
    existen en el catálogo). Un ítem "agregar" cuyo producto tiene atributos
    que el cliente NO dijo (ej. "Bidón 12L Recarga" cuando dijo "5 bidones",
    o "Dispensador USB" cuando dijo "dispensador") es una suposición del LLM
    y se rechaza: la mención queda pendiente en _resolver_menciones y se le
    pregunta. Al pedir un cambio explícito ("cambia los nuevos por recarga")
    se aceptan también los atributos de las líneas que ya están en el
    pedido."""
    nombres_catalogo = {p["nombre"] for p in catalogo}
    texto = _normalizar_texto(mensaje)
    dichos = _atributos_mensaje(texto)
    if _PATRON_MODIFICACION.search(texto):
        for item in productos_previos:
            dichos |= _atributos(item.get("nombre_producto") or "")

    aceptados, no_encontrados = [], []
    for item in extraidos:
        nombre = item.get("nombre_producto")
        if nombre not in nombres_catalogo:
            logger.warning("[order_flow] Producto fuera del catálogo: %r", nombre)
            no_encontrados.append(str(nombre))
            continue
        if (item.get("operacion") or "agregar") == "agregar" and not _atributos(nombre) <= dichos:
            logger.info(
                "[order_flow] %s rechazado: el cliente no dijo %s",
                nombre,
                sorted(_atributos(nombre) - dichos),
            )
            continue
        aceptados.append(item)
    return aceptados, no_encontrados


def _capacidad_respuesta(texto_normalizado: str) -> int | None:
    """Capacidad que da el cliente al preguntarle "¿de 12L o de 20L?": "de
    20", "20 litros", "20l" o, en una respuesta corta, "20" a secas."""
    tokens = texto_normalizado.split()
    for j in range(len(tokens)):
        capacidad = _capacidad_en(tokens, j)
        if capacidad is not None:
            return capacidad
    if len(tokens) <= 3:
        palabras = {"doce": 12, "veinte": 20}
        for token in tokens:
            if token.isdigit() and int(token) in _CAPACIDADES:
                return int(token)
            if token in palabras:
                return palabras[token]
    return None


def _resolver_aclaracion_bidon(aclaracion: dict | None, mensaje: str | None) -> dict | None:
    """Resuelve en código lo que falta de un bidón pendiente, sin depender
    del LLM: primero la capacidad ("¿de 12L o de 20L?") y después la variante
    ("¿nuevo o recarga?"). Devuelve {"aclaracion": lo que sigue pendiente o
    None, "lineas": líneas a agregar, "no_encontrado": capacidad pedida que
    no existe o None}, o None si el mensaje no responde nada de eso.

    - "20" / "de 20 litros": fija la capacidad.
    - "recarga" / "los quiero nuevos": toda la cantidad pendiente a esa
      variante (cubre también el error del LLM con "Quiero 2 bidones de 20
      litros recarga", que dejaba el ítem pendiente pese al "recarga").
    - "2 recargas y 2 nuevos": una línea por variante.
    - "1 nuevo y el resto recarga": la variante sin número se lleva el resto.
    - "2 recargas" de 4 pendientes: quedan 2 pendientes y se vuelve a
      preguntar por ellos.
    - Si todavía no hay capacidad, el reparto por variante ("3 recarga y 2
      nuevos") se guarda y se aplica cuando el cliente la diga.
    - "¿nuevo o recarga?" (ambas sin número) no resuelve nada.
    - "1 de 12 nuevo" en el mismo mensaje es otro bidón, ya completo: se
      agrega como línea aparte; "1 de 12" (sin variante) queda pendiente.
    - Sin capacidad pendiente, "3 de 20 y 2 de 12" para 5 bidones reparte
      los 5 en esas dos capacidades.
    - Varios bidones pendientes de distinta capacidad (lista): ver
      _resolver_aclaraciones_bidon.
    """
    aclaraciones = _lista_aclaraciones(aclaracion)
    if len(aclaraciones) > 1:
        return _resolver_aclaraciones_bidon(aclaraciones, mensaje)
    if not aclaraciones:
        return None
    aclaracion = aclaraciones[0]
    capacidad = aclaracion.get("capacidad_litros")
    pendiente = _cantidad_valida(aclaracion.get("cantidad"))
    if pendiente < 1:
        return None
    texto = _normalizar_texto(mensaje)
    respondio = False

    if capacidad is None:
        reparto = _repartir_por_capacidad(pendiente, texto)
        if reparto is not None:
            return reparto
        capacidad_dicha = _capacidad_respuesta(texto)
        if capacidad_dicha is not None and capacidad_dicha not in _CAPACIDADES:
            return {"aclaracion": aclaracion, "lineas": [], "no_encontrado": f"bidones de {capacidad_dicha}L"}
        if capacidad_dicha is not None:
            capacidad = capacidad_dicha
            respondio = True
    elif capacidad not in _CAPACIDADES:
        return None

    cantidades = _cantidades_por_variante(texto, capacidad)
    if cantidades:
        respondio = True
    else:
        cantidades = dict(aclaracion.get("variantes") or {})
    if not respondio:
        return None

    sin_cantidad = [variante for variante, n in cantidades.items() if n is None]
    if len(sin_cantidad) > 1:
        cantidades = {}
    elif sin_cantidad:
        resto = pendiente - sum(n for n in cantidades.values() if n is not None)
        if resto < 1:
            cantidades = {}
        else:
            cantidades[sin_cantidad[0]] = resto

    if capacidad is None:
        nueva = {**aclaracion, "variantes": cantidades} if cantidades else aclaracion
        return {"aclaracion": nueva, "lineas": [], "no_encontrado": None}

    lineas = [
        {"nombre_producto": f"Bidón {capacidad}L {variante}", "cantidad": n}
        for variante, n in cantidades.items()
        if n > 0
    ]
    restante = pendiente - sum(item["cantidad"] for item in lineas)
    nueva = {"capacidad_litros": capacidad, "cantidad": restante} if restante > 0 else None
    # Bidones de otra capacidad en el mismo mensaje ("2 de 20 recarga y 1 de
    # 12 nuevo"): se agregan aquí también, para no depender de que el LLM los
    # haya extraído; sin variante quedan pendientes.
    otras, pendientes = _lineas_otras_capacidades(texto, {capacidad})
    return {
        "aclaracion": _empaquetar_aclaraciones([nueva, *pendientes]),
        "lineas": lineas + otras,
        "no_encontrado": None,
    }


def _lineas_otras_capacidades(texto_normalizado: str, capacidades: set) -> tuple[list[dict], list[dict]]:
    """Líneas de bidón del mensaje de capacidades del catálogo distintas a
    `capacidades` (las que se están aclarando), con cantidad: (líneas
    completas, bidones sin variante que quedan pendientes)."""
    completas, pendientes = [], []
    for linea in _lineas_bidon(texto_normalizado, desglose=False)[0]:
        capacidad, cantidad = linea["capacidad"], linea["cantidad"]
        if capacidad in capacidades or capacidad not in _CAPACIDADES or not cantidad:
            continue
        if linea["variante"]:
            completas.append({"nombre_producto": f"Bidón {capacidad}L {linea['variante']}", "cantidad": cantidad})
        else:
            pendientes.append({"capacidad_litros": capacidad, "cantidad": cantidad})
    return completas, pendientes


def _repartir_por_capacidad(pendiente: int, texto_normalizado: str) -> dict | None:
    """Respuesta a "¿de 12L o de 20L?" que reparte los bidones pendientes en
    varias capacidades ("3 de 20 y 2 de 12" para 5 bidones). Solo si las
    cantidades suman exactamente lo pendiente; si no, None y se resuelve como
    una sola capacidad."""
    lineas = [linea for linea in _lineas_bidon(texto_normalizado)[0] if linea["cantidad"]]
    if (
        len({linea["capacidad"] for linea in lineas}) < 2
        or any(linea["capacidad"] not in _CAPACIDADES for linea in lineas)
        or sum(linea["cantidad"] for linea in lineas) != pendiente
    ):
        return None
    completas, pendientes = _lineas_otras_capacidades(texto_normalizado, set())
    return {"aclaracion": _empaquetar_aclaraciones(pendientes), "lineas": completas, "no_encontrado": None}


def _resolver_aclaraciones_bidon(aclaraciones: list[dict], mensaje: str | None) -> dict | None:
    """Como _resolver_aclaracion_bidon, con varios bidones pendientes de
    distinta capacidad ("3 de 20 y 1 de 12"), preguntados juntos.

    - "las de 20 recarga y la de 12 nuevo": cada variante a su capacidad.
    - "todos recarga" / "recarga": una variante sin capacidad ni cantidad
      va a todos.
    - "2 recarga y 1 nuevo" (sin capacidad): solo si calza con un único
      pendiente (el de 3 unidades); si no, no se adivina y se vuelve a
      preguntar.
    - El pendiente sin capacidad, si lo hay, solo se resuelve con la
      capacidad ("de 20"); mientras tanto no se le asigna variante.
    """
    texto = _normalizar_texto(mensaje)
    menciones = _menciones_bidon(texto)
    sin_capacidad = [m for m in menciones if m[2] is None]
    conocidas = [a for a in aclaraciones if a.get("capacidad_litros") in _CAPACIDADES]
    otras = [a for a in aclaraciones if a not in conocidas]

    destino: list[dict] = []
    if sin_capacidad and not otras:
        numeradas = [n for _, n, _ in sin_capacidad if n is not None]
        if not numeradas:
            destino = conocidas
        else:
            total = sum(numeradas)
            con_resto = len(numeradas) < len(sin_capacidad)
            candidatas = [
                a for a in conocidas
                if (_cantidad_valida(a.get("cantidad")) > total if con_resto else _cantidad_valida(a.get("cantidad")) == total)
            ]
            destino = candidatas if len(candidatas) == 1 else []

    lineas: list[dict] = []
    pendientes: list[dict] = []
    respondio = False
    for aclaracion in conocidas:
        capacidad = aclaracion["capacidad_litros"]
        pendiente = _cantidad_valida(aclaracion.get("cantidad"))
        propias = [m for m in menciones if m[2] == capacidad]
        if aclaracion in destino:
            propias += sin_capacidad
        cantidades = _agrupar_variantes(propias)
        sin_cantidad = [variante for variante, n in cantidades.items() if n is None]
        if len(sin_cantidad) > 1:
            cantidades = {}
        elif sin_cantidad:
            resto = pendiente - sum(n for n in cantidades.values() if n is not None)
            cantidades = {**cantidades, sin_cantidad[0]: resto} if resto > 0 else {}
        nuevas = [
            {"nombre_producto": f"Bidón {capacidad}L {variante}", "cantidad": n}
            for variante, n in cantidades.items()
            if n > 0
        ]
        if not nuevas:
            pendientes.append(aclaracion)
            continue
        respondio = True
        lineas += nuevas
        restante = pendiente - sum(item["cantidad"] for item in nuevas)
        if restante > 0:
            pendientes.append({"capacidad_litros": capacidad, "cantidad": restante})

    capacidad_dicha = _capacidad_respuesta(texto) if not menciones else None
    for aclaracion in otras:
        if capacidad_dicha in _CAPACIDADES:
            pendientes.append({**aclaracion, "capacidad_litros": capacidad_dicha})
            respondio = True
        else:
            pendientes.append(aclaracion)

    completas, nuevas_pendientes = _lineas_otras_capacidades(
        texto, {a.get("capacidad_litros") for a in aclaraciones}
    )
    if completas or nuevas_pendientes:
        respondio = True
    if not respondio:
        return None
    return {
        "aclaracion": _empaquetar_aclaraciones(pendientes + nuevas_pendientes),
        "lineas": lineas + completas,
        "no_encontrado": None,
    }


def _resolver_pendientes_modelo(
    pendientes: list[dict], mensaje: str | None, catalogo: list[dict]
) -> tuple[list[dict], list[dict], set[str]]:
    """Resuelve en código los productos pendientes de modelo ("¿qué
    dispensador?") con los atributos que dice el mensaje ("el usb"). Devuelve
    (pendientes que siguen, líneas a agregar, familias resueltas)."""
    dichos = _atributos_mensaje(_normalizar_texto(mensaje))
    restantes, lineas, familias = [], [], set()
    for pendiente in pendientes:
        atributos = set(pendiente.get("atributos") or []) | {
            a for a in dichos if any(a in _atributos(o["nombre"]) for o in pendiente.get("opciones") or [])
        }
        candidatos = _candidatos(pendiente["familia"], atributos, catalogo)
        if len(candidatos) == 1:
            lineas.append({"nombre_producto": candidatos[0]["nombre"], "cantidad": pendiente["cantidad"]})
            familias.add(pendiente["familia"])
        else:
            restantes.append(pendiente)
    return restantes, lineas, familias


def _resolver_menciones(
    menciones: list[dict], aceptados: list[dict], catalogo: list[dict]
) -> tuple[list[dict], dict | None, list[dict], list[dict]]:
    """Revisa cada producto que el cliente mencionó y que el LLM no extrajo
    (bien): si calza con un solo producto del catálogo se agrega; si calza
    con varios queda pendiente; si no calza con ninguno se avisa. Así nada
    de lo que el cliente dijo se descarta en silencio. Devuelve (líneas a
    agregar, bidones pendientes, pendientes de modelo, no encontrados). Los
    bidones pendientes van uno por capacidad (ver _empaquetar_aclaraciones):
    "3 de 20 y 1 de 12" deja pendientes los de 20L y el de 12L por
    separado."""
    lineas: list[dict] = []
    pendientes: list[dict] = []
    no_encontrados: list[dict] = []
    bidones: list[dict] = []

    for mencion in menciones:
        cubierta = any(
            _familia(item["nombre_producto"]) == mencion["familia"]
            and mencion["atributos"] <= _atributos(item["nombre_producto"])
            for item in aceptados
        )
        if cubierta:
            continue
        cantidad = mencion["cantidad"]
        candidatos = _candidatos(mencion["familia"], mencion["atributos"], catalogo)
        if len(candidatos) == 1:
            lineas.append({"nombre_producto": candidatos[0]["nombre"], "cantidad": cantidad or 1})
            continue
        if mencion["familia"] == "Bidón":
            capacidad = mencion.get("capacidad")
            if not candidatos:
                # Capacidad que no existe ("de 5 litros"): se avisa y se
                # pregunta la capacidad como si no la hubiera dicho.
                no_encontrados.append(
                    {"texto": _descripcion_mencion(mencion), "opciones": []}
                )
                capacidad = None
            bidon = {"capacidad_litros": capacidad, "cantidad": cantidad or 1}
            if mencion.get("variante"):
                bidon["variantes"] = {mencion["variante"]: cantidad or 1}
            bidones.append(bidon)
            continue
        if not candidatos:
            no_encontrados.append(
                {"texto": _descripcion_mencion(mencion), "opciones": _sugerencias("", mencion["familia"], catalogo)}
            )
            continue
        pendientes.append(
            {
                "familia": mencion["familia"],
                "cantidad": cantidad or 1,
                "atributos": sorted(mencion["atributos"]),
                "opciones": _opciones(candidatos),
            }
        )
    return lineas, _empaquetar_aclaraciones(bidones), pendientes, no_encontrados


# --------------------------------------------------------------------------
# Bidones pendientes de aclarar (draft["aclaracion_pendiente"])
#
# Un solo bidón pendiente se guarda como siempre: {"capacidad_litros",
# "cantidad", "variantes"?}. Varios de distinta capacidad ("3 de 20 y 1 de
# 12", ninguno con variante) se guardan como lista, uno por capacidad, y se
# preguntan juntos (ver _pregunta_pendiente_producto y
# _resolver_aclaraciones_bidon). Antes se fusionaban en uno solo con
# capacidad None y se perdía qué capacidad era cada uno.
# --------------------------------------------------------------------------


def _lista_aclaraciones(valor) -> list[dict]:
    if not valor:
        return []
    if isinstance(valor, dict):
        return [valor]
    return [aclaracion for aclaracion in valor if aclaracion]


def _empaquetar_aclaraciones(aclaraciones: list[dict | None]) -> dict | list[dict] | None:
    """Junta los bidones pendientes por capacidad (misma capacidad suma
    cantidades y variantes) y los deja en el formato del draft: None, un dict
    si queda uno, o una lista si quedan varios."""
    por_capacidad: dict = {}
    for aclaracion in aclaraciones:
        cantidad = _cantidad_valida((aclaracion or {}).get("cantidad"))
        if not aclaracion or cantidad < 1:
            continue
        capacidad = aclaracion.get("capacidad_litros")
        junta = por_capacidad.get(capacidad)
        if junta is None:
            junta = {**aclaracion, "cantidad": cantidad}
            if aclaracion.get("variantes"):
                junta["variantes"] = dict(aclaracion["variantes"])
            por_capacidad[capacidad] = junta
            continue
        junta["cantidad"] += cantidad
        variantes = dict(junta.get("variantes") or {})
        for variante, n in (aclaracion.get("variantes") or {}).items():
            variantes[variante] = (variantes.get(variante) or 0) + (n or 0)
        if variantes:
            junta["variantes"] = variantes
    juntas = list(por_capacidad.values())
    if not juntas:
        return None
    return juntas[0] if len(juntas) == 1 else juntas


def _sumar_aclaraciones(actual, nueva):
    """Junta los bidones pendientes de aclarar: misma capacidad suma
    cantidades; capacidades distintas quedan como pendientes separados."""
    return _empaquetar_aclaraciones(_lista_aclaraciones(actual) + _lista_aclaraciones(nueva))


def _clave_capacidad(capacidad: int) -> str:
    return f"Bidón {capacidad}L"


def _es_clave_capacidad(clave: str) -> bool:
    return re.fullmatch(r"Bidón \d+L", clave) is not None


def _unidades_por_familia(
    productos: list[dict], aclaracion, pendientes_modelo: list[dict] | None = None
) -> dict[str, int]:
    """Unidades por familia del draft y, en los bidones, también por
    capacidad ("Bidón 20L", la clave de _categoria), contando los productos
    que esperan una aclaración (capacidad, variante o modelo). Un bidón
    pendiente sin capacidad solo cuenta en "Bidón"."""
    unidades: dict[str, int] = {}

    def sumar(clave: str, cantidad: int) -> None:
        unidades[clave] = unidades.get(clave, 0) + cantidad

    for item in productos:
        nombre = item.get("nombre_producto") or ""
        cantidad = _cantidad_valida(item.get("cantidad"))
        sumar(_familia(nombre), cantidad)
        if _es_clave_capacidad(_categoria(nombre)):
            sumar(_categoria(nombre), cantidad)
    for pendiente in _lista_aclaraciones(aclaracion):
        cantidad = _cantidad_valida(pendiente.get("cantidad"))
        sumar("Bidón", cantidad)
        if pendiente.get("capacidad_litros"):
            sumar(_clave_capacidad(pendiente["capacidad_litros"]), cantidad)
    for pendiente in pendientes_modelo or []:
        familia = pendiente.get("familia")
        unidades[familia] = unidades.get(familia, 0) + _cantidad_valida(pendiente.get("cantidad"))
    return unidades


def _unidades_draft(draft: dict) -> dict[str, int]:
    return _unidades_por_familia(
        draft.get("productos") or [], draft.get("aclaracion_pendiente"), draft.get("pendientes_modelo")
    )


def _actualizar_unidades_pedidas(
    draft_previo: dict, draft_nuevo: dict, familias_modificadas: set[str]
) -> dict[str, int]:
    """Registro, por familia y por capacidad de bidón (ver
    _unidades_por_familia), de cuántas unidades ha pedido el cliente en la
    conversación. Es independiente de cómo se fusionan las líneas: solo sube
    cuando el draft crece (incluidos los productos pendientes de aclarar), y
    solo baja cuando el cliente cambia o quita algo explícitamente de esa
    familia. Si el draft termina con menos unidades que este registro, algo
    se perdió en el camino y no se muestra el resumen (ver
    _productos_no_registrados)."""
    antes = _unidades_draft(draft_previo)
    pedidas = dict(draft_previo["unidades_pedidas"]) if "unidades_pedidas" in draft_previo else dict(antes)
    despues = _unidades_draft(draft_nuevo)
    for clave in antes.keys() | despues.keys():
        if _familia(clave) in familias_modificadas:
            pedidas[clave] = despues.get(clave, 0)
        else:
            pedidas[clave] = pedidas.get(clave, 0) + max(0, despues.get(clave, 0) - antes.get(clave, 0))
    return {clave: n for clave, n in pedidas.items() if n > 0}


def _productos_no_registrados(draft: dict) -> dict[str, int]:
    """Unidades que el cliente pidió en la conversación y que no están en el
    draft (ver _actualizar_unidades_pedidas), por familia y por capacidad de
    bidón: "3 de 20 y 1 de 12" que termina con 4 bidones de 20L cuadra en el
    total pero pierde el de 12L ({"Bidón 12L": 1}). Los bidones pendientes
    sin capacidad pueden cubrir lo que falta de cualquier capacidad."""
    actuales = _unidades_draft(draft)
    pedidas = draft.get("unidades_pedidas") or {}
    faltan = {
        clave: n - actuales.get(clave, 0)
        for clave, n in pedidas.items()
        if not _es_clave_capacidad(clave) and n > actuales.get(clave, 0)
    }
    por_capacidad = {
        clave: n - actuales.get(clave, 0)
        for clave, n in pedidas.items()
        if _es_clave_capacidad(clave) and n > actuales.get(clave, 0)
    }
    sin_capacidad = actuales.get("Bidón", 0) - sum(n for clave, n in actuales.items() if _es_clave_capacidad(clave))
    if sum(por_capacidad.values()) > sin_capacidad:
        # Se informa por capacidad; en "Bidón" queda solo lo que falte aparte.
        resto = faltan.pop("Bidón", 0) - sum(por_capacidad.values())
        faltan.update(por_capacidad)
        if resto > 0:
            faltan["Bidón"] = resto
    return faltan


# --------------------------------------------------------------------------
# Corrección propuesta por el bot ("¿Quieres que los cambie todos a 20L?")
#
# Cuando el bot ofrece corregir algo del pedido, el LLM devuelve también la
# corrección como dato (correccion_propuesta). El código la valida contra el
# catálogo y la guarda en el draft; si el cliente responde "sí" al mensaje
# siguiente, se aplica y se muestra el resumen nuevo (nunca se confirma el
# pedido en el mismo turno). Cualquier otro mensaje la descarta.
# --------------------------------------------------------------------------

# Atributos que se reemplazan según lo que cambia la corrección.
_DIMENSIONES_CORRECCION = {
    "capacidad": lambda atributo: re.fullmatch(r"\d+l", atributo) is not None,
    "variante": lambda atributo: atributo in ("nuevo", "recarga"),
    "modelo": lambda atributo: atributo in ("usb", "basico"),
}


def _valor_atributo(atributo: str, valor) -> str | None:
    """Valor nuevo de la corrección como atributo de catálogo: "20" / "20L"
    → "20l"; "Nuevos" → "nuevo"; "USB" → "usb"."""
    texto = _normalizar_texto(str(valor if valor is not None else ""))
    if atributo == "capacidad":
        numero = re.fullmatch(r"(\d+)\s*(l|lt|lts|litros?)?", texto)
        return f"{numero.group(1)}l" if numero else None
    return _atributo_de(texto) if texto else None


def _validar_correccion(propuesta: dict | None, productos: list[dict], catalogo: list[dict]) -> dict | None:
    """Corrección propuesta por el LLM, validada contra el draft y el
    catálogo de la BD: cada línea afectada debe estar en el pedido y el
    producto resultante debe existir (exactamente uno). Devuelve {"cambios":
    [{"desde", "hacia", "cantidad": unidades a cambiar o None = todas}],
    "pide_cantidad": bool} o None si no es válida (y entonces no se ofrece)."""
    if not isinstance(propuesta, dict):
        return None
    atributo = propuesta.get("atributo")
    actuales = propuesta.get("productos_actuales") or []
    alcance = propuesta.get("alcance") or "todas"
    en_pedido = {p.get("nombre_producto"): _cantidad_valida(p.get("cantidad")) for p in productos}
    if not isinstance(actuales, list) or not actuales or any(a not in en_pedido for a in actuales):
        return None

    if atributo == "cantidad":
        nueva = _cantidad_valida(propuesta.get("valor_nuevo"))
        if len(actuales) != 1 or nueva < 1 or nueva == en_pedido[actuales[0]]:
            return None
        return {"cambios": [{"desde": actuales[0], "hacia": actuales[0], "cantidad_nueva": nueva}], "pide_cantidad": False}

    es_de_la_dimension = _DIMENSIONES_CORRECCION.get(atributo)
    valor = _valor_atributo(atributo, propuesta.get("valor_nuevo")) if es_de_la_dimension else None
    if not valor:
        return None
    cambios = []
    for actual in actuales:
        atributos = {a for a in _atributos(actual) if not es_de_la_dimension(a)} | {valor}
        candidatos = [
            p["nombre"] for p in catalogo
            if _familia(p["nombre"]) == _familia(actual) and set(_atributos(p["nombre"])) == atributos
        ]
        if len(candidatos) != 1 or candidatos[0] == actual:
            return None
        cambios.append({"desde": actual, "hacia": candidatos[0], "cantidad": None})

    if alcance == "algunas":
        return {"cambios": cambios, "pide_cantidad": True}
    if alcance != "todas":
        unidades = _cantidad_valida(alcance)
        if len(cambios) != 1 or not 1 <= unidades <= en_pedido[cambios[0]["desde"]]:
            return None
        cambios[0]["cantidad"] = unidades
    return {"cambios": cambios, "pide_cantidad": False}


def _cambio_atributo_simple(productos: list[dict], texto_normalizado: str, catalogo: list[dict]) -> dict | None:
    """Corrección validada (formato de _validar_correccion) si el mensaje
    cambia el modelo, la variante o la capacidad de un producto del pedido y
    se puede resolver sin el LLM: "mejor el dispensador básico", "cambia el
    bidón a nuevo", "mejor de 12 litros el bidón" (bug del 2026-10-07: "quiero
    el dispensador básico" repitió el resumen sin aviso). Debe mencionar un
    solo tipo de producto con exactamente una línea en el pedido, y cambiar
    una sola dimensión, con un solo valor. Una cantidad distinta de la de la
    línea no se combina aquí (la resuelven _cambio_cantidad_simple o el LLM);
    "un dispensador" con 1 en el pedido no es una cantidad nueva. Con "solo"
    o "sin" ("solo el dispensador básico", "sin el bidón, el dispensador
    básico") el cliente puede querer quitar otras líneas, y este helper solo
    cambia un atributo manteniendo el resto: no se adivina. Si algo es
    ambiguo o el producto resultante no existe (o es el mismo), devuelve None
    y lo resuelve el LLM o el aviso de cambio no aplicado."""
    if re.search(r"\b(solo|sin)\b", texto_normalizado):
        return None
    tipos = [tipo for tipo in _PATRONES_TIPO_PRODUCTO if _menciona_tipo(tipo, texto_normalizado)]
    if len(tipos) != 1:
        return None
    lineas = [p for p in productos if _tipo_producto(p.get("nombre_producto") or "") == tipos[0]]
    if len(lineas) != 1:
        return None
    nombre = lineas[0]["nombre_producto"]
    if any(n != _cantidad_valida(lineas[0].get("cantidad")) for n in _cantidades_mensaje(texto_normalizado)):
        return None
    actuales = _atributos(nombre)
    dichos = _atributos_mensaje(texto_normalizado)
    cambios = []
    for atributo, es_de_la_dimension in _DIMENSIONES_CORRECCION.items():
        valores = {a for a in dichos if es_de_la_dimension(a)}
        if len(valores) > 1:
            return None  # "el usb o el básico"
        if valores and not valores <= actuales:
            cambios.append((atributo, valores.pop()))
    if len(cambios) != 1:
        return None
    atributo, valor = cambios[0]
    propuesta = {"productos_actuales": [nombre], "atributo": atributo, "valor_nuevo": valor, "alcance": "todas"}
    return _validar_correccion(propuesta, productos, catalogo)


def _pregunta_correccion(correccion: dict, productos: list[dict]) -> str:
    en_pedido = {p.get("nombre_producto"): _cantidad_valida(p.get("cantidad")) for p in productos}
    cambios = correccion["cambios"]
    if "cantidad_nueva" in cambios[0]:
        cambio = cambios[0]
        return f"¿Quieres que deje {cambio['cantidad_nueva']}x {cambio['desde']}?"
    if correccion["pide_cantidad"]:
        opciones = ", ".join(f"{en_pedido[c['desde']]}x {c['desde']} → {c['hacia']}" for c in cambios)
        return f"¿Cuántas unidades quieres cambiar? Tienes: {opciones}. Dime cuántas, o responde TODAS."
    detalle = " y ".join(
        f"{c['cantidad'] or en_pedido[c['desde']]}x {c['desde']} por {c['hacia']}" for c in cambios
    )
    return f"¿Quieres que cambie {detalle}?"


def _aplicar_cambios_correccion(productos: list[dict], correccion: dict) -> list[dict]:
    resultado = [dict(item) for item in productos]
    for cambio in correccion["cambios"]:
        indice = next((i for i, p in enumerate(resultado) if p.get("nombre_producto") == cambio["desde"]), None)
        if indice is None:
            continue
        actual = _cantidad_valida(resultado[indice].get("cantidad"))
        if "cantidad_nueva" in cambio:
            resultado[indice]["cantidad"] = cambio["cantidad_nueva"]
            continue
        unidades = min(cambio["cantidad"] or actual, actual)
        if unidades == actual:
            resultado.pop(indice)
        else:
            resultado[indice]["cantidad"] = actual - unidades
        _sumar_linea(resultado, cambio["hacia"], unidades)
    return resultado


def _unidades_respuesta(texto: str | None) -> int | str | None:
    """Respuesta a "¿cuántas unidades quieres cambiar?": "todas" o un número."""
    tokens = _normalizar_texto(texto).split()
    if any(t in ("todas", "todos", "toda", "todo") for t in tokens):
        return "todas"
    for token in tokens:
        if token.isdigit():
            return int(token)
        if token in _NUMEROS_TEXTO:
            return _NUMEROS_TEXTO[token]
    return None


def _sin_pregunta_final(texto: str | None) -> str:
    """Quita la última pregunta del texto del LLM (la reemplaza la pregunta
    armada en código desde la corrección validada)."""
    return re.sub(r"\s*¿[^¿]*\?\s*$", "", texto or "").strip()


async def _catalogo() -> list[dict]:
    """Catálogo vigente desde la BD: [{"nombre", "precio"}]."""
    async with SessionLocal() as session:
        result = await session.execute(select(Producto).order_by(Producto.id))
        return [{"nombre": p.nombre, "precio": p.precio_unitario} for p in result.scalars().all()]


def _resumen_productos_corto(productos: list[dict]) -> str:
    return ", ".join(
        f"{item.get('cantidad', 1)}x {item.get('nombre_producto', '')}" for item in productos
    )


def _menciona_direccion_o_ubicacion(texto: str | None) -> bool:
    if not texto:
        return False
    texto_normalizado = texto.lower()
    return any(frase in texto_normalizado for frase in _FRASES_REABREN_DIRECCION_UBICACION)


def _respuesta_es_de_producto(texto: str | None) -> bool:
    """True si el texto sugerido por el LLM pregunta/habla de productos (ej.
    "¿nuevo o recarga?", "¿Básico o USB?", capacidad de la promo) y no salta
    a otro paso (dirección, ubicación, nombre, "algo más" o el resumen)."""
    if not texto:
        return False
    texto_minusculas = texto.lower()
    if not any(palabra in texto_minusculas for palabra in _PALABRAS_PRODUCTO):
        return False
    if _menciona_direccion_o_ubicacion(texto_minusculas):
        return False
    return not any(
        frase in texto_minusculas
        for frase in ("algo más", "algo mas", "nombre", "confirmas el pedido", "resumen")
    )


def _promo_sin_capacidad(productos: list[dict], notas: str | None) -> bool:
    tiene_promo = any(
        (item.get("nombre_producto") or "").startswith("Promo") for item in productos
    )
    return tiene_promo and not re.search(r"(12|20)", notas or "")


def _datos_cliente(cliente: Cliente | None) -> dict | None:
    """Snapshot plano del cliente identificado por teléfono (sin objetos ORM
    en el draft ni entre sesiones)."""
    if cliente is None:
        return None
    return {
        "id": cliente.id,
        "nombre": cliente.nombre,
        "direccion": (cliente.direccion or "").strip() or None,
        "latitud": float(cliente.latitud) if cliente.latitud is not None else None,
        "longitud": float(cliente.longitud) if cliente.longitud is not None else None,
    }


def _datos_faltantes(draft: dict, cliente: dict | None) -> list[str]:
    """Lista ORDENADA de los datos que faltan para poder mostrar el resumen,
    según el draft y el tipo de cliente. El bot pregunta siempre solo por el
    primero (de a un paso).

    - Cliente existente: producto → confirmar dirección registrada (o, si no
      tiene, o si dijo que no, dirección nueva + ubicación) → "¿algo más?".
      Nunca se pide el nombre: ya está en la tabla cliente.
    - Cliente nuevo: producto → nombre → dirección + ubicación → "¿algo más?".
    """
    productos = draft.get("productos") or []
    faltantes = []

    # No se avanza a la dirección (ni a "¿algo más?" ni al resumen) mientras
    # quede un producto por aclarar (capacidad o variante de un bidón, modelo
    # de un dispensador o promo), algo que el cliente pidió y no existe en el
    # catálogo, o una línea inválida (fuera del catálogo, o con cantidad 0;
    # _validar_extraidos ya las filtra, esto es defensivo).
    if (
        not productos
        or draft.get("aclaracion_pendiente")
        or draft.get("pendientes_modelo")
        or draft.get("no_encontrados")
        or _promo_sin_capacidad(productos, draft.get("notas"))
        or any(
            item.get("nombre_producto") not in CATALOGO_NOMBRES
            or _cantidad_valida(item.get("cantidad")) < 1
            for item in productos
        )
    ):
        faltantes.append("producto")

    if cliente is None and not draft.get("nombre_cliente"):
        faltantes.append("nombre")

    usa_direccion_habitual = draft.get("usa_direccion_habitual")
    if cliente is not None and cliente.get("direccion") and usa_direccion_habitual is None:
        faltantes.append("confirmar_direccion")
    elif not usa_direccion_habitual:
        if not draft.get("direccion_texto"):
            faltantes.append("direccion")
        if draft.get("ubicacion") is None and not draft.get("ubicacion_rechazada"):
            faltantes.append("ubicacion")

    if not draft.get("algo_mas_respondido"):
        faltantes.append("algo_mas")

    return faltantes


def _contexto_desde_draft(draft: dict | None) -> dict | None:
    if draft is None:
        return None
    return {
        "intencion": draft.get("intencion"),
        # Con otro nombre que el campo "productos" de la respuesta, para que
        # el LLM no los repita: solo debe extraer los del mensaje actual.
        "productos_en_pedido": draft.get("productos", []),
        "aclaracion_pendiente": draft.get("aclaracion_pendiente"),
        "productos_sin_modelo": [
            {"familia": p["familia"], "cantidad": p["cantidad"]} for p in draft.get("pendientes_modelo") or []
        ],
        "usa_direccion_habitual": draft.get("usa_direccion_habitual"),
        "direccion_texto": draft.get("direccion_texto"),
        "notas": draft.get("notas"),
        "ubicacion_recibida": draft.get("ubicacion") is not None,
        "ubicacion_rechazada": bool(draft.get("ubicacion_rechazada")),
        "nombre_cliente": draft.get("nombre_cliente"),
        "algo_mas_preguntado": draft.get("paso") == "algo_mas",
        "pregunta_pendiente": draft.get("paso"),
        "resumen_mostrado": draft.get("estado") in (ESTADO_ESPERANDO_CONFIRMACION, ESTADO_ESPERANDO_MODIFICACION),
    }


async def _interpretar_con_debug(
    phone: str, message: str, es_cliente_nuevo: bool, context: dict | None
) -> dict:
    # Logging de debug para diagnosticar problemas de interpretación. Usa
    # logger.debug a propósito: no aparece con el nivel INFO por defecto, así
    # que no hace falta quitarlo; si se necesita volver a diagnosticar algo,
    # basta con subir temporalmente el nivel de logging a DEBUG.
    logger.debug("[DEBUG contexto] %s", json.dumps(context, ensure_ascii=False))
    resultado = await interpret_message(
        phone=phone,
        message=message,
        es_cliente_nuevo=es_cliente_nuevo,
        context=context,
    )
    logger.debug("[DEBUG resultado_llm] %s", json.dumps(resultado, ensure_ascii=False))
    return resultado


def _lista_opciones(opciones: list[dict]) -> str:
    return "\n".join(f"- {o['nombre']}: {_formatear_clp(o['precio'])}" for o in opciones)


def _pregunta_variantes_bidones(aclaraciones: list[dict]) -> str:
    """"¿Los 3 bidones de 20L y el bidón de 12L los quieres nuevos ... o de
    recarga ...?", con un ejemplo de respuesta por capacidad."""
    descripciones, ejemplos = [], []
    for aclaracion in aclaraciones:
        cantidad = _cantidad_valida(aclaracion.get("cantidad"))
        capacidad = aclaracion["capacidad_litros"]
        if cantidad == 1:
            descripciones.append(f"el bidón de {capacidad}L")
            ejemplos.append(f"el de {capacidad}L")
        else:
            descripciones.append(f"los {cantidad} bidones de {capacidad}L")
            ejemplos.append(f"los de {capacidad}L")
    bidones = f"{', '.join(descripciones[:-1])} y {descripciones[-1]}"
    return PREGUNTA_ACLARACION_BIDONES.format(
        bidones=bidones[0].upper() + bidones[1:],
        ejemplo=f"{ejemplos[0]} recarga y {ejemplos[1]} nuevo",
    )


def _pregunta_pendiente_producto(draft: dict) -> str | None:
    """Pregunta, armada en código, por lo primero que falta aclarar de los
    productos: algo que no existe en el catálogo, la capacidad de un bidón,
    su variante, o el modelo de un producto con varias opciones."""
    partes = []
    sin_opciones = False
    for no_encontrado in draft.get("no_encontrados") or []:
        texto = MENSAJE_NO_ENCONTRADO.format(producto=no_encontrado["texto"])
        if no_encontrado.get("opciones"):
            texto += f" ¿Te refieres a alguna de estas opciones?\n{_lista_opciones(no_encontrado['opciones'])}"
        else:
            sin_opciones = True
        partes.append(texto)

    # Bidones pendientes: primero la capacidad del que no la tiene; con varias
    # capacidades conocidas, la variante de todas en una sola pregunta.
    aclaraciones = _lista_aclaraciones(draft.get("aclaracion_pendiente"))
    sin_capacidad = next((a for a in aclaraciones if a.get("capacidad_litros") is None), None)
    aclaracion = sin_capacidad or (aclaraciones[0] if len(aclaraciones) == 1 else None)
    if aclaraciones and aclaracion is None:
        partes.append(_pregunta_variantes_bidones(aclaraciones))
    elif aclaracion and aclaracion.get("capacidad_litros") is None:
        cantidad = _cantidad_valida(aclaracion.get("cantidad"))
        bidones = "El bidón lo" if cantidad == 1 else f"Los {cantidad} bidones los"
        partes.append(
            PREGUNTA_CAPACIDAD_BIDON.format(
                bidones=bidones,
                capacidades=" o de ".join(f"{c}L" for c in _CAPACIDADES),
            )
        )
    elif aclaracion:
        partes.append(
            PREGUNTA_ACLARACION_BIDON.format(
                cantidad=aclaracion.get("cantidad", 1),
                capacidad=aclaracion.get("capacidad_litros", ""),
            )
        )
    elif draft.get("pendientes_modelo"):
        pendiente = draft["pendientes_modelo"][0]
        partes.append(
            PREGUNTA_MODELO.format(
                familia=pendiente["familia"].lower(),
                opciones=_lista_opciones(pendiente["opciones"]),
            )
        )
    elif sin_opciones:
        partes.append(PREGUNTA_PRODUCTO)
    return "\n\n".join(partes) or None


def _texto_paso_producto(draft: dict, respuesta_llm: str | None) -> str:
    """Pregunta del paso "producto". Lo que falta aclarar de los productos se
    pregunta siempre con el texto armado en código. Si no falta nada de eso,
    se deja pasar el texto del LLM solo si efectivamente habla de productos;
    si saltó a otro paso (pidió dirección, nombre, "¿algo más?"...) se
    reemplaza."""
    pregunta = _pregunta_pendiente_producto(draft)
    if pregunta:
        return pregunta
    if _respuesta_es_de_producto(respuesta_llm):
        return respuesta_llm
    productos = draft.get("productos") or []
    if productos and _promo_sin_capacidad(productos, draft.get("notas")):
        return PREGUNTA_CAPACIDAD_PROMO
    return PREGUNTA_PRODUCTO


def _texto_paso(
    paso: str,
    faltantes: list[str],
    draft: dict,
    cliente: dict | None,
    respuesta_llm: str | None,
    quiere_agregar_algo: bool,
) -> str:
    if paso == "producto":
        return _texto_paso_producto(draft, respuesta_llm)
    if paso == "nombre":
        return MENSAJE_PEDIR_NOMBRE
    if paso == "confirmar_direccion":
        return PREGUNTA_CONFIRMAR_DIRECCION.format(direccion=cliente["direccion"])
    if paso == "direccion":
        if "ubicacion" not in faltantes:
            return PREGUNTA_DIRECCION_TEXTO
        if cliente is not None and cliente.get("direccion"):
            return PREGUNTA_DIRECCION_NUEVA_Y_UBICACION
        return PREGUNTA_DIRECCION_Y_UBICACION
    if paso == "ubicacion":
        return PREGUNTA_UBICACION
    if paso == "algo_mas":
        return PREGUNTA_QUE_MAS if quiere_agregar_algo else PREGUNTA_ALGO_MAS
    raise ValueError(f"Paso desconocido: {paso}")


def _datos_despacho(draft: dict, cliente: dict | None) -> tuple[str, str]:
    """(nombre, dirección de despacho) para el resumen previo a confirmar. Las
    coordenadas no se muestran al cliente; se guardan al confirmar (ver
    _confirmar_pedido)."""
    nombre = cliente["nombre"] if cliente is not None else draft.get("nombre_cliente")
    if cliente is not None and draft.get("usa_direccion_habitual"):
        direccion = cliente["direccion"]
    else:
        direccion = draft.get("direccion_texto")
    return nombre, direccion


async def _construir_resumen(draft: dict, cliente: dict | None) -> dict:
    resumen = await construir_resumen_pedido(draft.get("productos") or [])
    nombre, direccion = _datos_despacho(draft, cliente)
    lineas_texto = "\n".join(
        f'- {linea["cantidad"]}x {linea["nombre"]} — {_formatear_clp(linea["subtotal"])}'
        for linea in resumen["lineas"]
    )
    bloques = [
        f"Resumen de tu pedido:\n{lineas_texto}\nTotal: {_formatear_clp(resumen['total'])}",
        f"Nombre: {nombre}\nDirección de despacho: {direccion}",
    ]
    if draft.get("notas"):
        bloques.append(f"Notas: {draft['notas']}")
    bloques.append("¿Confirmas el pedido? Responde SI para confirmar.")
    return {**resumen, "texto_resumen": "\n\n".join(bloques)}


def _saludo(cliente: dict | None) -> str:
    if cliente is not None:
        return f"¡Hola, {cliente['nombre']}!"
    return "¡Hola!"


def _quitar_saludo_inicial(texto: str) -> str:
    return re.sub(r"^\s*¡?\s*hola\b[^!.?\n]*[!.]?\s*", "", texto, flags=re.IGNORECASE)


async def _responder_siguiente_paso(
    phone: str,
    draft: dict,
    cliente: dict | None,
    respuesta_llm: str | None = None,
    quiere_agregar_algo: bool = False,
) -> tuple[str, str]:
    """Calcula en código el siguiente dato faltante, deja el draft en el
    estado/paso correspondiente y devuelve (paso, texto a enviar). Si no
    falta nada, arma el resumen y deja el draft esperando confirmación."""
    faltantes = _datos_faltantes(draft, cliente)

    if not faltantes:
        no_registrados = _productos_no_registrados(draft)
        if no_registrados:
            # El draft tiene menos unidades de las que el cliente pidió en la
            # conversación: no se muestra un resumen incompleto. Se le dice
            # qué falta y se reabre "¿algo más?"; el registro se iguala al
            # draft para no volver a reclamar lo mismo si responde que no.
            logger.error(
                "[order_flow] Productos pedidos sin registrar para phone=%s: %s (draft: %s)",
                phone,
                no_registrados,
                draft.get("productos"),
            )
            draft = {
                **draft,
                "unidades_pedidas": _unidades_draft(draft),
                "algo_mas_respondido": False,
                "paso": "algo_mas",
                "estado": "armando",
            }
            draft.pop("resumen", None)
            save_draft(phone, draft)
            faltantes_texto = ", ".join(f"{n}x {familia}" for familia, n in no_registrados.items())
            return "algo_mas", MENSAJE_PRODUCTOS_NO_REGISTRADOS.format(
                registrados=_resumen_productos_corto(draft.get("productos") or []),
                faltantes=faltantes_texto,
            )

        try:
            resumen = await _construir_resumen(draft, cliente)
        except ValueError:
            # El LLM devolvió un nombre de producto que no existe en la BD:
            # se descarta y se vuelve a preguntar el producto en vez de caer
            # con un error genérico.
            logger.exception("[order_flow] Productos inválidos en el draft de phone=%s", phone)
            draft = {**draft, "productos": [], "algo_mas_respondido": False}
            return await _responder_siguiente_paso(phone, draft, cliente)
        draft = {**draft, "paso": "confirmacion", "estado": ESTADO_ESPERANDO_CONFIRMACION, "resumen": resumen}
        save_draft(phone, draft)
        return "confirmacion", resumen["texto_resumen"]

    paso = faltantes[0]
    if paso in ("direccion", "ubicacion") and "ubicacion" in faltantes:
        estado = "esperando_ubicacion"
    else:
        estado = "armando"
    draft = {**draft, "paso": paso, "estado": estado}
    draft.pop("resumen", None)
    save_draft(phone, draft)
    return paso, _texto_paso(paso, faltantes, draft, cliente, respuesta_llm, quiere_agregar_algo)


async def _aplicar_resultado_llm(
    phone: str,
    resultado: dict,
    draft_previo: dict | None,
    cliente: dict | None,
    mensaje: str | None = None,
) -> str:
    """Combina lo que extrajo el LLM con el draft previo (sin perder datos ya
    capturados) y responde con el siguiente paso calculado en código."""
    es_primer_turno = draft_previo is None
    draft_previo = draft_previo or {}
    paso_previo = draft_previo.get("paso")
    intencion = resultado.get("intencion")

    # Un pedido en curso nunca se vacía por lo que devuelva el LLM (saludos,
    # interjecciones, mensajes ambiguos): la única forma de vaciarlo es una
    # cancelación EXPLÍCITA, que se intercepta antes en procesar_mensaje. Lo
    # que el LLM extrae en este turno se FUSIONA con lo que ya había (ver
    # _fusionar_productos), nunca lo reemplaza.
    productos_previos = draft_previo.get("productos") or []
    aclaracion_previa = draft_previo.get("aclaracion_pendiente")
    aclaracion_llm = resultado.get("aclaracion_pendiente")
    extraidos = resultado.get("productos") or []
    respuesta_llm = resultado.get("respuesta_sugerida")
    texto = _normalizar_texto(mensaje)
    modificacion_explicita = bool(_PATRON_MODIFICACION.search(texto))

    # Pregunta, queja o duda sobre el pedido en curso ("¿por qué asumes que
    # quiero bidones de 12?"): se responde, nunca con "fuera de alcance". Si
    # el LLM igual la marcó fuera de alcance, su texto no sirve y se usa uno
    # genérico. En una duda no se agrega ni se deja pendiente nada: los
    # productos que nombra son de lo que se queja, no un pedido nuevo.
    hay_pedido_previo = bool(productos_previos or aclaracion_previa or draft_previo.get("pendientes_modelo"))
    respuesta_duda = None
    es_duda = (
        hay_pedido_previo
        and _es_pregunta_o_reclamo(mensaje)
        and (intencion == "duda_pedido" or (intencion == "fuera_de_alcance" and _habla_del_pedido(texto)))
    )
    propuesta_llm = None
    if es_duda:
        respuesta_duda = respuesta_llm if intencion == "duda_pedido" else None
        propuesta_llm = resultado.get("correccion_propuesta") if intencion == "duda_pedido" else None
        intencion = "duda_pedido"
        resultado = {"intencion": intencion}
        extraidos, aclaracion_llm, respuesta_llm, mensaje, texto = [], None, None, None, ""

    # Justo después del resumen, "quiero el dispensador básico" sin decir
    # "cambia" ni "también" puede ser un cambio o algo que se suma (bug del
    # 2026-10-07: el bot repitió el mismo resumen sin aviso). El bot no
    # adivina: si no lo puede resolver, avisa (ver cambio_no_aplicado). Aplica
    # aunque el LLM lo marque duda_pedido, si no es pregunta ni reclamo
    # (es_duda False). En cualquier otro paso es False: en "¿algo más?", "sí,
    # quiero un dispensador" agrega.
    correccion_implicita = (
        paso_previo == "confirmacion"
        and not es_duda
        and intencion not in ("consulta_precio", "consulta_pedidos")
        and _menciona_algun_producto(texto)
        and not _PATRON_ADICION.search(texto)
    )
    catalogo = await _catalogo()
    no_encontrados: list[dict] = []
    lineas_codigo: list[dict] = []
    familias_resueltas: set[str] = set()

    # 1. Lo que estaba pendiente de aclarar (capacidad/variante de un bidón,
    # modelo de un dispensador o promo) y este mensaje responde se resuelve
    # en código; lo que el LLM haya extraído de esas familias en este turno
    # se ignora para no sumarlo dos veces.
    aclaracion_pendiente = aclaracion_previa
    resolucion = _resolver_aclaracion_bidon(aclaracion_previa, mensaje)
    if resolucion is not None:
        aclaracion_pendiente = resolucion["aclaracion"]
        lineas_codigo += resolucion["lineas"]
        familias_resueltas.add("Bidón")
        if resolucion["no_encontrado"]:
            no_encontrados.append(
                {"texto": resolucion["no_encontrado"], "opciones": []}
            )
    pendientes_modelo, lineas, resueltas = _resolver_pendientes_modelo(
        draft_previo.get("pendientes_modelo") or [], mensaje, catalogo
    )
    lineas_codigo += lineas
    familias_resueltas |= resueltas

    # 2. Lo que extrajo el LLM: solo productos del catálogo y con atributos
    # que el cliente dijo (nunca una capacidad o un modelo supuesto).
    # Un cambio de cantidad simple ("mejor que sean 2 bidones, no 3") se
    # resuelve en código sin depender de que el LLM devuelva "fijar"; lo que
    # el LLM extrajo de esa familia se ignora para no aplicarlo dos veces.
    cambio_simple = _cambio_cantidad_simple(productos_previos, texto)
    familia_cambio = _familia(productos_previos[cambio_simple[0]]["nombre_producto"]) if cambio_simple else None
    if familia_cambio in familias_resueltas:
        cambio_simple = familia_cambio = None
    # Un cambio de modelo, variante o capacidad ("mejor el dispensador
    # básico") también se resuelve en código. Si el cliente no dijo que era un
    # cambio (correccion_implicita), no se aplica: se le pregunta (más abajo).
    # Lo que el LLM extrajo de esa familia se ignora en ambos casos.
    cambio_atributo = None
    if (modificacion_explicita or correccion_implicita) and not cambio_simple:
        cambio_atributo = _cambio_atributo_simple(productos_previos, texto, catalogo)
    familia_atributo = _familia(cambio_atributo["cambios"][0]["desde"]) if cambio_atributo else None
    if familia_atributo in familias_resueltas:
        cambio_atributo = familia_atributo = None
    aceptados, nombres_no_encontrados = _validar_extraidos(extraidos, mensaje, productos_previos, catalogo)
    no_encontrados += [
        {"texto": f"«{nombre}»", "opciones": _sugerencias(nombre, None, catalogo)}
        for nombre in nombres_no_encontrados
    ]
    productos, familias_modificadas = _fusionar_productos(
        productos_previos,
        aceptados,
        mensaje,
        excluir_familias=frozenset(familias_resueltas | {familia_cambio, familia_atributo}),
    )
    # Si lo que extrajo el LLM no cambió nada, el único cambio de este turno
    # es el que resuelve el código (ver algo_mas_respondido más abajo).
    fusion_sin_cambios = productos == productos_previos
    if cambio_simple:
        indice, cantidad = cambio_simple
        nombre = productos_previos[indice]["nombre_producto"]
        logger.info("[order_flow] Cambio de cantidad resuelto en código: %s -> %d", nombre, cantidad)
        productos = [
            {**p, "cantidad": cantidad} if p.get("nombre_producto") == nombre else p for p in productos
        ]
        familias_modificadas.add(familia_cambio)
    if cambio_atributo and modificacion_explicita:
        cambio = cambio_atributo["cambios"][0]
        logger.info("[order_flow] Cambio de atributo resuelto en código: %s -> %s", cambio["desde"], cambio["hacia"])
        productos = _aplicar_cambios_correccion(productos, cambio_atributo)
        familias_modificadas.add(familia_atributo)

    # 3. Lo que el cliente mencionó y el LLM no extrajo (bien) no se descarta
    # en silencio: se agrega si calza con un solo producto, queda pendiente
    # si calza con varios, o se avisa que no existe. Con un pedido explícito
    # de cambio ("cambia los nuevos por recarga") las menciones son los
    # productos a cambiar, no productos nuevos: eso lo resuelve el LLM. En
    # una consulta ("¿cuánto cuesta el dispensador?") no se está pidiendo
    # nada.
    bidon_mencionado = None
    if intencion in ("consulta_precio", "consulta_pedidos", "duda_pedido"):
        pass
    elif not modificacion_explicita:
        menciones = [m for m in _menciones_productos(texto) if m["familia"] not in familias_resueltas]
        if correccion_implicita:
            # Lo que nombra de una familia que ya está en el pedido puede ser
            # un cambio: no se suma en código (sumaba un segundo dispensador
            # con "quiero el dispensador básico").
            en_pedido = {_familia(p.get("nombre_producto") or "") for p in productos_previos}
            menciones = [m for m in menciones if m["familia"] not in en_pedido]
        lineas, bidon_mencionado, nuevos_pendientes, sin_catalogo = _resolver_menciones(
            menciones, aceptados, catalogo
        )
        lineas_codigo += lineas
        pendientes_modelo += nuevos_pendientes
        no_encontrados += sin_catalogo
        if bidon_mencionado:
            aclaracion_pendiente = _sumar_aclaraciones(aclaracion_pendiente, bidon_mencionado)
    else:
        # "ya no quiero los bidones" / "olvida el dispensador": descarta lo
        # que esperaba aclaración de esa familia.
        if aclaracion_pendiente and resolucion is None and _menciona_tipo("bidon", texto):
            aclaracion_pendiente = None
            familias_modificadas.add("Bidón")
        descartados = [
            p for p in pendientes_modelo if _menciona_tipo(_tipo_producto(p["familia"]), texto)
        ]
        pendientes_modelo = [p for p in pendientes_modelo if p not in descartados]
        familias_modificadas |= {p["familia"] for p in descartados}

    for linea in lineas_codigo:
        _sumar_linea(productos, linea["nombre_producto"], linea["cantidad"])

    # 4. Un bidón ambiguo que solo detectó el LLM (ej. "quiero 2 de 20"): su
    # capacidad solo se acepta si el cliente la dijo. Si el código ya vio un
    # bidón en el mensaje, manda el código (el LLM deja pendiente "2 bidones
    # de 20 litros recarga" aunque esté completo).
    codigo_vio_bidon = (
        "Bidón" in familias_resueltas
        or any(m["familia"] == "Bidón" for m in _menciones_productos(texto))
        or any(_familia(item["nombre_producto"]) == "Bidón" for item in aceptados)
    )
    if aclaracion_llm and aclaracion_pendiente is None and not codigo_vio_bidon:
        capacidad = aclaracion_llm.get("capacidad_litros")
        if f"{capacidad}l" not in _atributos_mensaje(texto):
            capacidad = None
        aclaracion_pendiente = {
            "capacidad_litros": capacidad,
            "cantidad": max(1, _cantidad_valida(aclaracion_llm.get("cantidad"))),
        }

    if familias_resueltas or lineas_codigo or cambio_simple or cambio_atributo:
        # El texto del LLM pudo preguntar algo que el código ya resolvió.
        respuesta_llm = None
    hay_pendientes = bool(aclaracion_pendiente or pendientes_modelo or no_encontrados)
    productos_cambiaron = productos != productos_previos

    # Pedido explícito de cambio que no cambió nada (bug del 2026-10-07:
    # "mejor que sean 2 bidones, no 3" con el LLM devolviendo "productos"
    # vacío volvió a mostrar el mismo resumen, sin aviso). Se le dice que no
    # se pudo aplicar en vez de fingir que sí. No cuentan los pasos de texto
    # libre (una dirección "quiero cambiarla" no es un cambio del pedido), "no
    # quiero nada más" en "¿algo más?", ni las consultas que responde el LLM.
    # Vale también para una corrección implícita después del resumen.
    cambio_no_aplicado = (
        (modificacion_explicita or correccion_implicita)
        and productos_previos
        and not es_duda
        and intencion not in ("consulta_precio", "consulta_pedidos")
        and not (intencion == "fuera_de_alcance" and not _habla_del_pedido(texto))
        and paso_previo not in _PASOS_TEXTO_LIBRE
        and not (paso_previo == "algo_mas" and _no_quiere_nada_mas(mensaje))
        and not productos_cambiaron
        and not familias_modificadas
        and not no_encontrados
        and aclaracion_pendiente == aclaracion_previa
        and pendientes_modelo == (draft_previo.get("pendientes_modelo") or [])
    )

    # Datos ya capturados nunca se pierden ni se vuelven a pedir: un valor
    # nulo del LLM no borra lo que ya estaba en el draft.
    nombre_cliente = draft_previo.get("nombre_cliente")
    if cliente is None:
        nombre_cliente = resultado.get("nombre_cliente") or nombre_cliente
        if not nombre_cliente and paso_previo == "nombre":
            nombre_cliente = _nombre_desde_texto(mensaje)

    usa_direccion_habitual = draft_previo.get("usa_direccion_habitual")
    direccion_texto = draft_previo.get("direccion_texto")
    ubicacion_rechazada = bool(draft_previo.get("ubicacion_rechazada"))
    tiene_direccion_registrada = cliente is not None and bool(cliente.get("direccion"))

    if not tiene_direccion_registrada:
        usa_direccion_habitual = False
    elif usa_direccion_habitual is None:
        if resultado.get("direccion_texto"):
            usa_direccion_habitual = False
        elif paso_previo == "confirmar_direccion":
            # Respuesta a "¿Despachamos a {dirección}?". La negativa se
            # evalúa primero ("sí, pero es otra" → dirección nueva).
            if _pide_direccion_distinta(mensaje):
                usa_direccion_habitual = False
            elif resultado.get("usa_direccion_habitual") or _es_afirmativa(mensaje):
                usa_direccion_habitual = True

    if not usa_direccion_habitual:
        direccion_texto = resultado.get("direccion_texto") or direccion_texto
        ubicacion_rechazada = ubicacion_rechazada or bool(resultado.get("ubicacion_rechazada"))

    notas = resultado.get("notas") or draft_previo.get("notas")

    algo_mas_respondido = bool(draft_previo.get("algo_mas_respondido"))
    quiere_agregar_algo = False
    # Un cambio pedido sobre el resumen y resuelto en código ("mejor que sean
    # 2 bidones", "mejor el dispensador básico") vuelve directo al resumen
    # actualizado: el cliente ya respondió "¿algo más?". Cualquier otro cambio
    # de productos lo reabre.
    cambio_resuelto_en_codigo = (
        paso_previo == "confirmacion"
        and bool(cambio_simple or (cambio_atributo and modificacion_explicita))
        and fusion_sin_cambios
        and not lineas_codigo
    )
    if productos_cambiaron and productos_previos and not cambio_resuelto_en_codigo:
        algo_mas_respondido = False
    elif paso_previo == "algo_mas":
        # "sí" a "¿algo más?" significa que quiere agregar algo, aunque el
        # LLM lo marque como pedido_completo (visto con "Si" a secas).
        quiere_agregar_algo = _es_afirmativa(mensaje) and not _no_quiere_nada_mas(mensaje)
        algo_mas_respondido = not quiere_agregar_algo and (
            bool(resultado.get("pedido_completo")) or _no_quiere_nada_mas(mensaje)
        )

    nuevo_draft = {
        "intencion": "pedido" if productos else intencion,
        "productos": productos,
        "aclaracion_pendiente": aclaracion_pendiente,
        "notas": notas,
        "nombre_cliente": nombre_cliente,
        "usa_direccion_habitual": usa_direccion_habitual,
        "direccion_texto": direccion_texto,
        "ubicacion": draft_previo.get("ubicacion"),
        "ubicacion_rechazada": ubicacion_rechazada,
        "algo_mas_respondido": algo_mas_respondido,
        "pendientes_modelo": pendientes_modelo,
        # Solo de este turno: se recalcula en cada mensaje.
        "no_encontrados": no_encontrados,
    }
    nuevo_draft["unidades_pedidas"] = _actualizar_unidades_pedidas(
        draft_previo, nuevo_draft, familias_modificadas
    )

    # Salvaguarda de "sin productos no hay '¿algo más?' ni resumen": como
    # "producto" es siempre el primer dato faltante cuando la lista está
    # vacía (ver _datos_faltantes), con un draft sin productos el paso
    # calculado es "producto" sin importar qué haya respondido el LLM (que en
    # producción llegó a preguntar "¿algo más?" sin productos). Se verifica
    # explícitamente para que un cambio futuro en el orden no lo rompa.
    paso, texto = await _responder_siguiente_paso(
        phone, nuevo_draft, cliente, respuesta_llm, quiere_agregar_algo
    )
    if not productos and paso != "producto":
        logger.error("[order_flow] Paso '%s' calculado sin productos para phone=%s", paso, phone)
        save_draft(phone, {**nuevo_draft, "paso": "producto", "estado": "armando"})
        paso, texto = "producto", PREGUNTA_PRODUCTO

    if cambio_atributo and not modificacion_explicita:
        # No se adivina si cambia o suma: se pregunta, y la propuesta queda
        # guardada para el mensaje siguiente (ver _responder_a_correccion). Un
        # "sí" posterior responde esta pregunta, nunca confirma el pedido.
        cambio = cambio_atributo["cambios"][0]
        guardado = get_draft(phone) or nuevo_draft
        save_draft(
            phone,
            {
                **guardado,
                "estado": ESTADO_ESPERANDO_MODIFICACION if paso == "confirmacion" else guardado.get("estado"),
                "correccion_pendiente": {**cambio_atributo, "permite_agregar": True},
            },
        )
        return PREGUNTA_CAMBIAR_O_AGREGAR.format(actual=cambio["desde"], nuevo=cambio["hacia"])

    if cambio_no_aplicado:
        logger.info("[order_flow] Cambio pedido y no aplicado para phone=%s: %r", phone, mensaje)
        if paso == "confirmacion":
            # Un "sí" suelto después no debe confirmar el pedido que el
            # cliente quería cambiar. En "¿algo más?" el estado no se toca:
            # un "no" posterior es "no quiero nada más", no una cancelación.
            save_draft(phone, {**(get_draft(phone) or nuevo_draft), "estado": ESTADO_ESPERANDO_MODIFICACION})
        aviso = MENSAJE_CAMBIO_NO_APLICADO if modificacion_explicita else MENSAJE_MODIFICACION_NO_ENTENDIDA
        return aviso.format(pedido=_resumen_productos_corto(productos))

    # Con el pedido listo para confirmar, una respuesta a otra cosa (duda,
    # tema fuera de alcance) no va pegada al resumen: va seguida de las
    # opciones, y el pedido pasa a esperando_modificacion para que un "sí"
    # suelto no lo confirme (solo se confirma justo después del resumen).
    siguiente = PREGUNTA_OPCIONES_PEDIDO if paso == "confirmacion" else texto
    correccion = None
    if es_duda:
        correccion = _validar_correccion(propuesta_llm, productos, catalogo)
        if correccion:
            # La pregunta final la arma el código desde la corrección validada,
            # para que un "sí" responda exactamente a lo que quedó guardado.
            explicacion = _sin_pregunta_final(respuesta_duda)
            texto = f"{explicacion}\n\n{_pregunta_correccion(correccion, productos)}".strip()
        else:
            if propuesta_llm:
                # El LLM ofreció algo que no existe en el catálogo (o no calza
                # con el pedido): no se ofrece.
                logger.info("[order_flow] Corrección propuesta descartada: %s", propuesta_llm)
                respuesta_duda = None
            respuesta = respuesta_duda or (
                MENSAJE_DUDA_GENERICA.format(pedido=_resumen_productos_corto(productos))
                if productos
                else MENSAJE_DUDA_SIN_PRODUCTOS
            )
            texto = f"{respuesta}\n\n{siguiente}"
    elif (
        intencion in _INTENCIONES_RESPUESTA_LLM
        and resultado.get("respuesta_sugerida")
        # Si el cliente pidió algo que el código resolvió, agregó, dejó
        # pendiente o no encontró en el catálogo, el "fuera de alcance" del
        # LLM es un error: va la respuesta del código.
        and not (intencion == "fuera_de_alcance" and (hay_pendientes or lineas_codigo or familias_resueltas))
    ):
        # Consulta de precio/pedidos o tema fuera de alcance: se responde lo
        # que preguntó y, si hay un pedido en curso, se retoma el paso
        # pendiente a continuación (tras una consulta de precio sí se vuelve a
        # mostrar el resumen). Si el cliente pidió algo que el código dejó
        # pendiente o no encontró en el catálogo, va la pregunta del código
        # (el LLM suele responder "fuera de alcance" sin decir qué producto
        # no existe).
        if intencion == "fuera_de_alcance":
            texto = siguiente
        texto = (
            f"{resultado['respuesta_sugerida']}\n\n{texto}"
            if productos or hay_pendientes
            else resultado["respuesta_sugerida"]
        )
    elif productos_previos and not productos_cambiaron and intencion != "pedido" and paso != "confirmacion":
        texto = f"Sigo con tu pedido de {_resumen_productos_corto(productos)}. {texto}"

    if paso == "confirmacion" and "Resumen de tu pedido" not in texto:
        save_draft(phone, {**(get_draft(phone) or nuevo_draft), "estado": ESTADO_ESPERANDO_MODIFICACION})
    if correccion:
        # Válida solo para el mensaje siguiente (ver procesar_mensaje).
        save_draft(phone, {**(get_draft(phone) or nuevo_draft), "correccion_pendiente": correccion})

    if es_primer_turno:
        texto = f"{_saludo(cliente)} {_quitar_saludo_inicial(texto)}"

    return texto


async def _aplicar_ubicacion(
    phone: str, location: dict | None, draft_previo: dict | None, cliente: dict | None
) -> str:
    """Ubicación de WhatsApp recibida, en cualquier estado del flujo. Solo
    se agregan las coordenadas al draft (sin pasar por el LLM, para que nada
    de lo ya capturado —nombre, dirección, productos— se pierda) y se
    responde con el siguiente paso calculado en código."""
    draft = dict(draft_previo or {})
    tiene_direccion_registrada = cliente is not None and bool(cliente.get("direccion"))

    if draft.get("usa_direccion_habitual") and tiene_direccion_registrada:
        # Ya confirmó su dirección registrada: se despacha con las
        # coordenadas guardadas del cliente, esta ubicación no se usa.
        _, texto = await _responder_siguiente_paso(phone, draft, cliente)
        return texto

    ubicacion = location or {}
    draft["ubicacion"] = {
        "latitud": ubicacion.get("latitude"),
        "longitud": ubicacion.get("longitude"),
    }
    if tiene_direccion_registrada and draft.get("usa_direccion_habitual") is None:
        # Compartir una ubicación mientras se le pregunta por su dirección
        # registrada equivale a indicar una dirección distinta.
        draft["usa_direccion_habitual"] = False

    _, texto = await _responder_siguiente_paso(phone, draft, cliente)
    texto = f"{MENSAJE_UBICACION_RECIBIDA} {texto}"
    if draft_previo is None:
        texto = f"{_saludo(cliente)} {texto}"
    return texto


async def _escalar_telefono_duplicado(phone: str, clientes: list[Cliente]) -> str:
    """Más de un cliente con el mismo teléfono: el bot no elige uno. Se avisa
    a la ejecutiva, se cierra la conversación del bot (los mensajes
    siguientes van a la ejecutiva, ver whatsapp.py) y se le avisa al cliente
    que lo contactarán, con el mismo mensaje de derivación según horario de
    atención que un mensaje espontáneo (ver horario_atencion)."""
    ids = ", ".join(str(cliente.id) for cliente in clientes)
    logger.warning("[order_flow] Teléfono %s registrado en varios clientes (ids %s)", phone, ids)
    clear_draft(phone)
    ahora = datetime.now().astimezone()
    try:
        tipo_cliente, nombres = clasificar_clientes(clientes)
        await send_whatsapp_message(
            to=settings.EJECUTIVA_PHONE,
            message=texto_notificacion_ejecutiva(
                tipo_cliente, nombres, phone, fuera_de_horario=not en_horario_atencion(ahora)
            ),
        )
    except Exception:
        logger.exception("[order_flow] Falló avisar a la ejecutiva del teléfono duplicado %s", phone)
    await marcar_inactiva(phone)
    return mensaje_derivacion_ejecutiva(ahora)


async def _buscar_clientes(phone: str) -> list[Cliente]:
    async with SessionLocal() as session:
        return await buscar_clientes_por_telefono(session, phone)


def _agregar_auditoria_cliente(
    session, cliente: Cliente, accion: str, antes: dict | None
) -> None:
    # Se agrega a la MISMA sesión que el pedido (no vía registrar_auditoria,
    # que abre su propia sesión) para que quede en la misma transacción.
    session.add(
        Auditoria(
            usuario=USUARIO_AUDITORIA_BOT,
            entidad="cliente",
            entidad_id=cliente.id,
            accion=accion,
            antes=antes,
            despues=construir_snapshot(cliente),
        )
    )


async def _confirmar_pedido(phone: str, draft: dict | None) -> str:
    if draft is None:
        # Idempotencia: un mensaje duplicado (ej. reintento de webhook) puede
        # llegar después de que el primero ya confirmó el pedido y limpió el
        # draft. En vez de fallar o crear un pedido nuevo, respondemos con un
        # mensaje neutro.
        return "Tu pedido ya fue confirmado anteriormente."

    # Creación/actualización del cliente, su auditoría y el pedido con sus
    # detalles van en UNA sola transacción: si algo falla, rollback de todo
    # (no queda un pedido sin el cliente actualizado ni al revés).
    async with SessionLocal() as session:
        clientes = await buscar_clientes_por_telefono(session, phone)
        if len(clientes) > 1:
            return await _escalar_telefono_duplicado(phone, clientes)
        cliente = clientes[0] if clientes else None

        faltantes = _datos_faltantes(draft, _datos_cliente(cliente))
        if faltantes:
            # Defensivo: el cliente pudo cambiar en la BD entre el resumen y
            # el "SI" (ej. la ejecutiva borró su dirección). Se retoma el
            # paso que falte en vez de crear un pedido incompleto.
            _, texto = await _responder_siguiente_paso(phone, draft, _datos_cliente(cliente))
            return texto

        try:
            ubicacion = draft.get("ubicacion") or {}
            latitud = ubicacion.get("latitud")
            longitud = ubicacion.get("longitud")

            if cliente is None:
                cliente = Cliente(
                    telefono=normalizar_telefono(phone),
                    nombre=draft["nombre_cliente"],
                    direccion=draft["direccion_texto"],
                    latitud=latitud,
                    longitud=longitud,
                    activo=True,
                    opt_out_whatsapp=False,
                )
                session.add(cliente)
                await session.flush()
                await session.refresh(cliente)
                _agregar_auditoria_cliente(session, cliente, "crear", antes=None)
            elif not draft.get("usa_direccion_habitual"):
                # Dirección nueva: reemplaza la registrada y sus coordenadas
                # (null si no compartió ubicación, para no dejar las de la
                # dirección anterior).
                snapshot_antes = construir_snapshot(cliente)
                cliente.direccion = draft["direccion_texto"]
                cliente.latitud = latitud
                cliente.longitud = longitud
                await session.flush()
                await session.refresh(cliente)
                _agregar_auditoria_cliente(session, cliente, "actualizar", antes=snapshot_antes)

            resumen = draft.get("resumen") or {}
            lineas = resumen.get("lineas", [])

            nombres_productos = [linea["nombre"] for linea in lineas]
            result = await session.execute(
                select(Producto).where(Producto.nombre.in_(nombres_productos))
            )
            productos_bd = {p.nombre: p for p in result.scalars().all()}

            # En este punto el cliente ya tiene la dirección de despacho de
            # este pedido (la registrada que confirmó, o la nueva recién
            # guardada), así que el pedido la copia tal cual.
            pedido = Pedido(
                cliente_id=cliente.id,
                estado=EstadoPedido.PENDIENTE,
                direccion_despacho=cliente.direccion,
                latitud=cliente.latitud,
                longitud=cliente.longitud,
                total=resumen.get("total", 0),
            )
            session.add(pedido)
            await session.flush()

            for linea in lineas:
                producto = productos_bd[linea["nombre"]]
                session.add(
                    DetallePedido(
                        pedido_id=pedido.id,
                        producto_id=producto.id,
                        cantidad_solicitada=linea["cantidad"],
                        precio_unitario=linea["precio_unitario"],
                    )
                )

            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("[order_flow] Falló la creación del pedido para phone=%s", phone)
            return MENSAJE_ERROR_PEDIDO

    clear_draft(phone)
    await marcar_inactiva(phone)
    return (
        f"¡Pedido #{pedido.id} confirmado! Quedó pendiente de revisión, "
        "te contactaremos para coordinar la entrega."
    )


def _quiere_agregar(texto: str | None) -> bool:
    """Respuesta a PREGUNTA_CAMBIAR_O_AGREGAR que pide sumar ("agrégalo",
    "los dos"). Si dice ambas cosas no se adivina."""
    texto_normalizado = _normalizar_texto(texto)
    return bool(_PATRON_RESPUESTA_AGREGAR.search(texto_normalizado)) and not _PATRON_RESPUESTA_CAMBIAR.search(
        texto_normalizado
    )


def _quiere_cambiar(texto: str | None) -> bool:
    """Respuesta a PREGUNTA_CAMBIAR_O_AGREGAR que pide reemplazar
    ("cámbialo", "reemplázalo")."""
    texto_normalizado = _normalizar_texto(texto)
    return bool(_PATRON_RESPUESTA_CAMBIAR.search(texto_normalizado)) and not _PATRON_RESPUESTA_AGREGAR.search(
        texto_normalizado
    )


async def _responder_a_correccion(
    phone: str, draft: dict, cliente: dict | None, correccion: dict, mensaje: str | None
) -> str | None:
    """Respuesta del cliente a una corrección ofrecida por el bot. Devuelve
    el texto a enviar, o None si el mensaje no responde la pregunta (la
    corrección ya quedó descartada y el mensaje sigue el flujo normal)."""
    if correccion.get("pide_cantidad"):
        unidades = _unidades_respuesta(mensaje)
        if unidades == "todas":
            correccion = {**correccion, "pide_cantidad": False}
        elif unidades is not None and len(correccion["cambios"]) == 1:
            correccion = {
                "cambios": [{**correccion["cambios"][0], "cantidad": unidades}],
                "pide_cantidad": False,
            }
        else:
            return None
    elif correccion.get("permite_agregar") and _quiere_agregar(mensaje):
        # "agrégalo", "los dos": suma el producto nuevo con la misma cantidad
        # que la línea actual, sin quitar nada.
        cambio = correccion["cambios"][0]
        productos = [dict(p) for p in draft.get("productos") or []]
        cantidad = next(
            (_cantidad_valida(p.get("cantidad")) for p in productos if p.get("nombre_producto") == cambio["desde"]), 1
        )
        _sumar_linea(productos, cambio["hacia"], cantidad)
        nuevo = {**draft, "productos": productos}
        nuevo["unidades_pedidas"] = _unidades_draft(nuevo)
        _, texto = await _responder_siguiente_paso(phone, nuevo, cliente)
        logger.info("[order_flow] Producto agregado para phone=%s: %dx %s", phone, cantidad, cambio["hacia"])
        return f"{MENSAJE_PRODUCTO_AGREGADO}\n\n{texto}"
    elif _es_negativa_simple(mensaje):
        save_draft(phone, {**draft, "estado": ESTADO_ESPERANDO_MODIFICACION})
        return MENSAJE_CORRECCION_RECHAZADA
    elif not (_es_confirmacion_explicita(mensaje) or (correccion.get("permite_agregar") and _quiere_cambiar(mensaje))):
        return None

    productos = _aplicar_cambios_correccion(draft.get("productos") or [], correccion)
    nuevo = {**draft, "productos": productos}
    # Cambio pedido explícitamente: lo pedido pasa a ser lo que quedó.
    nuevo["unidades_pedidas"] = _unidades_draft(nuevo)
    paso, texto = await _responder_siguiente_paso(phone, nuevo, cliente)
    logger.info("[order_flow] Corrección aplicada para phone=%s: %s", phone, correccion["cambios"])
    return f"{MENSAJE_CORRECCION_APLICADA}\n\n{texto}"


async def _es_cliente_nuevo(phone: str) -> bool:
    return not await _buscar_clientes(phone)

# ---------------------------------------------------------------------------
# Historia #64: ofrecer repetir el último pedido entregado
# ---------------------------------------------------------------------------


async def _ultimo_pedido_entregado(cliente_id: int) -> list[dict]:
    """Líneas ({nombre_producto, cantidad}) del último pedido ENTREGADO del
    cliente, o [] si no tiene historial. Se repite lo solicitado y se omiten
    los productos que ya no están en el catálogo."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido)
            .where(Pedido.cliente_id == cliente_id, Pedido.estado == EstadoPedido.ENTREGADO)
            .options(selectinload(Pedido.detalles).selectinload(DetallePedido.producto))
            .order_by(Pedido.creado_en.desc(), Pedido.id.desc())
            .limit(1)
        )
        pedido = result.scalar_one_or_none()

    if pedido is None:
        return []
    return [
        {"nombre_producto": detalle.producto.nombre, "cantidad": detalle.cantidad_solicitada}
        for detalle in pedido.detalles
        if detalle.producto.nombre in CATALOGO_NOMBRES and detalle.cantidad_solicitada > 0
    ]


def _es_saludo(texto: str | None) -> bool:
    return _empieza_con(_normalizar_texto(texto), ("hola", "holi", "buenas", "buenos", "buen dia", "hey"))


async def _ofrecer_repetir_pedido(
    phone: str, cliente: dict | None, resultado: dict, mensaje: str | None
) -> str | None:
    """Primer mensaje de un cliente existente que todavía no pidió nada: si
    tiene un pedido entregado, se le ofrece repetirlo. Devuelve el texto de la
    oferta, o None si no corresponde ofrecerla."""
    texto = _normalizar_texto(mensaje)
    intencion = resultado.get("intencion")
    if (
        cliente is None
        or intencion in ("consulta_precio", "consulta_pedidos", "duda_pedido")
        or not (intencion == "pedido" or _es_saludo(mensaje))
        or resultado.get("productos")
        or resultado.get("aclaracion_pendiente")
        or _menciona_algun_producto(texto)
    ):
        return None

    lineas = await _ultimo_pedido_entregado(cliente["id"])
    if not lineas:
        return None

    # La oferta queda en el draft (sin productos) hasta que responda.
    save_draft(
        phone,
        {"paso": "producto", "estado": "armando", "productos": [], "oferta_repetir": lineas},
    )
    return (
        f"{_saludo(cliente)} ¿Quieres repetir tu último pedido "
        f"({_resumen_productos_corto(lineas)})? Responde SÍ, o cuéntame qué necesitas."
    )


async def _responder_a_oferta_repetir(
    phone: str, draft: dict, cliente: dict | None, mensaje: str | None
) -> str | None:
    """Respuesta a la oferta de repetir el último pedido. Devuelve el texto a
    enviar, o None si el mensaje no responde la oferta (la oferta ya quedó
    descartada y el mensaje sigue el flujo normal)."""
    lineas = draft["oferta_repetir"]
    base = {clave: valor for clave, valor in draft.items() if clave != "oferta_repetir"}
    texto = _normalizar_texto(mensaje)

    if _es_negativa_simple(mensaje):
        save_draft(phone, base)
        return f"Entendido. {PREGUNTA_PRODUCTO}"

    acepta = (
        (_es_afirmativa(mensaje) or "repet" in texto or "lo mismo" in texto)
        and not _empieza_con(texto, ("no",))
        and not _menciona_algun_producto(texto)
    )
    if not acepta:
        save_draft(phone, base)
        return None

    nuevo = {**base, "productos": lineas}
    nuevo["unidades_pedidas"] = _unidades_draft(nuevo)
    _, respuesta = await _responder_siguiente_paso(phone, nuevo, cliente)
    return f"Perfecto, repetimos tu último pedido.\n\n{respuesta}"

async def procesar_mensaje(
    phone: str,
    message_type: str,
    message_text: str | None,
    location: dict | None,
) -> str:
    # Serializa el procesamiento de mensajes de un mismo teléfono: si llegan
    # dos mensajes del mismo cliente en paralelo (ej. reintento de webhook),
    # el segundo espera a que el primero termine por completo (incluyendo
    # cualquier escritura en el draft o creación de pedido) antes de empezar.
    async with get_lock(phone):
        # Identificación por teléfono (normalizado) en cada mensaje: si hay
        # más de un cliente con ese teléfono, no se elige uno.
        clientes = await _buscar_clientes(phone)
        if len(clientes) > 1:
            return await _escalar_telefono_duplicado(phone, clientes)
        cliente = _datos_cliente(clientes[0]) if clientes else None

        draft = get_draft(phone)
        estado = draft.get("estado") if draft else None

        # Historia #64: respuesta a la oferta de repetir el último pedido.
        if draft is not None and draft.get("oferta_repetir") and message_type == "text":
            respuesta = await _responder_a_oferta_repetir(phone, draft, cliente, message_text)
            if respuesta is not None:
                return respuesta
            draft = get_draft(phone)
            estado = draft.get("estado") if draft else None

        # Cancelación EXPLÍCITA del pedido/estado en curso: se intercepta antes
        # de llamar al LLM y antes de cualquier otra rama del flujo, con
        # cualquier draft (con o sin productos). clear_draft(phone) borra el
        # draft completo, y como la tabla cliente solo se escribe al
        # confirmar, cancelar no deja ningún cambio en cliente. Si no es claro
        # que quiera cancelar (ver _intencion_cancelar), se le pregunta.
        if draft is not None and message_type == "text":
            cancelacion = _intencion_cancelar(
                message_text, texto_libre=draft.get("paso") in _PASOS_TEXTO_LIBRE
            )
            if cancelacion == "cancelar":
                clear_draft(phone)
                return MENSAJE_PEDIDO_CANCELADO
            if cancelacion == "duda":
                if estado in (ESTADO_ESPERANDO_CONFIRMACION, ESTADO_ESPERANDO_MODIFICACION):
                    # Tras esta pregunta un "sí" no debe confirmar el pedido.
                    save_draft(phone, {**draft, "estado": ESTADO_ESPERANDO_MODIFICACION})
                return PREGUNTA_CANCELAR

        # Corrección que el bot ofreció en su último mensaje ("¿Quieres que
        # cambie ... por Bidón 20L ...?"): vale solo para este mensaje. Se
        # aplica con un sí explícito (o con la cantidad, si se preguntó
        # cuántas), "no" la descarta y pregunta qué cambiar, y cualquier otra
        # cosa la descarta y sigue el flujo normal.
        correccion = draft.pop("correccion_pendiente", None) if draft else None
        if correccion:
            save_draft(phone, draft)
            if message_type == "text":
                respuesta = await _responder_a_correccion(phone, draft, cliente, correccion, message_text)
                if respuesta is not None:
                    return respuesta

        if message_type == "location":
            return await _aplicar_ubicacion(phone, location, draft, cliente)

        # Solo se confirma con un sí explícito y si el último mensaje del bot
        # fue el resumen. Cualquier otra respuesta no confirma: una negativa
        # pregunta qué cambiar, y el resto (cambios, consultas) pasa al LLM,
        # que vuelve a armar el resumen si no falta nada.
        if estado == ESTADO_ESPERANDO_CONFIRMACION:
            if _es_confirmacion_explicita(message_text):
                return await _confirmar_pedido(phone, draft)
            if _es_negativa_simple(message_text):
                save_draft(phone, {**draft, "estado": ESTADO_ESPERANDO_MODIFICACION})
                return MENSAJE_NO_CONFIRMADO
        elif estado == ESTADO_ESPERANDO_MODIFICACION:
            if _es_confirmacion_explicita(message_text):
                # Respondió "no" al resumen y ahora "sí": no se confirma (¿sí a
                # qué?). Se muestra de nuevo el resumen para que confirme
                # explícitamente.
                paso, texto = await _responder_siguiente_paso(phone, draft, cliente)
                return f"{PREFIJO_REPETIR_RESUMEN}\n\n{texto}" if paso == "confirmacion" else texto
            if _es_negativa_simple(message_text):
                return PREGUNTA_CANCELAR

        resultado = await _interpretar_con_debug(
            phone, message_text or "", cliente is None, _contexto_desde_draft(draft)
        )

        # Historia #64: primer mensaje de un cliente con historial.
        if draft is None and message_type == "text":
            oferta = await _ofrecer_repetir_pedido(phone, cliente, resultado, message_text)
            if oferta is not None:
                return oferta

        return await _aplicar_resultado_llm(phone, resultado, draft, cliente, message_text)
