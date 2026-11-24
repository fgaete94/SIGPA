"""
Script temporal para validar dos reglas en código de order_flow (sin depender
de la intención que devuelva el LLM):

1. Rechazo puro ("no", "nada", "Hola, no quiero") sin nada en curso: cierre
   cortés, sin volver a preguntar por el producto (es_rechazo_puro,
   _cierre_por_rechazo). Tras la oferta del producto habitual el primer "no"
   la descarta (comportamiento de siempre) y el segundo cierra.
2. Petición explícita de hablar con una persona ("Derívame a ejecutivo",
   "Pásame con la ejecutiva"): aviso a la ejecutiva (plantilla
   aviso_ejecutiva_cliente o texto libre) antes del LLM, una sola vez por
   conversación y sin borrar el pedido en curso (pide_ejecutiva,
   _derivar_por_solicitud).

No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 5693330062x-3x
(dentro de 569333006xx; test_agradecimiento usa 01-09 y 90-99 de ese rango).
Al empezar y al terminar (también si falla), el script BORRA los clientes de
ese rango (con sus pedidos, detalles, productos habituales), los mensajes de
mensaje_whatsapp, la conversación y el borrador. No toca ningún otro cliente.
Las filas de auditoria no se borran (son el registro histórico).

Los casos de flujo normal llaman al LLM real. Nunca se envía nada a Meta: los
envíos (send_whatsapp_message, send_whatsapp_template) se reemplazan por
fakes que solo registran en mensaje_whatsapp, como el envío real; y
_post_a_meta se reemplaza por un centinela que falla la prueba si algo se
escapa.

Uso: python -m scripts.test_rechazo_derivacion
"""

import asyncio
import copy
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
from app.services.conversacion_bot_service import esta_activa, marcar_activa
from app.services.draft_store import get_draft
from app.services.horario_atencion import texto_horario_atencion
from app.services.mensaje_whatsapp_service import registrar_mensaje
from app.services.order_flow import procesar_mensaje

RANGO_TELEFONOS_SQL = r"^(56)?9333006[23]\d$"

PLANTILLA = "aviso_ejecutiva_cliente"
EJECUTIVA_PRUEBA = "56933300639"

CHILE = ZoneInfo("America/Santiago")
DENTRO = datetime(2026, 10, 8, 10, 0, tzinfo=CHILE)  # jueves 10:00
FUERA = datetime(2026, 10, 8, 21, 0, tzinfo=CHILE)  # jueves 21:00

HABITUAL = "Bidón 20L Recarga"

FRASE_RECHAZO_LLM = "te sugiero contactar a un ejecutivo"


def _cliente(n: int, nombre: str) -> dict:
    return {
        "telefono_bd": f"+569333006{n:02d}",
        "telefono": f"569333006{n:02d}",
        "nombre": nombre,
        "direccion": f"Av. Prueba {n}00, Viña del Mar",
    }


SIN_HISTORIAL = _cliente(20, "Felipe")
CON_HABITUAL = _cliente(21, "Hilda")
EJECUTIVA_DENTRO = _cliente(22, "Elsa")
EJECUTIVA_FUERA = _cliente(23, "Fabio")
CON_DRAFT = _cliente(24, "Dora")
CHAT_REAL = _cliente(25, "Felipe")
TEXTO_LIBRE = _cliente(26, "Tito")
CON_DRAFT_NO = _cliente(27, "Norma")
MEZCLADO = _cliente(28, "Mara")

CLIENTES = (SIN_HISTORIAL, CON_HABITUAL, EJECUTIVA_DENTRO, EJECUTIVA_FUERA, CON_DRAFT, CHAT_REAL, TEXTO_LIBRE, CON_DRAFT_NO, MEZCLADO)


def _cierre(nombre: str) -> str:
    return order_flow.MENSAJE_CIERRE_RECHAZO.format(nombre=f", {nombre}")


# --------------------------------------------------------------------------
# Fakes de envío (nada sale a Meta)
# --------------------------------------------------------------------------

plantillas: list[dict] = []  # avisos con plantilla
textos: list[tuple[str, str]] = []  # (to, mensaje) por order_flow y whatsapp.py
fugas: list[dict] = []  # llamadas que llegaron a _post_a_meta (no debería haber)
llamadas_llm: list[str] = []


