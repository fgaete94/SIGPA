"""
Script temporal para validar las reglas en código de order_flow que corrigen
los bugs de la prueba de estrés por WhatsApp (sin depender de lo que
devuelva el LLM):

A. Cantidades inválidas: cero o negativas, fracciones y sobre la cota
   técnica de 10000 por línea. No entran al borrador y se responde una vez.
   Una cantidad grande pero válida (1000) sí entra (ticket pedido grande).
B. Restas relativas ("quítale 998", "mejor descuéntame 5 bidones").
C. El LLM no calcula: un texto suyo con montos se descarta y va el detalle
   armado en código.
D. Cancelar un pedido ya confirmado deriva a la ejecutiva (aviso único).
E. Cambio de dirección sin el aviso contradictorio, "nada más" a "¿algo
   más?", y referencias de ubicación ("la casa verde").
F. "lo mismo de la otra vez" con un pedido entregado dispara la oferta de
   repetir (#64).

No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 569333007xx.
Al empezar y al terminar (también si falla), el script BORRA de ese rango los
clientes (con sus pedidos, detalles, productos habituales y sus filas de
auditoria por ID exacto), los mensajes de mensaje_whatsapp, la conversación
y el borrador. No toca ningún otro cliente.

Los turnos de flujo normal llaman al LLM real. Nunca se envía nada a Meta: los
envíos (send_whatsapp_message, send_whatsapp_template) se reemplazan por fakes
que solo registran en mensaje_whatsapp, como el envío real, y _post_a_meta se
reemplaza por un centinela que falla la prueba si algo se escapa. n8n no se
llama.

Uso: python -m scripts.test_validacion_cantidades
"""

import asyncio
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.draft_store as draft_store
import app.services.notificacion_ejecutiva as notificacion_ejecutiva
import app.services.order_flow as order_flow
import app.services.whatsapp_client as whatsapp_client
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.conversacion_bot_service import marcar_activa
from app.services.draft_store import clear_draft, get_draft, save_draft
from app.services.mensaje_whatsapp_service import registrar_mensaje
from app.services.order_flow import procesar_mensaje

RANGO_TELEFONOS_SQL = r"^(56)?9333007\d{2}$"
EJECUTIVA_PRUEBA = "56933300799"

CHILE = ZoneInfo("America/Santiago")
DENTRO = datetime(2026, 10, 8, 10, 0, tzinfo=CHILE)  # jueves 10:00

B20R = "Bidón 20L Recarga"
B12R = "Bidón 12L Recarga"
DUSB = "Dispensador USB"


def _cliente(n: int, nombre: str) -> dict:
    return {
        "telefono_bd": f"+569333007{n:02d}",
        "telefono": f"569333007{n:02d}",
        "nombre": nombre,
        "direccion": f"Av. Prueba {n}00, Viña del Mar",
    }


ALTA = _cliente(1, "Alba")
NEGATIVA = _cliente(2, "Nelson")
FRACCION = _cliente(3, "Franco")
RESTA = _cliente(4, "Rebeca")
RESTA_VARIAS = _cliente(5, "Ramón")
MONTOS = _cliente(6, "Mónica")
CANCELA = _cliente(7, "Carla")
CANCELA_SIN_DRAFT = _cliente(8, "César")
DIRECCION = _cliente(9, "Diego")
NADA_MAS = _cliente(10, "Nadia")
REFERENCIA = _cliente(11, "René")
REPETIR = _cliente(12, "Rita")
PRIMER_MENSAJE = _cliente(13, "Pía")
TOPE_SUMA = _cliente(14, "Tomás")
# Cliente nuevo (sin fila en cliente) para el cambio de dirección.
NUEVO_DIRECCION = "56933300720"

CLIENTES = (
    ALTA, NEGATIVA, FRACCION, RESTA, RESTA_VARIAS, MONTOS, CANCELA, CANCELA_SIN_DRAFT,
    DIRECCION, NADA_MAS, REFERENCIA, REPETIR, PRIMER_MENSAJE, TOPE_SUMA,
)
TELEFONOS = [c["telefono"] for c in CLIENTES] + [NUEVO_DIRECCION]


