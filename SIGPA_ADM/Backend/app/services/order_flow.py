"""Orquestación del flujo de pedidos por WhatsApp.

Conecta interpret_message (interpretación del LLM), construir_resumen_pedido
(precios reales desde la BD) y draft_store (borrador de pedido en curso por
cliente) para decidir qué responder en cada mensaje entrante.
"""

import json
import logging

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Cliente, DetallePedido, Pedido, Producto
from app.models.enums import EstadoPedido
from app.services.agent_service import construir_resumen_pedido, interpret_message
from app.services.draft_store import clear_draft, get_draft, save_draft

logger = logging.getLogger(__name__)

CONFIRMACIONES = {"si", "sí", "confirmo", "dale", "ok"}

MENSAJE_PEDIR_UBICACION = (
    "Para continuar necesito que compartas tu ubicación de WhatsApp: usa el clip 📎 "
    "y selecciona 'Ubicación'."
)

PREGUNTA_DIRECCION_HABITUAL = (
    "¿Confirmas tu dirección habitual o prefieres indicar una distinta para este pedido?"
)

PREGUNTA_ALGO_MAS = "¿Deseas agregar algo más a tu pedido?"

MENSAJE_CONTINUAR_PEDIDO_GENERICO = (
    "¡Perfecto, ya tengo registrada tu dirección de despacho! ¿Cómo seguimos con tu pedido?"
)

# Frases (no solo palabras sueltas) que indican que el texto está PIDIENDO o
# volviendo a CONFIRMAR dirección/ubicación al cliente. Deliberadamente más
# específicas que un simple "direcci"/"ubicaci": una mención declarativa y
# legítima como "...a tu dirección habitual" o "ya registré tu ubicación" no
# debe dispararlas, solo un pedido/confirmación real (segunda persona:
# "confirmas", "compartas", "indica", etc.).
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

MENSAJE_PEDIR_NOMBRE = "¿A nombre de quién registramos tu pedido?"

MENSAJE_ERROR_PEDIDO = (
    "Hubo un problema al registrar tu pedido, por favor intenta de nuevo o contacta a un ejecutivo."
)


def _contexto_desde_draft(draft: dict | None) -> dict | None:
    if draft is None:
        return None
    return {
        "intencion": draft.get("intencion"),
        "productos": draft.get("productos", []),
        "aclaracion_pendiente": draft.get("aclaracion_pendiente"),
        "usa_direccion_habitual": draft.get("usa_direccion_habitual"),
        "direccion_texto": draft.get("direccion_texto"),
        "notas": draft.get("notas"),
        "ubicacion_recibida": draft.get("ubicacion") is not None,
        "nombre_cliente": draft.get("nombre_cliente"),
        "algo_mas_preguntado": bool(draft.get("algo_mas_preguntado")),
    }


def _pedido_listo_salvo_algo_mas(
    resultado: dict, es_cliente_nuevo: bool, ubicacion_recibida: bool
) -> bool:
    """True si productos/dirección/nombre ya están resueltos y lo único que
    falta para pedido_completo es el paso "¿algo más?" (ver SYSTEM_PROMPT).
    Se usa solo para trackear "algo_mas_preguntado" en el draft, nunca para
    forzar pedido_completo por código (eso lo decide el LLM)."""
    productos = resultado.get("productos") or []
    if not productos or resultado.get("aclaracion_pendiente"):
        return False
    if es_cliente_nuevo:
        return (
            bool(resultado.get("nombre_cliente"))
            and resultado.get("direccion_texto") is not None
            and ubicacion_recibida
        )
    return bool(resultado.get("usa_direccion_habitual")) or (
        resultado.get("direccion_texto") is not None and ubicacion_recibida
    )


def _menciona_direccion_o_ubicacion(texto: str | None) -> bool:
    """Detección por frases: True si el texto vuelve a pedir o confirmar
    dirección, o pide compartir ubicación, en vez de solo mencionarla de
    paso (ver _FRASES_REABREN_DIRECCION_UBICACION). Se usa para sanear
    respuestas del LLM que reabren ese tema cuando ya no corresponde (ver
    salvaguarda centralizada de dirección en _aplicar_resultado_llm)."""
    if not texto:
        return False
    texto_normalizado = texto.lower()
    return any(frase in texto_normalizado for frase in _FRASES_REABREN_DIRECCION_UBICACION)


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