async def _fake_template(to, template_name, language_code, body_params, texto_registro):
    plantillas.append({"to": to, "template_name": template_name, "body_params": list(body_params)})
    await registrar_mensaje(telefono=to, direccion="saliente", tipo="template", contenido=texto_registro,
                            meta_message_id="wamid.fake", estado="enviado")
    return {"messages": [{"id": "wamid.fake"}]}


async def _fake_text(to, message):
    textos.append((to, message))
    await registrar_mensaje(telefono=to, direccion="saliente", tipo="text", contenido=message,
                            meta_message_id="wamid.fake", estado="enviado")
    return {"messages": [{"id": "wamid.fake"}]}


async def _centinela_meta(to, payload, tipo, contenido):
    fugas.append({"to": to, "tipo": tipo})
    return None


_interpretar_real = order_flow._interpretar_con_debug


async def _interpretar_contado(phone, *args, **kwargs):
    llamadas_llm.append(phone)
    return await _interpretar_real(phone, *args, **kwargs)


def _instalar_fakes() -> None:
    notificacion_ejecutiva.send_whatsapp_template = _fake_template
    order_flow.send_whatsapp_message = _fake_text
    whatsapp_route.send_whatsapp_message = _fake_text
    whatsapp_client._post_a_meta = _centinela_meta
    order_flow._interpretar_con_debug = _interpretar_contado
    settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = PLANTILLA
    settings.EJECUTIVA_PHONE = EJECUTIVA_PRUEBA
    settings.META_WHATSAPP_TOKEN = ""


def _hora(ahora: datetime) -> None:
    order_flow._ahora = lambda: ahora


def _avisos_de(telefono: str) -> list[dict]:
    numero = f"+{telefono}"
    return [p for p in plantillas if p["body_params"][1] == numero]


def _textos_ejecutiva_de(telefono: str) -> list[str]:
    return [m for to, m in textos if to == EJECUTIVA_PRUEBA and f"+{telefono}" in m]


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar_rango() -> None:
    ids_sql = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    pedidos_sql = f"select id from pedido where cliente_id in ({ids_sql})"
    tel_sql = "regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    for sql in (
        f"delete from detalle_pedido where pedido_id in ({pedidos_sql})",
        f"delete from mensaje_whatsapp where pedido_id in ({pedidos_sql}) or cliente_id in ({ids_sql}) or {tel_sql}",
        f"delete from pedido where cliente_id in ({ids_sql})",
        f"delete from cliente_producto_habitual where cliente_id in ({ids_sql})",
        f"delete from cliente where id in ({ids_sql})",
        f"delete from conversacion_bot where {tel_sql}",
        f"delete from borrador_pedido where {tel_sql}",
    ):
        await _ejecutar(sql, rango=RANGO_TELEFONOS_SQL)
    for datos in CLIENTES:
        draft_store._drafts.pop(datos["telefono"], None)
        draft_store._persistidos.pop(datos["telefono"], None)
        draft_store._pendientes_borrar.discard(datos["telefono"])
        order_flow._derivaciones_avisadas.pop(datos["telefono"], None)


async def _contar_rango() -> dict[str, int]:
    tel = "regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    conteos = {}
    for tabla in ("cliente", "mensaje_whatsapp", "conversacion_bot", "borrador_pedido"):
        conteos[tabla] = (await _ejecutar(f"select count(*) from {tabla} where {tel}", rango=RANGO_TELEFONOS_SQL)).scalar()
    conteos["pedido"] = (
        await _ejecutar(f"select count(*) from pedido where cliente_id in (select id from cliente where {tel})", rango=RANGO_TELEFONOS_SQL)
    ).scalar()
    return conteos


async def _crear_cliente(datos: dict, habituales: list[str] = ()) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :lat, :lon) returning id",
        nombre=datos["nombre"], telefono=datos["telefono_bd"], direccion=datos["direccion"],
        lat=Decimal("-33.024500"), lon=Decimal("-71.551800"),
    )
    cliente_id = result.scalar_one()
    for nombre in habituales:
        await _ejecutar(
            "insert into cliente_producto_habitual (cliente_id, producto_id) select :cid, id from producto where nombre = :n",
            cid=cliente_id, n=nombre,
        )
    return cliente_id


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _nueva_conversacion(phone: str) -> None:
    """Como el recordatorio: abre la conversación y descarta el borrador."""
    await marcar_activa(phone)
    order_flow._derivaciones_avisadas.pop(phone, None)