# --------------------------------------------------------------------------
# Fakes de envío (nada sale a Meta) y conteo de avisos a la ejecutiva
# --------------------------------------------------------------------------

textos: list[tuple[str, str]] = []
fugas: list[dict] = []
avisos_ejecutiva: list[str] = []  # teléfono del cliente por cada _avisar_ejecutiva
_avisar_real = order_flow._avisar_ejecutiva


async def _fake_text(to, message):
    textos.append((to, message))
    await registrar_mensaje(telefono=to, direccion="saliente", tipo="text", contenido=message,
                            meta_message_id="wamid.fake", estado="enviado")
    return {"messages": [{"id": "wamid.fake"}]}


async def _fake_template(to, template_name, language_code, body_params, texto_registro):
    fugas.append({"to": to, "tipo": "template inesperada"})
    return None


async def _centinela_meta(to, payload, tipo, contenido):
    fugas.append({"to": to, "tipo": tipo})
    return None


async def _avisar_contado(phone, tipo_cliente, nombres, fuera_de_horario):
    avisos_ejecutiva.append(phone)
    return await _avisar_real(phone, tipo_cliente, nombres, fuera_de_horario)


def _instalar_fakes() -> None:
    notificacion_ejecutiva.send_whatsapp_template = _fake_template
    order_flow.send_whatsapp_message = _fake_text
    whatsapp_route.send_whatsapp_message = _fake_text
    whatsapp_client._post_a_meta = _centinela_meta
    order_flow._avisar_ejecutiva = _avisar_contado
    order_flow._ahora = lambda: DENTRO
    settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""  # aviso por texto libre (fake)
    settings.EJECUTIVA_PHONE = EJECUTIVA_PRUEBA
    settings.META_WHATSAPP_TOKEN = ""


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


_TEL = "regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
_IDS = f"select id from cliente where {_TEL}"
_PEDIDOS = f"select id from pedido where cliente_id in ({_IDS})"


async def _limpiar_rango() -> None:
    for sql in (
        f"delete from auditoria where (entidad = 'cliente' and entidad_id in ({_IDS}))"
        f" or (entidad = 'pedido' and entidad_id in ({_PEDIDOS}))",
        f"delete from detalle_pedido where pedido_id in ({_PEDIDOS})",
        f"delete from mensaje_whatsapp where pedido_id in ({_PEDIDOS}) or cliente_id in ({_IDS}) or {_TEL}",
        f"delete from pedido where cliente_id in ({_IDS})",
        f"delete from cliente_producto_habitual where cliente_id in ({_IDS})",
        f"delete from cliente where id in ({_IDS})",
        f"delete from conversacion_bot where {_TEL}",
        f"delete from borrador_pedido where {_TEL}",
    ):
        await _ejecutar(sql, rango=RANGO_TELEFONOS_SQL)
    for telefono in TELEFONOS + [EJECUTIVA_PRUEBA]:
        draft_store._drafts.pop(telefono, None)
        draft_store._persistidos.pop(telefono, None)
        draft_store._pendientes_borrar.discard(telefono)
        order_flow._derivaciones_avisadas.pop(telefono, None)


async def _contar_rango() -> dict[str, int]:
    conteos = {}
    for tabla in ("cliente", "mensaje_whatsapp", "conversacion_bot", "borrador_pedido"):
        conteos[tabla] = (
            await _ejecutar(f"select count(*) from {tabla} where {_TEL}", rango=RANGO_TELEFONOS_SQL)
        ).scalar()
    conteos["pedido"] = (await _ejecutar(f"select count(*) from pedido where cliente_id in ({_IDS})",
                                         rango=RANGO_TELEFONOS_SQL)).scalar()
    return conteos


async def _crear_cliente(datos: dict) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :lat, :lon) returning id",
        nombre=datos["nombre"], telefono=datos["telefono_bd"], direccion=datos["direccion"],
        lat=Decimal("-33.024500"), lon=Decimal("-71.551800"),
    )
    return result.scalar_one()