async def _aplicar_resultado_llm(
    phone: str, resultado: dict, draft_previo: dict | None, es_cliente_nuevo: bool
) -> str:
    draft_previo = draft_previo or {}
    direccion_preguntada = bool(draft_previo.get("direccion_preguntada"))
    respuesta_a_sanear = False

    # Salvaguarda CENTRALIZADA de dirección/ubicación: un solo punto de
    # verdad sobre "¿ya está resuelta la dirección de despacho?", aplicado
    # en todos los caminos del flujo. Reemplaza a las dos salvaguardas
    # puntuales que existían antes (una solo para "usa_direccion_habitual",
    # otra solo para el primer "esperando_ubicacion" de un cliente
    # existente). Son tres ramas mutuamente excluyentes según el momento del
    # flujo en el que estemos, pero comparten el mismo mecanismo de saneo de
    # texto más abajo (respuesta_a_sanear):
    if direccion_preguntada:
        # A) La dirección YA quedó resuelta en un turno anterior
        #    (draft_previo.direccion_preguntada == True). El LLM no debe
        #    volver a tocarla en este turno:
        #    1. Ignoramos cualquier cambio que proponga a
        #       "usa_direccion_habitual"/"direccion_texto": se mantienen los
        #       valores que ya estaban en el draft.
        #    2. Forzamos "esperando_ubicacion": False incondicionalmente: la
        #       ubicación, si hacía falta, ya se resolvió o no aplica aquí.
        #    3. Si el texto que sugirió el LLM en este turno vuelve a pedir o
        #       confirmar dirección o a pedir ubicación (bug intermitente del
        #       LLM), lo marcamos para reemplazarlo más abajo por el texto
        #       que sí corresponde al estado real del pedido en este punto.
        respuesta_a_sanear = _menciona_direccion_o_ubicacion(resultado.get("respuesta_sugerida"))
        resultado = {
            **resultado,
            "usa_direccion_habitual": draft_previo.get("usa_direccion_habitual"),
            "direccion_texto": draft_previo.get("direccion_texto"),
            "esperando_ubicacion": False,
        }
    elif resultado.get("usa_direccion_habitual"):
        # B) La dirección se resuelve recién EN ESTE turno: el cliente
        #    acaba de confirmar su dirección habitual (draft_previo todavía
        #    no tenía direccion_preguntada=True). Aceptamos ese cambio tal
        #    como lo decidió el LLM —no hay nada que "ignorar" todavía—,
        #    pero la dirección habitual nunca requiere ubicación: forzamos
        #    "esperando_ubicacion": False y saneamos el texto si de todas
        #    formas la pidió por error en esta misma respuesta.
        respuesta_a_sanear = _menciona_direccion_o_ubicacion(resultado.get("respuesta_sugerida"))
        resultado = {**resultado, "esperando_ubicacion": False}
        direccion_preguntada = True
    elif not es_cliente_nuevo and resultado.get("esperando_ubicacion"):
        # C) Cliente existente al que el LLM saltó a pedir ubicación sin
        #    haber preguntado ni confirmado la dirección habitual todavía.
        resultado = {
            **resultado,
            "esperando_ubicacion": False,
            "usa_direccion_habitual": False,
            "pedido_completo": False,
            "respuesta_sugerida": PREGUNTA_DIRECCION_HABITUAL,
        }
        direccion_preguntada = True

    ubicacion_recibida = draft_previo.get("ubicacion") is not None

    nuevo_draft = {
        "intencion": resultado.get("intencion"),
        "productos": resultado.get("productos", []),
        "aclaracion_pendiente": resultado.get("aclaracion_pendiente"),
        "usa_direccion_habitual": resultado.get("usa_direccion_habitual"),
        "direccion_texto": resultado.get("direccion_texto"),
        "notas": resultado.get("notas"),
        "ubicacion": draft_previo.get("ubicacion"),
        "direccion_preguntada": direccion_preguntada,
        "nombre_cliente": resultado.get("nombre_cliente"),
    }

    if resultado.get("esperando_ubicacion"):
        nuevo_draft["algo_mas_preguntado"] = False
        nuevo_draft["estado"] = "esperando_ubicacion"
        save_draft(phone, nuevo_draft)
        return resultado.get("respuesta_sugerida", MENSAJE_PEDIR_UBICACION)

    if resultado.get("pedido_completo"):
        # No depende de "respuesta_sugerida": el texto que se envía es el
        # resumen, así que aunque el LLM haya reabierto dirección/ubicación
        # por error más arriba, ese texto nunca llega al cliente en este caso.
        resumen = await construir_resumen_pedido(resultado.get("productos", []))
        nuevo_draft["algo_mas_preguntado"] = False
        nuevo_draft["estado"] = "esperando_confirmacion"
        nuevo_draft["resumen"] = resumen
        save_draft(phone, nuevo_draft)
        return resumen["texto_resumen"]

    # pedido_completo sigue en false: si productos/dirección/nombre ya
    # estaban resueltos y lo único pendiente era el paso "¿algo más?",
    # marcamos algo_mas_preguntado=true para que el backend le indique al
    # LLM, en el próximo turno, que el mensaje del cliente responde
    # únicamente a esa pregunta (ver SYSTEM_PROMPT en agent_service.py).
    algo_mas_pendiente = _pedido_listo_salvo_algo_mas(resultado, es_cliente_nuevo, ubicacion_recibida)
    nuevo_draft["algo_mas_preguntado"] = algo_mas_pendiente
    nuevo_draft["estado"] = "armando"
    save_draft(phone, nuevo_draft)

    respuesta_sugerida = resultado.get("respuesta_sugerida", "")
    if respuesta_a_sanear:
        # El LLM reabrió el tema de dirección/ubicación por error aunque ya
        # estaba resuelto (ver salvaguarda centralizada más arriba): no
        # dejamos pasar ese texto, lo reemplazamos por el que corresponde al
        # estado real del pedido en este punto.
        respuesta_sugerida = PREGUNTA_ALGO_MAS if algo_mas_pendiente else MENSAJE_CONTINUAR_PEDIDO_GENERICO

    return respuesta_sugerida