async def _decir(phone: str, turno: str) -> str:
    print(f"  Cliente: {turno}")
    respuesta = await procesar_mensaje(phone, "text", turno, None)
    paso = (get_draft(phone) or {}).get("paso")
    print(f"  Bot [{paso}]: {respuesta}")
    return respuesta


_wamid = 0


async def _por_webhook(phone: str, turno: str) -> str:
    """Pasa por whatsapp.py (registro, presentación del asistente) como un
    mensaje real. Devuelve lo que se le envió al cliente."""
    global _wamid
    _wamid += 1
    antes = len(textos)
    print(f"  Cliente: {turno}")
    await whatsapp_route._procesar_mensaje_entrante(
        {"from": phone, "id": f"wamid.prueba-rechazo-{_wamid}", "type": "text", "text": {"body": turno}}
    )
    enviados = [m for to, m in textos[antes:] if to == phone]
    for m in enviados:
        print(f"  Bot: {m}")
    return "\n".join(enviados)


class Verificador:
    def __init__(self):
        self.resultados: list[tuple[str, bool, str]] = []
        self.caso = ""

    def check(self, condicion: bool, descripcion: str) -> None:
        self.resultados.append((self.caso, bool(condicion), descripcion))
        print(f"    [{'OK' if condicion else 'FALLA'}] {descripcion}")


def _no_pregunta_producto(texto: str) -> bool:
    return order_flow.PREGUNTA_PRODUCTO not in texto and "qué cantidad" not in texto.lower()


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_detectores(v: Verificador) -> None:
    v.caso = "detectores"
    print("\n=== Detectores (sin LLM) ===")
    for t in ("no", "no quiero", "nada", "no quiero nada", "no gracias", "por ahora no", "no necesito",
              "no requiero", "Hola , no quiero", "Hola, no quiero.", "No, gracias!", "no necesito recarga"):
        v.check(order_flow.es_rechazo_puro(t), f"rechazo puro: {t!r}")
    for t in ("no quiero 12, quiero 20", "no, mejor 3 de 20", "gracias", "hola", "no, agrega un bidón",
              "no hay nada?", "no quiero recarga, quiero nuevo"):
        v.check(not order_flow.es_rechazo_puro(t), f"NO es rechazo puro: {t!r}")
    for t in ("Derívame a ejecutivo", "Pásame con la ejecutiva", "quiero hablar con una persona",
              "quiero hablar con un humano", "quiero hablar con alguien", "ejecutiva",
              "necesito hablar con un ejecutivo", "me atiende alguien?", "Ejecutiva por favor",
              "comunícame con una persona"):
        v.check(order_flow.pide_ejecutiva(t), f"pide ejecutiva: {t!r}")
    for t in ("quiero 2 bidones", "pasa alguien mañana a dejar el agua?", "la ejecutiva me dijo que hay promo",
              "no quiero", "hola", "pasa una persona a dejar el agua"):
        v.check(not order_flow.pide_ejecutiva(t), f"NO pide ejecutiva: {t!r}")


async def caso_rechazo_simple(v: Verificador) -> None:
    v.caso = "rechazo simple"
    print("\n=== Rechazo puro simple ===")
    phone = SIN_HISTORIAL["telefono"]
    await _nueva_conversacion(phone)
    llm_antes = len(llamadas_llm)
    r = await _decir(phone, "no quiero")
    v.check(r == _cierre("Felipe"), "cierre cortés con el nombre")
    v.check(len(llamadas_llm) == llm_antes, "sin llamar al LLM")
    v.check(get_draft(phone) is None, "sin borrador")
    v.check(await esta_activa(phone), "la conversación sigue activa")


async def caso_rechazo_con_saludo(v: Verificador) -> None:
    v.caso = "rechazo con saludo"
    print("\n=== Rechazo con saludo ===")
    phone = SIN_HISTORIAL["telefono"]
    await _nueva_conversacion(phone)
    r = await _decir(phone, "Hola , no quiero")
    v.check(r == _cierre("Felipe"), "cierre cortés")
    v.check(_no_pregunta_producto(r), "no pregunta por el producto")