async def _crear_pedido_entregado(cliente_id: int, lineas: dict[str, int]) -> int:
    result = await _ejecutar(
        "insert into pedido (cliente_id, estado, direccion_despacho, total, creado_en) "
        "values (:cid, cast('entregado' as estado_pedido), 'Dirección de prueba', 0, now() - interval '3 days') "
        "returning id",
        cid=cliente_id,
    )
    pedido_id = result.scalar_one()
    for nombre, cantidad in lineas.items():
        await _ejecutar(
            "insert into detalle_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario) "
            "select :pid, id, :cantidad, precio_unitario from producto where nombre = :nombre",
            pid=pedido_id, cantidad=cantidad, nombre=nombre,
        )
    return pedido_id


async def _pedidos_de(telefono: str) -> list[dict]:
    rows = await _ejecutar(
        f"select p.id, p.direccion_despacho, c.direccion as direccion_cliente from pedido p "
        f"join cliente c on c.id = p.cliente_id where {_TEL.replace('telefono', 'c.telefono')} "
        "and p.estado = 'pendiente' order by p.id",
        rango=f"^(56)?{telefono[2:]}$",
    )
    return [dict(r._mapping) for r in rows]


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _nueva_conversacion(phone: str) -> None:
    await marcar_activa(phone)
    clear_draft(phone)
    order_flow._derivaciones_avisadas.pop(phone, None)


async def _decir(phone: str, turno: str) -> str:
    print(f"  Cliente: {turno}")
    respuesta = await procesar_mensaje(phone, "text", turno, None)
    paso = (get_draft(phone) or {}).get("paso")
    print(f"  Bot [{paso}]: {respuesta}")
    return respuesta


def _sembrar(phone: str, productos: list[tuple[str, int]], paso: str = "algo_mas") -> dict:
    """Borrador de un cliente existente con su dirección registrada ya
    confirmada, esperando la respuesta a `paso`."""
    lineas = [{"nombre_producto": nombre, "cantidad": cantidad} for nombre, cantidad in productos]
    draft = {
        "intencion": "pedido",
        "productos": lineas,
        "aclaracion_pendiente": None,
        "notas": None,
        "nombre_cliente": None,
        "usa_direccion_habitual": True,
        "direccion_texto": None,
        "ubicacion": None,
        "ubicacion_rechazada": False,
        "algo_mas_respondido": False,
        "pendientes_modelo": [],
        "no_encontrados": [],
        "paso": paso,
        "estado": "armando",
    }
    draft["unidades_pedidas"] = order_flow._unidades_draft(draft)
    save_draft(phone, draft)
    return draft


def _lineas(phone: str) -> dict[str, int]:
    return {p["nombre_producto"]: p["cantidad"] for p in (get_draft(phone) or {}).get("productos") or []}


def _pendientes(phone: str) -> list[int]:
    aclaracion = (get_draft(phone) or {}).get("aclaracion_pendiente")
    return [a.get("cantidad") for a in order_flow._lista_aclaraciones(aclaracion)]


class Verificador:
    def __init__(self):
        self.resultados: list[tuple[str, bool, str]] = []
        self.caso = ""

    def check(self, condicion: bool, descripcion: str) -> None:
        self.resultados.append((self.caso, bool(condicion), descripcion))
        print(f"    [{'OK' if condicion else 'FALLA'}] {descripcion}")


# --------------------------------------------------------------------------
# A. Cantidades inválidas
# --------------------------------------------------------------------------