async def _confirmar_pedido(phone: str, draft: dict) -> str:
    async with SessionLocal() as session:
        try:
            result = await session.execute(select(Cliente).where(Cliente.telefono == phone))
            cliente = result.scalar_one_or_none()

            if cliente is None:
                nombre_cliente = draft.get("nombre_cliente")
                if not nombre_cliente:
                    draft["esperando_nombre"] = True
                    save_draft(phone, draft)
                    return MENSAJE_PEDIR_NOMBRE

                ubicacion_cliente = draft.get("ubicacion") or {}
                cliente = Cliente(
                    telefono=phone,
                    nombre=nombre_cliente,
                    latitud=ubicacion_cliente.get("latitud"),
                    longitud=ubicacion_cliente.get("longitud"),
                    activo=True,
                )
                session.add(cliente)
                await session.flush()

            resumen = draft.get("resumen") or {}
            lineas = resumen.get("lineas", [])

            nombres_productos = [linea["nombre"] for linea in lineas]
            result = await session.execute(
                select(Producto).where(Producto.nombre.in_(nombres_productos))
            )
            productos_bd = {p.nombre: p for p in result.scalars().all()}

            usa_direccion_habitual = bool(draft.get("usa_direccion_habitual"))
            ubicacion_pedido = draft.get("ubicacion") or {}

            pedido = Pedido(
                cliente_id=cliente.id,
                estado=EstadoPedido.PENDIENTE,
                direccion_despacho=None if usa_direccion_habitual else draft.get("direccion_texto"),
                latitud=None if usa_direccion_habitual else ubicacion_pedido.get("latitud"),
                longitud=None if usa_direccion_habitual else ubicacion_pedido.get("longitud"),
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
                        cantidad=linea["cantidad"],
                        precio_unitario=linea["precio_unitario"],
                    )
                )

            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("[order_flow] Falló la creación del pedido para phone=%s", phone)
            return MENSAJE_ERROR_PEDIDO

    clear_draft(phone)
    return (
        f"¡Pedido #{pedido.id} confirmado! Quedó pendiente de revisión, "
        "te contactaremos para coordinar la entrega."
    )


async def _es_cliente_nuevo(phone: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(select(Cliente).where(Cliente.telefono == phone))
        cliente = result.scalar_one_or_none()
    return cliente is None


async def procesar_mensaje(
    phone: str,
    message_type: str,
    message_text: str | None,
    location: dict | None,
) -> str:
    es_cliente_nuevo = await _es_cliente_nuevo(phone)
    draft = get_draft(phone)
    estado = draft.get("estado") if draft else None

    if draft is not None and estado == "esperando_confirmacion":
        if draft.get("esperando_nombre"):
            nombre_cliente = (message_text or "").strip()
            draft["nombre_cliente"] = nombre_cliente or draft.get("nombre_cliente")
            draft["esperando_nombre"] = False
            save_draft(phone, draft)
            return await _confirmar_pedido(phone, draft)

        texto_normalizado = (message_text or "").strip().lower()

        if texto_normalizado in CONFIRMACIONES:
            return await _confirmar_pedido(phone, draft)

        resultado = await _interpretar_con_debug(
            phone, message_text or "", es_cliente_nuevo, _contexto_desde_draft(draft)
        )
        return await _aplicar_resultado_llm(phone, resultado, draft, es_cliente_nuevo)

    if draft is not None and estado == "esperando_ubicacion":
        if message_type == "location":
            ubicacion = location or {}
            draft["ubicacion"] = {
                "latitud": ubicacion.get("latitude"),
                "longitud": ubicacion.get("longitude"),
            }

            # No calculamos pedido_completo aquí: dejamos que el LLM lo
            # decida (con el contexto ya actualizado, incluyendo
            # "ubicacion_recibida": true) para que respete el paso
            # obligatorio "¿algo más?" antes de completar el pedido, igual
            # que en cualquier otro turno de texto.
            resultado = await _interpretar_con_debug(
                phone,
                "[El cliente compartió su ubicación de WhatsApp]",
                es_cliente_nuevo,
                _contexto_desde_draft(draft),
            )
            return await _aplicar_resultado_llm(phone, resultado, draft, es_cliente_nuevo)

        save_draft(phone, draft)
        return MENSAJE_PEDIR_UBICACION

    resultado = await _interpretar_con_debug(
        phone, message_text or "", es_cliente_nuevo, _contexto_desde_draft(draft)
    )
    return await _aplicar_resultado_llm(phone, resultado, draft, es_cliente_nuevo)