async def caso_tres_rechazos(v: Verificador) -> None:
    v.caso = "tres rechazos"
    print("\n=== Tres rechazos seguidos, luego gracias y pedido nuevo ===")
    phone = SIN_HISTORIAL["telefono"]
    await _nueva_conversacion(phone)
    llm_antes = len(llamadas_llm)
    for turno in ("Hola , no quiero", "Nada", "No quiero nada"):
        r = await _decir(phone, turno)
        v.check(r == _cierre("Felipe") and _no_pregunta_producto(r), f"{turno!r}: cierre, sin preguntar producto")
    v.check(len(llamadas_llm) == llm_antes, "ninguno pasó por el LLM")
    r = await _decir(phone, "gracias")
    v.check(r == order_flow.MENSAJE_AGRADECIMIENTO_SIN_BORRADOR, "tras el cierre, 'gracias' va por el agradecimiento puro")
    r = await _decir(phone, "quiero 2 bidones de 20 recarga")
    productos = (get_draft(phone) or {}).get("productos") or []
    v.check(any(p["nombre_producto"] == "Bidón 20L Recarga" and p["cantidad"] == 2 for p in productos),
            "un pedido nuevo después del cierre funciona normal")


async def caso_rechazo_mezclado(v: Verificador) -> None:
    v.caso = "rechazo mezclado"
    print("\n=== Rechazo mezclado: sigue el flujo normal ===")
    phone = MEZCLADO["telefono"]
    await _nueva_conversacion(phone)
    llm_antes = len(llamadas_llm)
    r = await _decir(phone, "no quiero 12, quiero 20")
    v.check(r != _cierre("Mara"), "'no quiero 12, quiero 20' no cierra")
    v.check(len(llamadas_llm) == llm_antes + 1, "pasó por el LLM (flujo normal)")
    await _nueva_conversacion(phone)
    llm_antes = len(llamadas_llm)
    r = await _decir(phone, "no, mejor 3 de 20")
    v.check(r != _cierre("Mara"), "'no, mejor 3 de 20' no cierra")
    v.check(len(llamadas_llm) == llm_antes + 1, "pasó por el LLM (flujo normal)")


async def caso_oferta_habitual(v: Verificador) -> None:
    v.caso = "oferta habitual"
    print("\n=== Rechazo tras la oferta del producto habitual ===")
    phone = CON_HABITUAL["telefono"]
    await _nueva_conversacion(phone)
    r = await _decir(phone, "Hola")
    v.check(bool((get_draft(phone) or {}).get("oferta_habitual")), "se ofreció el producto habitual")
    r = await _decir(phone, "no")
    v.check(r == f"Entendido. {order_flow.PREGUNTA_PRODUCTO}", "primer 'no': descarta la oferta (comportamiento de siempre)")
    r = await _decir(phone, "nada")
    v.check(r == _cierre("Hilda"), "segundo rechazo ('nada'): cierre")

    await _nueva_conversacion(phone)
    await _decir(phone, "Hola")
    r = await _decir(phone, "no quiero nada")
    v.check(r == f"Entendido. {order_flow.PREGUNTA_PRODUCTO}", "'no quiero nada' a la oferta: la descarta")
    r = await _decir(phone, "no quiero nada")
    v.check(r == _cierre("Hilda"), "segundo 'no quiero nada': cierre")