async def caso_alta(v: Verificador) -> None:
    v.caso = "A1 1000 bidones"
    print("\n=== A1: 1000 bidones con un borrador en curso, luego '2 water jugs' ===")
    phone = ALTA["telefono"]
    await _nueva_conversacion(phone)
    await _decir(phone, "quiero 2 bidones de 20 recarga")
    v.check(_lineas(phone) == {B20R: 2}, "borrador inicial: 2x Bidón 20L Recarga")
    # Ticket pedido grande: una cantidad entera positiva siempre entra (el
    # umbral decide la derivación al confirmar, ver test_pedido_grande).
    r = await _decir(phone, "1000 bidones")
    v.check(r != order_flow.MENSAJE_CANTIDAD_ALTA, "1000 ya no se rechaza")
    todas = list(_lineas(phone).values()) + _pendientes(phone)
    v.check(1000 in todas, f"los 1000 entran al borrador (líneas {_lineas(phone)}, pendientes {_pendientes(phone)})")
    v.check(_lineas(phone).get(B20R) == 2, "la línea anterior sigue con 2")
    r = await _decir(phone, "2 water jugs")
    todas = list(_lineas(phone).values()) + _pendientes(phone)
    v.check(all(n <= order_flow.COTA_TECNICA_POR_LINEA for n in todas), "nada sobre la cota técnica")


async def caso_alta_primer_mensaje(v: Verificador) -> None:
    v.caso = "A1b cota técnica"
    print("\n=== A1b: cota técnica de 10000 por línea; '1.000' es mil ===")
    phone = PRIMER_MENSAJE["telefono"]
    await _nueva_conversacion(phone)
    r = await _decir(phone, "quiero 20000 bidones de 20 recarga")
    v.check(r == order_flow.MENSAJE_CANTIDAD_ALTA, "20000 (sobre la cota técnica): mensaje de cantidad alta")
    v.check(get_draft(phone) is None, "no se crea borrador")
    r = await _decir(phone, "1.000 bidones de 20 recarga")
    v.check(_lineas(phone).get(B20R) == 1000, "'1.000' (separador de miles) entra como 1000")
    r = await _decir(phone, "ejecutiva")
    v.check("ya le avisé a una ejecutiva" in r.lower(), "'ejecutiva' deriva")


async def caso_tope_por_suma(v: Verificador) -> None:
    v.caso = "A1c suma"
    print("\n=== A1c: 60 + 'agrega 50' = 110 entra; una suma sobre la cota técnica no ===")
    phone = TOPE_SUMA["telefono"]
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 60)])
    await _decir(phone, "agrega 50 bidones de 20 recarga")
    v.check(_lineas(phone) == {B20R: 110}, "60 + 50 = 110 (ya no se rechaza)")
    _sembrar(phone, [(B20R, 9000)])
    borrador_antes = dict(get_draft(phone) or {})
    r = await _decir(phone, "agrega 2000 bidones de 20 recarga")
    v.check(r == order_flow.MENSAJE_CANTIDAD_ALTA, "9000 + 2000 = 11000 supera la cota técnica: se rechaza")
    v.check(get_draft(phone) == borrador_antes, "el borrador queda intacto (9000)")


async def caso_negativa(v: Verificador) -> None:
    v.caso = "A2 cero/negativa"
    print("\n=== A2: '-3 bidones' y '0': se responde una vez y la conversación sigue ===")
    phone = NEGATIVA["telefono"]
    await _nueva_conversacion(phone)
    await _decir(phone, "quiero 2 bidones de 20 recarga")
    borrador_antes = dict(get_draft(phone) or {})
    r = await _decir(phone, "-3 bidones")
    v.check(r == order_flow.MENSAJE_CANTIDAD_NO_POSITIVA, "'-3 bidones': mensaje de número positivo (exacto)")
    v.check(get_draft(phone) == borrador_antes, "borrador intacto")
    r = await _decir(phone, "0")
    v.check(r == order_flow.MENSAJE_CANTIDAD_NO_POSITIVA, "'0': mismo mensaje")
    v.check(get_draft(phone) == borrador_antes, "borrador intacto")
    r = await _decir(phone, "sí")
    v.check(r != order_flow.MENSAJE_CANTIDAD_NO_POSITIVA and "positivo" not in r, "el turno siguiente sigue normal")
    for _ in range(3):
        if (get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION:
            break
        await _decir(phone, {"algo_mas": "no"}.get((get_draft(phone) or {}).get("paso"), "sí"))
    v.check((get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION,
            "el pedido llega al resumen sin repetir la pregunta")
    v.check(_lineas(phone) == {B20R: 2}, "con 2x Bidón 20L Recarga")


async def caso_fraccion(v: Verificador) -> None:
    v.caso = "A3 fracciones"
    print("\n=== A3: 'medio bidón', 'dos bidones y medio', '1.5', '1,5' ===")
    phone = FRACCION["telefono"]
    await _nueva_conversacion(phone)
    await _decir(phone, "quiero 2 bidones de 20 recarga")
    borrador_antes = dict(get_draft(phone) or {})
    for turno in ("medio bidón", "dos bidones y medio", "1.5", "1,5"):
        r = await _decir(phone, turno)
        v.check(r == order_flow.MENSAJE_SOLO_UNIDADES_COMPLETAS, f"{turno!r}: solo bidones completos (exacto)")
        v.check(get_draft(phone) == borrador_antes, f"{turno!r}: borrador intacto")


# --------------------------------------------------------------------------
# B. Restas relativas
# --------------------------------------------------------------------------


async def caso_resta(v: Verificador) -> None:
    v.caso = "B1 restas"
    print("\n=== B1: 1000 → 'quítale 998' = 2; 20 → 'mejor descuéntame 5 bidones' = 15 ===")
    phone = RESTA["telefono"]
    await _nueva_conversacion(phone)
    # Un borrador con 1000 (anterior al tope) solo se puede bajar.
    _sembrar(phone, [(B20R, 1000)])
    r = await _decir(phone, "quítale 998")
    v.check(_lineas(phone) == {B20R: 2}, "1000 - 998 = 2")
    v.check("quedan 2x Bidón 20L Recarga" in r, "confirma lo que quedó")

    await _nueva_conversacion(phone)
    await _decir(phone, "quiero 20 bidones de 20 recarga")
    v.check(_lineas(phone) == {B20R: 20}, "borrador con 20")
    r = await _decir(phone, "mejor descuéntame 5 bidones")
    v.check(_lineas(phone) == {B20R: 15}, "20 - 5 = 15")

    r = await _decir(phone, "quítale 20")
    v.check(_lineas(phone) == {}, "15 - 20: la línea se elimina, nunca negativa")
    v.check(r.startswith("Quité esa línea del pedido"), "aviso claro 'Quité esa línea del pedido'")

    # Las frases que FIJAN la cantidad siguen fijando.
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 20)], paso="algo_mas")
    await _decir(phone, "que sean 5 bidones")
    v.check(_lineas(phone) == {B20R: 5}, "'que sean 5 bidones' fija 5 (no resta)")


async def caso_resta_varias(v: Verificador) -> None:
    v.caso = "B2 varias líneas"
    print("\n=== B2: resta con varias líneas: pregunta cuál ===")
    phone = RESTA_VARIAS["telefono"]
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 3), (B12R, 2)])
    r = await _decir(phone, "quítale 1")
    v.check(r.startswith("¿A cuál producto le quito 1?"), "pregunta a cuál línea")
    v.check(_lineas(phone) == {B20R: 3, B12R: 2}, "no toca nada todavía")
    await _decir(phone, "a los de 20")
    v.check(_lineas(phone) == {B20R: 2, B12R: 2}, "resta 1 a la de 20L")
    r = await _decir(phone, "quítale 2 a los de 12")
    v.check(_lineas(phone) == {B20R: 2}, "2 - 2 = 0: quita la línea de 12L")
    v.check("Quité esa línea del pedido" in r, "avisa que quitó la línea")
    _sembrar(phone, [(B20R, 3), (DUSB, 1)])
    await _decir(phone, "quita el dispensador")
    v.check(_lineas(phone) == {B20R: 3}, "'quita el dispensador' (sin número) elimina la línea, como siempre")


# --------------------------------------------------------------------------
# C. El LLM no calcula
# --------------------------------------------------------------------------