async def caso_no_con_draft(v: Verificador) -> None:
    v.caso = "no con draft"
    print("\n=== 'no' con un pedido en curso ===")
    phone = CON_DRAFT_NO["telefono"]
    await _nueva_conversacion(phone)
    await _decir(phone, "quiero 2 bidones de 20 recarga")
    for _ in range(4):
        paso = (get_draft(phone) or {}).get("paso")
        if paso == "algo_mas" or (get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION:
            break
        await _decir(phone, "sí")
    draft = get_draft(phone) or {}
    if draft.get("paso") == "algo_mas":
        r = await _decir(phone, "no")
        v.check(r != _cierre("Norma"), "'no' a '¿algo más?' no cierra")
    draft = get_draft(phone) or {}
    v.check(draft.get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION, "se llegó al resumen")
    r = await _decir(phone, "no")
    v.check(r == order_flow.MENSAJE_NO_CONFIRMADO, "'no' al resumen: pregunta qué cambiar (como siempre)")
    v.check(bool((get_draft(phone) or {}).get("productos")), "el borrador conserva los productos")


async def caso_ejecutiva_dentro(v: Verificador) -> None:
    v.caso = "ejecutiva dentro"
    print("\n=== Ejecutiva en horario: aviso una sola vez ===")
    phone = EJECUTIVA_DENTRO["telefono"]
    _hora(DENTRO)
    await _nueva_conversacion(phone)
    llm_antes = len(llamadas_llm)
    r = await _decir(phone, "Derívame a ejecutivo")
    v.check("ya le avisé a una ejecutiva" in r and order_flow.COMPLEMENTO_DERIVACION_EN_HORARIO in r,
            "confirma el aviso (en horario)")
    v.check(FRASE_RECHAZO_LLM not in r.lower(), "sin 'te sugiero contactar a un ejecutivo'")
    avisos = _avisos_de(phone)
    v.check(len(avisos) == 1, "send_whatsapp_template llamado exactamente una vez")
    v.check(avisos and avisos[0]["to"] == EJECUTIVA_PRUEBA and avisos[0]["template_name"] == PLANTILLA,
            "plantilla aviso_ejecutiva_cliente a EJECUTIVA_PHONE")
    v.check(avisos and avisos[0]["body_params"] == ["el cliente Elsa", f"+{phone}", "en horario de atención"],
            "parámetros: cliente, teléfono, en horario")
    r = await _decir(phone, "Pásame con la ejecutiva")
    v.check(r.startswith("Ya le avisé a una ejecutiva"), "la repetición responde que ya se avisó")
    v.check(len(_avisos_de(phone)) == 1, "sin reenviar el aviso")
    v.check(len(llamadas_llm) == llm_antes, "ninguna petición pasó por el LLM")
    # Reinicio del proceso (se pierde la memoria): la BD evita el reenvío.
    order_flow._derivaciones_avisadas.clear()
    draft_store._drafts.pop(phone, None)
    draft_store._persistidos.pop(phone, None)
    r = await _decir(phone, "quiero hablar con una persona")
    v.check(r.startswith("Ya le avisé") and len(_avisos_de(phone)) == 1, "tras un reinicio tampoco se reenvía (mensaje_whatsapp)")
    v.check(await esta_activa(phone), "la conversación sigue activa")
    # Conversación nueva (el recordatorio de otro día): se vuelve a avisar.
    # El aviso anterior se corre un día atrás para no depender del margen de
    # reloj entre la app y la BD (MARGEN_RELOJ_BD).
    await _ejecutar(
        "update mensaje_whatsapp set creado_en = creado_en - interval '1 day' where telefono = :t",
        t=EJECUTIVA_PRUEBA,
    )
    await _nueva_conversacion(phone)
    r = await _decir(phone, "me atiende alguien?")
    v.check(r.startswith("Listo, Elsa") and len(_avisos_de(phone)) == 2, "en una conversación nueva se avisa de nuevo")


async def caso_ejecutiva_fuera(v: Verificador) -> None:
    v.caso = "ejecutiva fuera"
    print("\n=== Ejecutiva fuera de horario ===")
    phone = EJECUTIVA_FUERA["telefono"]
    _hora(FUERA)
    await _nueva_conversacion(phone)
    r = await _decir(phone, "necesito hablar con un ejecutivo")
    v.check(texto_horario_atencion() in r and "ya le avisé a una ejecutiva" in r, "confirma el aviso e informa el horario")
    v.check(FRASE_RECHAZO_LLM not in r.lower(), "sin 'te sugiero contactar a un ejecutivo'")
    avisos = _avisos_de(phone)
    v.check(len(avisos) == 1 and avisos[0]["body_params"][2] == "fuera de horario", "aviso único marcado fuera de horario")
    r = await _decir(phone, "ejecutiva")
    v.check(r.startswith("Ya le avisé") and texto_horario_atencion() in r and len(_avisos_de(phone)) == 1,
            "repetición fuera de horario: ya se avisó, con el horario, sin reenviar")
    _hora(DENTRO)


async def caso_texto_libre(v: Verificador) -> None:
    v.caso = "texto libre"
    print("\n=== Sin plantilla configurada: texto libre, una vez ===")
    phone = TEXTO_LIBRE["telefono"]
    _hora(DENTRO)
    settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""
    try:
        await _nueva_conversacion(phone)
        await _decir(phone, "quiero hablar con un humano")
        await _decir(phone, "pásame con un humano")
        v.check(len(_avisos_de(phone)) == 0, "sin plantilla")
        enviados = _textos_ejecutiva_de(phone)
        v.check(len(enviados) == 1 and enviados[0].startswith("Hola, el cliente Tito"), "un solo texto libre a la ejecutiva")
    finally:
        settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = PLANTILLA


async def caso_draft_conservado(v: Verificador) -> None:
    v.caso = "draft conservado"
    print("\n=== Derivación con un pedido en curso ===")
    phone = CON_DRAFT["telefono"]
    _hora(DENTRO)
    await _nueva_conversacion(phone)
    await _decir(phone, "quiero 3 bidones de 20 recarga")
    antes = copy.deepcopy(get_draft(phone))
    v.check(bool((antes or {}).get("productos")), "hay un pedido en curso")
    r = await _decir(phone, "Pásame con la ejecutiva")
    v.check(order_flow.COMPLEMENTO_DERIVACION_CON_PEDIDO in r, "le dice que el pedido sigue guardado")
    v.check(get_draft(phone) == antes, "el borrador queda idéntico")
    v.check(len(_avisos_de(phone)) == 1, "aviso enviado una vez")
    r = await _decir(phone, "Pásame con la ejecutiva")
    v.check(get_draft(phone) == antes and len(_avisos_de(phone)) == 1, "la repetición tampoco toca el borrador ni reenvía")
    # El pedido se puede seguir.
    for _ in range(4):
        if (get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION:
            break
        paso = (get_draft(phone) or {}).get("paso")
        await _decir(phone, {"algo_mas": "no"}.get(paso, "sí"))
    v.check((get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION,
            "el pedido continúa hasta el resumen")


async def caso_chat_real(v: Verificador) -> None:
    v.caso = "chat real (webhook)"
    print("\n=== Chat real de producción, por whatsapp.py ===")
    phone = CHAT_REAL["telefono"]
    _hora(DENTRO)
    await _nueva_conversacion(phone)
    r1 = await _por_webhook(phone, "Hola , no quiero")
    v.check(_cierre("Felipe") in r1 and _no_pregunta_producto(r1), "'Hola , no quiero': cierre (con presentación)")
    r2 = await _por_webhook(phone, "Nada")
    v.check(r2 == _cierre("Felipe"), "'Nada': cierre")
    r3 = await _por_webhook(phone, "No quiero nada")
    v.check(r3 == _cierre("Felipe"), "'No quiero nada': cierre")
    r4 = await _por_webhook(phone, "Derívame a ejecutivo")
    v.check("ya le avisé a una ejecutiva" in r4 and FRASE_RECHAZO_LLM not in r4.lower(), "'Derívame a ejecutivo': deriva")
    r5 = await _por_webhook(phone, "Pásame con la ejecutiva")
    v.check(r5.startswith("Ya le avisé"), "'Pásame con la ejecutiva': ya se avisó")
    v.check(len(_avisos_de(phone)) == 1, "un solo aviso a la ejecutiva en todo el chat")


async def main() -> None:
    _instalar_fakes()
    v = Verificador()
    try:
        await _limpiar_rango()
        await _crear_cliente(SIN_HISTORIAL)
        await _crear_cliente(CON_HABITUAL, [HABITUAL])
        for datos in (EJECUTIVA_DENTRO, EJECUTIVA_FUERA, CON_DRAFT, CHAT_REAL, TEXTO_LIBRE, CON_DRAFT_NO, MEZCLADO):
            await _crear_cliente(datos)
        _hora(DENTRO)

        for caso in (caso_detectores, caso_rechazo_simple, caso_rechazo_con_saludo, caso_tres_rechazos,
                     caso_rechazo_mezclado, caso_oferta_habitual, caso_no_con_draft, caso_ejecutiva_dentro,
                     caso_ejecutiva_fuera, caso_texto_libre, caso_draft_conservado, caso_chat_real):
            try:
                await caso(v)
            except Exception as exc:  # un caso roto no impide correr los demás
                v.caso = caso.__name__
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
    casos: dict[str, list[bool]] = {}
    for caso, ok, _ in v.resultados:
        casos.setdefault(caso, []).append(ok)
    for caso, oks in casos.items():
        print(f"  {caso:28s} {sum(oks)}/{len(oks)} {'OK' if all(oks) else 'FALLA'}")
    fallas = [(c, d) for c, ok, d in v.resultados if not ok]
    print(f"\nTotal: {len(v.resultados) - len(fallas)}/{len(v.resultados)} OK")
    for c, d in fallas:
        print(f"  FALLA [{c}] {d}")
    if fallas:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