async def caso_montos(v: Verificador) -> None:
    v.caso = "C montos del LLM"
    print("\n=== C: respuesta del LLM con montos: se descarta y va el detalle armado en código ===")
    phone = MONTOS["telefono"]
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 3)], paso="algo_mas")

    interpretar_real = order_flow._interpretar_con_debug

    async def llm_que_calcula(*args, **kwargs):
        resultado = await interpretar_real(*args, **kwargs)
        return {**resultado, "intencion": "duda_pedido", "productos": [], "correccion_propuesta": None,
                "respuesta_sugerida": "Serían 3 bidones a $2.000 c/u, total $6.000."}

    order_flow._interpretar_con_debug = llm_que_calcula
    try:
        r = await _decir(phone, "¿cuánto me sale todo?")
    finally:
        order_flow._interpretar_con_debug = interpretar_real
    v.check("$6.000" not in r and "c/u" not in r, "el texto del LLM con montos se descarta")
    v.check("Tu pedido hasta ahora" in r and "Total: $7.500" in r, "va el detalle armado en código (3 x $2.500)")
    v.check(_lineas(phone) == {B20R: 3}, "el borrador no cambia")

    # Con el LLM real: cualquier monto de la respuesta debe ser del cálculo en código.
    r = await _decir(phone, "y en total cuánto es?")
    montos = set(re.findall(r"\$[\d.]+", r))
    v.check(montos <= {"$2.500", "$7.500"}, f"montos de la respuesta real solo del código: {sorted(montos)}")
    for t in ("El total es $12.500", "Total: 3 bidones", "son 5.000 pesos"):
        v.check(order_flow._texto_con_montos(t), f"validador detecta {t!r}")
    v.check(not order_flow._texto_con_montos("¿Los quieres de 12L o de 20L?"), "validador no toca preguntas sin montos")


# --------------------------------------------------------------------------
# D. Cancelar un pedido confirmado
# --------------------------------------------------------------------------


async def caso_cancela_confirmado(v: Verificador) -> None:
    v.caso = "D cancelar confirmado"
    print("\n=== D: 'cancela el pedido #631' con un borrador en curso ===")
    phone = CANCELA["telefono"]
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 2)])
    borrador_antes = dict(get_draft(phone) or {})
    avisos_antes = avisos_ejecutiva.count(phone)
    esperado = ("Ya le avisé a una ejecutiva para que gestione la cancelación del pedido #631. "
                "Te contactará a la brevedad.")
    r = await _decir(phone, "cancela el pedido #631")
    v.check(r == esperado, "respuesta exacta con el número que escribió")
    v.check(get_draft(phone) == borrador_antes, "el borrador en curso no se borra")
    r = await _decir(phone, "Cancela el pedido #631")
    v.check(r == esperado, "repetido: misma respuesta")
    r = await _decir(phone, "quiero cancelar el pedido que hice")
    v.check("cancelación de tu pedido" in r, "'el pedido que hice': deriva sin número")
    v.check(get_draft(phone) == borrador_antes, "el borrador sigue intacto")
    v.check(avisos_ejecutiva.count(phone) - avisos_antes == 1, "el aviso a la ejecutiva sale exactamente una vez")
    v.check(sum(1 for to, m in textos if to == EJECUTIVA_PRUEBA and f"+{phone}" in m) == 1,
            "un solo mensaje a la ejecutiva (fake)")
    r = await _decir(phone, "cancela")
    v.check(r == order_flow.MENSAJE_PEDIDO_CANCELADO and get_draft(phone) is None,
            "'cancela' a secas sigue cancelando el borrador en curso")


async def caso_cancela_sin_draft(v: Verificador) -> None:
    v.caso = "D sin borrador"
    print("\n=== D: 'anula mi pedido' sin borrador ===")
    phone = CANCELA_SIN_DRAFT["telefono"]
    await _nueva_conversacion(phone)
    r = await _decir(phone, "anula mi pedido")
    v.check(r == order_flow.MENSAJE_CANCELACION_CONFIRMADO_SIN_NUMERO, "deriva la cancelación (sin número)")
    v.check(avisos_ejecutiva.count(phone) == 1, "un aviso a la ejecutiva")
    v.check(get_draft(phone) is None, "no crea borrador")


# --------------------------------------------------------------------------
# E. Menores
# --------------------------------------------------------------------------


async def _hasta_resumen(phone: str) -> None:
    for _ in range(5):
        if (get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION:
            return
        paso = (get_draft(phone) or {}).get("paso")
        await _decir(phone, {"algo_mas": "no", "ubicacion": "no puedo compartir la ubicación"}.get(paso, "sí"))


async def caso_direccion(v: Verificador) -> None:
    v.caso = "E1 cambio de dirección"
    print("\n=== E1: 'ok pero cámbiame la dirección a Los Leones 456' (cliente existente, tras el resumen) ===")
    phone = DIRECCION["telefono"]
    await _nueva_conversacion(phone)
    await _decir(phone, "quiero 2 bidones de 20 recarga")
    await _hasta_resumen(phone)
    r = await _decir(phone, "ok pero cámbiame la dirección a Los Leones 456")
    v.check("No pude aplicar el cambio" not in r, "sin el aviso contradictorio")
    v.check("Los Leones 456" in r, "confirma la dirección nueva")
    draft = get_draft(phone) or {}
    v.check(draft.get("direccion_texto") == "Los Leones 456" and draft.get("usa_direccion_habitual") is False,
            "el borrador quedó con la dirección nueva")
    await _hasta_resumen(phone)
    r = await _decir(phone, "si")
    pedidos = await _pedidos_de(phone)
    v.check(len(pedidos) == 1 and pedidos[0]["direccion_despacho"] == "Los Leones 456",
            "el pedido se despacha a Los Leones 456")

    print("\n=== E1b: lo mismo con un cliente nuevo (la dirección sí se aplicaba y decía que no) ===")
    phone = NUEVO_DIRECCION
    await _nueva_conversacion(phone)
    for turno in ("quiero 2 bidones de 20 recarga", "Ana Prueba", "Calle Uno 123, Viña del Mar",
                  "no puedo compartir la ubicación", "no"):
        await _decir(phone, turno)
    await _hasta_resumen(phone)
    r = await _decir(phone, "ok pero cámbiame la dirección a Los Leones 456")
    v.check("No pude aplicar el cambio" not in r and "Los Leones 456" in r, "cliente nuevo: confirma el cambio")
    v.check((get_draft(phone) or {}).get("direccion_texto") == "Los Leones 456", "borrador con la dirección nueva")


async def caso_nada_mas(v: Verificador) -> None:
    v.caso = "E2 nada más"
    print("\n=== E2: respuestas negativas a '¿Deseas agregar algo más?' ===")
    phone = NADA_MAS["telefono"]
    for turno in ("nada más", "eso es todo", "es todo", "no necesito nada más", "nada más gracias",
                  "eso es todo, gracias"):
        await _nueva_conversacion(phone)
        _sembrar(phone, [(B20R, 2)], paso="algo_mas")
        r = await _decir(phone, turno)
        v.check("Resumen de tu pedido" in r and order_flow.PREGUNTA_ALGO_MAS not in r,
                f"{turno!r}: pasa directo al resumen, sin volver a preguntar")


async def caso_referencia(v: Verificador) -> None:
    v.caso = "E3 referencia"
    print("\n=== E3: 'por el lado del supermercado, la casa verde' con un pedido en curso ===")
    phone = REFERENCIA["telefono"]
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 2)], paso="algo_mas")
    r = await _decir(phone, "por el lado del supermercado, la casa verde")
    v.check(r.startswith("Anotado: por el lado del supermercado, la casa verde."), "confirma 'Anotado: <texto>'")
    v.check("solo puedo ayudar" not in r.lower(), "no es fuera de tema")
    v.check((get_draft(phone) or {}).get("referencia_entrega") == "por el lado del supermercado, la casa verde",
            "queda en el borrador")
    for turno in ("tocar el timbre 2", "portón negro"):
        r = await _decir(phone, turno)
        v.check(r.startswith(f"Anotado: {turno}."), f"{turno!r}: anotado")
    r = await _decir(phone, "no")
    v.check("(por el lado del supermercado, la casa verde; tocar el timbre 2; portón negro)" in r,
            "el resumen muestra la referencia junto a la dirección")
    await _decir(phone, "si")
    pedidos = await _pedidos_de(phone)
    v.check(len(pedidos) == 1 and pedidos[0]["direccion_despacho"] == (
        f"{REFERENCIA['direccion']} (por el lado del supermercado, la casa verde; tocar el timbre 2; portón negro)"),
        "pedido.direccion_despacho lleva la referencia entre paréntesis")
    v.check(len(pedidos) == 1 and pedidos[0]["direccion_cliente"] == REFERENCIA["direccion"],
            "la dirección registrada del cliente no cambia")


# --------------------------------------------------------------------------
# F. "lo mismo de la otra vez"
# --------------------------------------------------------------------------


async def caso_repetir(v: Verificador, cliente_id: int) -> None:
    v.caso = "F lo mismo de la otra vez"
    print("\n=== F: 'lo mismo de la otra vez' con un pedido entregado ===")
    phone = REPETIR["telefono"]
    await _crear_pedido_entregado(cliente_id, {B20R: 3, DUSB: 1})
    await _nueva_conversacion(phone)
    r = await _decir(phone, "lo mismo de la otra vez")
    v.check("¿Quieres repetir tu último pedido" in r, "ofrece repetir el último pedido entregado")
    v.check("3x Bidón 20L Recarga" in r and "1x Dispensador USB" in r, "con sus líneas")
    await _decir(phone, "sí")
    v.check(_lineas(phone) == {B20R: 3, DUSB: 1}, "al aceptar, el borrador tiene las líneas del pedido entregado")


# --------------------------------------------------------------------------


async def main() -> None:
    _instalar_fakes()
    v = Verificador()
    try:
        await _limpiar_rango()
        ids = {}
        for datos in CLIENTES:
            ids[datos["telefono"]] = await _crear_cliente(datos)

        casos = [caso_alta, caso_alta_primer_mensaje, caso_tope_por_suma, caso_negativa, caso_fraccion,
                 caso_resta, caso_resta_varias, caso_montos, caso_cancela_confirmado, caso_cancela_sin_draft,
                 caso_direccion, caso_nada_mas, caso_referencia]
        for caso in casos:
            try:
                await caso(v)
            except Exception as exc:  # un caso roto no impide correr los demás
                v.caso = caso.__name__
                v.check(False, f"excepción: {exc!r}")
        try:
            await caso_repetir(v, ids[REPETIR["telefono"]])
        except Exception as exc:
            v.caso = "caso_repetir"
            v.check(False, f"excepción: {exc!r}")
        v.caso = "seguridad"
        v.check(not fugas, f"nada llegó a Meta ({len(fugas)} envíos interceptados)")
    finally:
        await _limpiar_rango()
        conteos = await _contar_rango()
        print(f"\nFilas de prueba tras la limpieza: {conteos}")
        v.caso = "limpieza"
        v.check(all(n == 0 for n in conteos.values()), "0 filas de prueba")

    print("\n=== Resumen por caso ===")
    casos_ok: dict[str, list[bool]] = {}
    for caso, ok, _ in v.resultados:
        casos_ok.setdefault(caso, []).append(ok)
    for caso, oks in casos_ok.items():
        print(f"  {caso:28s} {sum(oks)}/{len(oks)} {'OK' if all(oks) else 'FALLA'}")
    fallas = [(c, d) for c, ok, d in v.resultados if not ok]
    print(f"\nTotal: {len(v.resultados) - len(fallas)}/{len(v.resultados)} OK")
    for c, d in fallas:
        print(f"  FALLA [{c}] {d}")
    if fallas:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
