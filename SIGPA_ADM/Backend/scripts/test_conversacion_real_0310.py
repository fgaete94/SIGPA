"""
Script temporal que reproduce, a través del webhook y de procesar_mensaje y
contra la BD, la conversación real del 2026-10-03 (teléfono 56957721243,
pedido #29) y valida las correcciones de sus 4 bugs:

1. Un mismo mensaje entregado varias veces por Meta (mismo wamid) generaba
   varias respuestas: ahora se procesa una sola vez.
2. "4 bidones de 20" + "2 recargas y 2 nuevos" + "un dispensador usb"
   dejaba el pedido solo con el dispensador: ahora los productos se suman.
3. "no", "no", "canelar", "si" ante "¿Confirmas?" terminaba confirmando el
   pedido: ahora "no" pregunta qué cambiar, "canelar" cancela y un "si"
   tras un "no" no confirma.
4. "no cancelen mi pedido" no debe cancelar.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 56932100xxx.
Al empezar y al terminar, el script BORRA de ese rango los clientes (con sus
pedidos y detalles), los mensajes de mensaje_whatsapp y las filas de
conversacion_bot. No toca ningún otro cliente ni pedido. Las filas de
auditoria no se borran (son el registro histórico).

Nunca envía mensajes reales: el caso del webhook reemplaza
send_whatsapp_message por un fake, y los demás casos llaman directo a
procesar_mensaje (que devuelve el texto en vez de enviarlo).

Uso: python -m scripts.test_conversacion_real_0310
"""

import asyncio
import logging
import os
import random
import time
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.api.routes.whatsapp as whatsapp_route
from app.core.database import SessionLocal
from app.main import app
from app.services import webhook_queue
from app.services.conversacion_bot_service import marcar_activa
from app.services.mensaje_whatsapp_service import (
    INDICE_WAMID_ENTRANTE,
    registrar_mensaje_entrante,
)
from app.services.draft_store import clear_draft, get_draft, save_draft
from app.services.order_flow import (
    MENSAJE_CORRECCION_RECHAZADA,
    MENSAJE_NO_CONFIRMADO,
    MENSAJE_PEDIDO_CANCELADO,
    PREFIJO_REPETIR_RESUMEN,
    PREGUNTA_OPCIONES_PEDIDO,
    PREGUNTA_CANCELAR,
    _aplicar_resultado_llm,
    _cantidades_por_variante,
    _datos_cliente,
    _habla_del_pedido,
    _intencion_cancelar,
    _responder_siguiente_paso,
    procesar_mensaje,
)
from app.models import Cliente

RANGO_TELEFONOS_SQL = r"^(56)?932100\d{3}$"

CLIENTE = {
    "telefono": "56932100001",
    "nombre": "Felipe Prueba 0310",
    "direccion": "Santa Maria 793",
    "latitud": Decimal("-33.122910"),
    "longitud": Decimal("-71.570862"),
}

TELEFONO_WEBHOOK = "56932100002"

TELEFONOS_PRUEBA = (CLIENTE["telefono"], TELEFONO_WEBHOOK)

LINEAS_BIDONES = [
    {"nombre_producto": "Bidón 20L Recarga", "cantidad": 2},
    {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 2},
]
LINEAS_PEDIDO = [*LINEAS_BIDONES, {"nombre_producto": "Dispensador USB", "cantidad": 1}]


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
    await _ejecutar(
        f"delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id in ({ids_sql}))",
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from mensaje_whatsapp where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from conversacion_bot where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)


async def _crear_cliente() -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        **CLIENTE,
    )
    return result.scalar_one()


async def _pedidos_de(cliente_id: int) -> list[dict]:
    result = await _ejecutar("select * from pedido where cliente_id = :id order by id", id=cliente_id)
    return [dict(fila._mapping) for fila in result]


async def _detalles_de_cliente(cliente_id: int) -> list[dict]:
    result = await _ejecutar(
        "select d.*, p.nombre from detalle_pedido d join producto p on p.id = d.producto_id "
        "where d.pedido_id in (select id from pedido where cliente_id = :id) order by d.id",
        id=cliente_id,
    )
    return [dict(fila._mapping) for fila in result]


async def _precios() -> dict[str, Decimal]:
    result = await _ejecutar("select nombre, precio_unitario from producto")
    return {fila.nombre: fila.precio_unitario for fila in result}


async def _cliente_orm(cliente_id: int) -> Cliente:
    async with SessionLocal() as session:
        return await session.get(Cliente, cliente_id)


def _clp(valor) -> str:
    return f"${int(valor):,.0f}".replace(",", ".")


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _conversar(phone: str, turnos: list[str]) -> list[str]:
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        draft = get_draft(phone) or {}
        print(f"  Bot [{draft.get('paso')}/{draft.get('estado')}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


async def _preparar_resumen(ctx: dict, productos: list[dict] | None = None) -> str:
    """Deja el draft del cliente de prueba esperando confirmación del
    resumen (por defecto bidones + dispensador, dirección registrada), armado
    por el mismo código que usa la conversación (_responder_siguiente_paso)."""
    phone = CLIENTE["telefono"]
    draft = {
        "intencion": "pedido",
        "productos": [dict(item) for item in productos or LINEAS_PEDIDO],
        "aclaracion_pendiente": None,
        "notas": None,
        "nombre_cliente": None,
        "usa_direccion_habitual": True,
        "direccion_texto": None,
        "ubicacion": None,
        "ubicacion_rechazada": False,
        "algo_mas_respondido": True,
    }
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    paso, texto = await _responder_siguiente_paso(phone, draft, cliente)
    assert paso == "confirmacion", f"no se llegó al resumen: {paso} / {texto}"
    print(f"  [estado preparado] Bot: {texto}")
    return texto


def _es_resumen(texto: str) -> bool:
    return "Resumen de tu pedido" in texto and "¿Confirmas el pedido?" in texto


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


def _payload(phone: str, wamid: str, texto: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_TEST",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"profile": {"name": "Prueba"}, "wa_id": phone}],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": wamid,
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": texto},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


async def caso_a(ctx: dict, v: Verificador) -> None:
    phone = TELEFONO_WEBHOOK
    await marcar_activa(phone)
    enviados = []

    async def fake_send(to: str, message: str):
        enviados.append((to, message))
        print(f"  [fake send] a {to}: {message}")
        return {"messages": [{"id": f"wamid.fake-{len(enviados)}"}]}

    original = whatsapp_route.send_whatsapp_message
    whatsapp_route.send_whatsapp_message = fake_send
    try:
        wamid = f"wamid.test-0310-{uuid.uuid4().hex}"
        payload = _payload(phone, wamid, "hola")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def post():
                inicio = time.perf_counter()
                respuesta = await client.post("/webhook", json=payload)
                return respuesta.status_code, time.perf_counter() - inicio

            resultados = await asyncio.gather(post(), post(), post())
            print(f"  3 entregas del mismo wamid: {resultados}")
            v.check(all(status == 200 for status, _ in resultados), "las 3 entregas reciben 200")
            v.check(
                all(duracion < 1.0 for _, duracion in resultados),
                "el webhook responde de inmediato (< 1 s, sin esperar al LLM)",
            )

            await webhook_queue.esperar_pendientes()
            v.check(len(enviados) == 1, f"se envía una sola respuesta (enviadas: {len(enviados)})")
            filas = await _ejecutar(
                "select count(*) from mensaje_whatsapp where meta_message_id = :w", w=wamid
            )
            v.check(filas.scalar_one() == 1, "el mensaje entrante queda registrado una sola vez, con su wamid")

            # Reintento de Meta después de un reinicio del proceso (se pierde
            # el registro en memoria): lo descarta la BD.
            webhook_queue.olvidar_wamids()
            status, _ = await post()
            await webhook_queue.esperar_pendientes()
            v.check(status == 200 and len(enviados) == 1, "tras un 'reinicio', el reintento tampoco se procesa (dedup en BD)")
    finally:
        whatsapp_route.send_whatsapp_message = original
        clear_draft(phone)

    # Orden por teléfono: tareas del mismo teléfono en orden de llegada y sin
    # solaparse, aunque cada una tarde distinto.
    orden, activas, solapadas = [], set(), []

    def tarea(phone_tarea: str, n: int):
        async def correr():
            if phone_tarea in activas:
                solapadas.append(n)
            activas.add(phone_tarea)
            await asyncio.sleep(random.uniform(0, 0.05))
            orden.append((phone_tarea, n))
            activas.discard(phone_tarea)
        return correr

    for n in range(6):
        webhook_queue.encolar("cola-a", tarea("cola-a", n))
        webhook_queue.encolar("cola-b", tarea("cola-b", n))
    await webhook_queue.esperar_pendientes()
    v.check([n for p, n in orden if p == "cola-a"] == list(range(6)), "cola por teléfono respeta el orden de llegada")
    v.check(not solapadas, "nunca se procesan dos mensajes del mismo teléfono en paralelo")


async def caso_b(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(phone, ["quiero 4 bidones de 20"])
    draft = get_draft(phone) or {}
    v.check(
        draft.get("aclaracion_pendiente") == {"capacidad_litros": 20, "cantidad": 4},
        "queda pendiente '¿nuevo o recarga?' por 4 bidones de 20L",
    )
    v.check(draft.get("paso") == "producto", "sigue en el paso 'producto'")

    respuestas += await _conversar(phone, ["2 recargas y 2 nuevos"])
    draft = get_draft(phone) or {}
    v.check(draft.get("productos") == LINEAS_BIDONES, "draft con dos líneas: 2x Bidón 20L Recarga y 2x Bidón 20L Nuevo")
    v.check(draft.get("aclaracion_pendiente") is None, "sin aclaración pendiente")
    v.check(
        draft.get("paso") == "confirmar_direccion" and CLIENTE["direccion"] in respuestas[-1],
        "avanza a confirmar la dirección registrada",
    )


async def caso_c(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    # Los turnos de b se repiten porque cada caso parte con el draft limpio.
    await _conversar(phone, ["quiero 4 bidones de 20", "2 recargas y 2 nuevos"])
    respuestas = await _conversar(phone, ["si", "si, un dispensador usb", "no"])
    draft = get_draft(phone) or {}
    precios = ctx["precios"]
    total = sum(precios[item["nombre_producto"]] * item["cantidad"] for item in LINEAS_PEDIDO)
    resumen = respuestas[-1]
    v.check("algo más" in respuestas[0].lower(), "tras confirmar la dirección pregunta '¿algo más?'")
    v.check(draft.get("productos") == LINEAS_PEDIDO, "draft con bidones + dispensador (nada se perdió)")
    v.check(_es_resumen(resumen), "muestra el resumen")
    v.check(
        all(f"{item['cantidad']}x {item['nombre_producto']}" in resumen for item in LINEAS_PEDIDO),
        "el resumen incluye las 3 líneas",
    )
    v.check(f"Total: {_clp(total)}" in resumen, f"total correcto ({_clp(total)})")
    v.check(draft.get("estado") == "esperando_confirmacion", "estado esperando_confirmacion")
    clear_draft(phone)


async def caso_d(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no"])
    v.check(respuestas[0] == MENSAJE_NO_CONFIRMADO, "responde 'Entendido, no lo confirmo. ¿Qué quieres cambiar...?'")
    v.check(not _es_resumen(respuestas[0]), "no repite el resumen")
    v.check((get_draft(phone) or {}).get("estado") == "esperando_modificacion", "estado esperando_modificacion")
    v.check((get_draft(phone) or {}).get("productos") == LINEAS_PEDIDO, "el pedido en curso se conserva")
    clear_draft(phone)


async def caso_e(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no", "no", "canelar"])
    v.check(respuestas[0] == MENSAJE_NO_CONFIRMADO, "1er 'no': pregunta qué cambiar o si cancela")
    v.check(respuestas[1] == PREGUNTA_CANCELAR, "2do 'no': pregunta si quiere CANCELAR")
    v.check(respuestas[2] == MENSAJE_PEDIDO_CANCELADO, "'canelar' (errata) cancela el pedido")
    v.check(get_draft(phone) is None, "draft vaciado por completo")
    respuestas += await _conversar(phone, ["si"])
    v.check("confirmado" not in respuestas[3].lower(), "el 'si' final no confirma nada")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no queda ningún pedido del cliente")
    v.check(not await _detalles_de_cliente(ctx["cliente_id"]), "no queda ningún detalle_pedido del cliente")
    clear_draft(phone)


async def caso_f(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no", "si"])
    v.check(respuestas[0] == MENSAJE_NO_CONFIRMADO, "'no' pregunta qué cambiar")
    v.check(
        respuestas[1].startswith(PREFIJO_REPETIR_RESUMEN) and _es_resumen(respuestas[1]),
        "'si' tras el 'no' vuelve a mostrar el resumen y pide confirmar",
    )
    v.check("confirmado" not in respuestas[1].lower(), "ese 'si' no confirma")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no se creó pedido")
    v.check((get_draft(phone) or {}).get("estado") == "esperando_confirmacion", "queda esperando confirmación explícita")
    clear_draft(phone)


async def caso_g(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no cancelen mi pedido"])
    draft = get_draft(phone)
    v.check(respuestas[0] != MENSAJE_PEDIDO_CANCELADO and respuestas[0] != PREGUNTA_CANCELAR, "no cancela ni pregunta si cancelar")
    v.check(draft is not None and draft.get("productos") == LINEAS_PEDIDO, "el pedido en curso se conserva")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no confirma el pedido")
    clear_draft(phone)


async def caso_h(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(
        phone, ["quiero 4 bidones de 20", "2 recargas y 2 nuevos", "si", "si, un dispensador usb", "no"]
    )
    v.check(_es_resumen(respuestas[-1]), "llega al resumen")
    respuestas += await _conversar(phone, ["si"])
    pedidos = await _pedidos_de(ctx["cliente_id"])
    v.check(len(pedidos) == 1 and "confirmado" in respuestas[-1].lower(), "'si' al resumen crea el pedido")
    detalles = await _detalles_de_cliente(ctx["cliente_id"])
    v.check(
        sorted((d["nombre"], d["cantidad_solicitada"]) for d in detalles)
        == sorted((item["nombre_producto"], item["cantidad"]) for item in LINEAS_PEDIDO),
        "detalle_pedido con las 3 líneas y sus cantidades",
    )
    precios = ctx["precios"]
    total = sum(precios[item["nombre_producto"]] * item["cantidad"] for item in LINEAS_PEDIDO)
    v.check(bool(pedidos) and pedidos[0]["total"] == total, f"total del pedido {_clp(total)}")
    v.check(get_draft(phone) is None, "draft limpio tras confirmar")
    # Limpia el pedido de prueba para que los casos siguientes partan sin pedidos.
    await _ejecutar(
        "delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id = :id)",
        id=ctx["cliente_id"],
    )
    await _ejecutar("delete from pedido where cliente_id = :id", id=ctx["cliente_id"])


async def caso_i(ctx: dict, v: Verificador) -> None:
    """Salvaguarda: si el draft tiene menos productos de los que el cliente
    pidió (lo que pasó con el pedido #29), no se muestra el resumen."""
    phone = CLIENTE["telefono"]
    draft = {
        "intencion": "pedido",
        "productos": [{"nombre_producto": "Dispensador USB", "cantidad": 1}],
        "aclaracion_pendiente": None,
        "usa_direccion_habitual": True,
        "algo_mas_respondido": True,
        "unidades_pedidas": {"Bidón": 4, "Dispensador": 1},
    }
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    paso, texto = await _responder_siguiente_paso(phone, draft, cliente)
    print(f"  Bot [{paso}]: {texto}")
    v.check(paso != "confirmacion" and not _es_resumen(texto), "no muestra el resumen incompleto")
    v.check("4x Bidón" in texto, "le dice qué productos no quedaron registrados")
    respuestas = await _conversar(phone, ["no"])
    v.check(_es_resumen(respuestas[0]), "si responde que no quiere agregar nada, recién ahí muestra el resumen")
    clear_draft(phone)


async def caso_j(ctx: dict, v: Verificador) -> None:
    """Detección determinista (sin LLM) de cancelación y de variantes."""
    esperados_cancelar = {
        "cancelar": "cancelar",
        "CANCELAR": "cancelar",
        "canelar": "cancelar",
        "anula el pedido": "cancelar",
        "no, cancela": "cancelar",
        "no cancelen mi pedido": None,
        "no me lo cancelen": None,
        "nunca anulen mi pedido": None,
        "no puedo compartirla desde este celular": None,
        "cancela el dispensador": "duda",
        "oye por favor canelar todo lo que te pedi": "duda",
        "hola": None,
    }
    for mensaje, esperado in esperados_cancelar.items():
        v.check(_intencion_cancelar(mensaje) == esperado, f"cancelación {mensaje!r} -> {esperado}")
    v.check(_intencion_cancelar("Los Canelos 345", texto_libre=True) is None, "'Los Canelos 345' en paso dirección no cancela")
    v.check(_intencion_cancelar("Manuela", texto_libre=True) is None, "'Manuela' en paso nombre no cancela")
    esperados_variantes = {
        "2 recargas y 2 nuevos": {"Recarga": 2, "Nuevo": 2},
        "dos nuevos y dos recargas": {"Nuevo": 2, "Recarga": 2},
        "1 nuevo y el resto recarga": {"Nuevo": 1, "Recarga": None},
        "3 de 20 recarga": {"Recarga": 3},
        "recarga": {"Recarga": None},
    }
    for mensaje, esperado in esperados_variantes.items():
        v.check(_cantidades_por_variante(mensaje, 20) == esperado, f"variantes {mensaje!r} -> {esperado}")
    v.check(
        _cantidades_por_variante("necesito 2 bidones de 20 litros recarga y 1 de 12 nuevo", 20) == {"Recarga": 2},
        "'1 de 12 nuevo' no se cuenta como 12 bidones de 20L",
    )

    # Un pedido en curso + "2 recargas" de 4 pendientes: no avanza a la
    # dirección hasta resolver los 2 que faltan.
    phone = CLIENTE["telefono"]
    save_draft(
        phone,
        {
            "intencion": "pedido",
            "productos": [],
            "aclaracion_pendiente": {"capacidad_litros": 20, "cantidad": 4},
            "paso": "producto",
            "estado": "armando",
        },
    )
    await _conversar(phone, ["2 recargas"])
    draft = get_draft(phone) or {}
    v.check(
        draft.get("productos") == [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 2}]
        and draft.get("aclaracion_pendiente") == {"capacidad_litros": 20, "cantidad": 2}
        and draft.get("paso") == "producto",
        "con 2 de 4 resueltos, sigue preguntando por los 2 restantes",
    )
    clear_draft(phone)


class _CapturaErrores(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.registros = []

    def emit(self, record):
        self.registros.append(record)


async def caso_k(ctx: dict, v: Verificador) -> None:
    """El índice único uq_mensaje_whatsapp_entrante_meta_message_id: una
    violación es "mensaje duplicado" (no se procesa, no se responde, 200),
    nunca un error."""
    phone = TELEFONO_WEBHOOK

    # 1. El índice existe y rechaza el mismo wamid entrante dos veces.
    wamid_sql = f"wamid.test-0310-sql-{uuid.uuid4().hex}"
    insertar = (
        "insert into mensaje_whatsapp (telefono, meta_message_id, direccion, tipo, contenido, estado) "
        "values (:t, :w, 'entrante', 'text', 'hola', 'recibido')"
    )
    await _ejecutar(insertar, t=phone, w=wamid_sql)
    try:
        await _ejecutar(insertar, t=phone, w=wamid_sql)
        violacion = None
    except IntegrityError as exc:
        violacion = str(exc.orig)
    v.check(
        violacion is not None and INDICE_WAMID_ENTRANTE in violacion,
        "insertar dos veces el mismo wamid entrante viola el índice único",
    )

    # 2. registrar_mensaje_entrante traduce esa violación a "duplicado".
    captura = _CapturaErrores()
    logging.getLogger("app").addHandler(captura)
    try:
        wamid = f"wamid.test-0310-dup-{uuid.uuid4().hex}"
        primero = await registrar_mensaje_entrante(phone, "text", "hola", wamid)
        segundo = await registrar_mensaje_entrante(phone, "text", "hola", wamid)
        v.check(primero is True and segundo is False, "1ra vez se procesa, 2da es duplicado")

        wamid_carrera = f"wamid.test-0310-race-{uuid.uuid4().hex}"
        resultados = await asyncio.gather(
            *(registrar_mensaje_entrante(phone, "text", "hola", wamid_carrera) for _ in range(3))
        )
        v.check(sorted(resultados) == [False, False, True], "3 registros simultáneos: solo uno se procesa")

        # 3. Por el webhook, con el registro en memoria perdido (reinicio):
        # 200, sin procesar ni responder.
        enviados, procesados = [], []

        async def fake_send(to: str, message: str):
            enviados.append((to, message))

        async def espia_procesar(**kwargs):
            procesados.append(kwargs)
            return "no debería llamarse"

        originales = (whatsapp_route.send_whatsapp_message, whatsapp_route.procesar_mensaje)
        whatsapp_route.send_whatsapp_message = fake_send
        whatsapp_route.procesar_mensaje = espia_procesar
        try:
            webhook_queue.olvidar_wamids()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                respuesta = await client.post("/webhook", json=_payload(phone, wamid, "hola"))
            await webhook_queue.esperar_pendientes()
        finally:
            whatsapp_route.send_whatsapp_message, whatsapp_route.procesar_mensaje = originales

        v.check(respuesta.status_code == 200, "el webhook devuelve 200")
        v.check(not procesados and not enviados, "no procesa ni responde el duplicado")
        filas = await _ejecutar("select count(*) from mensaje_whatsapp where meta_message_id = :w", w=wamid)
        v.check(filas.scalar_one() == 1, "sigue habiendo una sola fila con ese wamid")
    finally:
        logging.getLogger("app").removeHandler(captura)
    v.check(not captura.registros, "ningún error logueado (la violación no se trata como falla)")


# --------------------------------------------------------------------------
# Casos de la 2da prueba real del 2026-10-03 (17:42 hora Chile): capacidad
# supuesta, dispensador sin modelo descartado y productos fuera del catálogo.
# --------------------------------------------------------------------------


def _pregunta_capacidad(texto: str) -> bool:
    return "12L" in texto and "20L" in texto and "nuevo" not in texto.lower()


async def caso_l(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(phone, ["quiero 5 bidones"])
    draft = get_draft(phone) or {}
    v.check(_pregunta_capacidad(respuestas[0]), "pregunta '¿de 12L o de 20L?' (y todavía no nuevo/recarga)")
    v.check(not draft.get("productos"), "no agrega ningún bidón (no asume capacidad)")
    v.check(
        draft.get("aclaracion_pendiente", {}).get("capacidad_litros") is None
        and draft.get("aclaracion_pendiente", {}).get("cantidad") == 5,
        "queda pendiente: 5 bidones, capacidad sin definir",
    )
    v.check(draft.get("paso") == "producto", "no avanza del paso 'producto'")
    clear_draft(phone)


async def caso_m(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    precios = ctx["precios"]
    respuestas = await _conversar(phone, ["quiero un dispensador"])
    draft = get_draft(phone) or {}
    v.check(
        all(f"{nombre}: {_clp(precios[nombre])}" in respuestas[0] for nombre in ("Dispensador Básico", "Dispensador USB")),
        "pregunta cuál dispensador, listando los modelos con su precio",
    )
    v.check(not draft.get("productos"), "no elige un modelo por su cuenta")
    v.check(
        [p["familia"] for p in draft.get("pendientes_modelo") or []] == ["Dispensador"],
        "queda un dispensador pendiente de modelo",
    )
    respuestas += await _conversar(phone, ["el básico"])
    draft = get_draft(phone) or {}
    v.check(
        draft.get("productos") == [{"nombre_producto": "Dispensador Básico", "cantidad": 1}]
        and not draft.get("pendientes_modelo"),
        "'el básico' lo resuelve: 1x Dispensador Básico",
    )
    clear_draft(phone)

    # Sin LLM: si el LLM elige un modelo que el cliente no dijo, se rechaza y
    # queda pendiente igual.
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    resultado = {
        "intencion": "pedido",
        "productos": [{"nombre_producto": "Dispensador USB", "cantidad": 1, "operacion": "agregar"}],
        "respuesta_sugerida": "¡Perfecto! ¿Deseas agregar algo más?",
    }
    texto = await _aplicar_resultado_llm(phone, resultado, None, cliente, "dispensador")
    draft = get_draft(phone) or {}
    print(f"  [LLM simulado elige USB] Bot: {texto}")
    v.check(
        not draft.get("productos") and draft.get("pendientes_modelo"),
        "un 'Dispensador USB' supuesto por el LLM se rechaza y queda pendiente",
    )
    clear_draft(phone)

    # Una consulta de precio menciona el producto pero no lo pide.
    resultado = {
        "intencion": "consulta_precio",
        "productos": [],
        "producto_consultado": "Dispensador Básico",
        "respuesta_sugerida": 'El precio de "Dispensador Básico" es $7.000.',
    }
    await _aplicar_resultado_llm(phone, resultado, None, cliente, "¿cuánto cuesta el dispensador?")
    v.check(not (get_draft(phone) or {}).get("pendientes_modelo"), "'¿cuánto cuesta el dispensador?' no deja nada pendiente")
    clear_draft(phone)


async def caso_n(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(phone, ["quiero un bidón de 5 litros"])
    draft = get_draft(phone) or {}
    v.check("No encontré bidones de 5L" in respuestas[0], "avisa que no hay bidones de 5L")
    v.check(_pregunta_capacidad(respuestas[0]), "y pregunta de cuál capacidad del catálogo")
    v.check(draft.get("paso") == "producto" and not draft.get("productos"), "no avanza ni agrega nada")
    clear_draft(phone)

    # Sin LLM: un nombre fuera del catálogo devuelto por el LLM, con un pedido
    # en curso en "¿algo más?", no se descarta en silencio ni avanza.
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    draft_previo = {
        "intencion": "pedido",
        "productos": [dict(item) for item in LINEAS_BIDONES],
        "usa_direccion_habitual": True,
        "algo_mas_respondido": False,
        "paso": "algo_mas",
        "estado": "armando",
        "unidades_pedidas": {"Bidón": 4},
    }
    resultado = {
        "intencion": "pedido",
        "productos": [{"nombre_producto": "Agua Mineral 1.5L", "cantidad": 2, "operacion": "agregar"}],
        "pedido_completo": True,
        "respuesta_sugerida": "¡Listo!",
    }
    texto = await _aplicar_resultado_llm(phone, resultado, draft_previo, cliente, "y 2 aguas minerales de 1.5")
    draft = get_draft(phone) or {}
    print(f"  [LLM simulado con producto inexistente] Bot: {texto}")
    v.check("No encontré «Agua Mineral 1.5L»" in texto, "dice qué producto no encontró")
    v.check(
        not _es_resumen(texto) and "algo más" not in texto.lower() and draft.get("paso") == "producto",
        "no avanza a '¿algo más?' ni al resumen",
    )
    v.check(draft.get("productos") == LINEAS_BIDONES, "conserva el pedido en curso")
    clear_draft(phone)


async def caso_o(ctx: dict, v: Verificador) -> None:
    """La conversación real completa, con las respuestas que pide el bot
    corregido (capacidad y modelo de dispensador)."""
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(
        phone,
        ["hola", "quiero 5 bidones", "3 recarga y 2 nuevos", "20", "si", "si", "dispensador", "el usb", "no"],
    )
    esperado = [
        {"nombre_producto": "Bidón 20L Recarga", "cantidad": 3},
        {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 2},
        {"nombre_producto": "Dispensador USB", "cantidad": 1},
    ]
    precios = ctx["precios"]
    total = sum(precios[item["nombre_producto"]] * item["cantidad"] for item in esperado)
    v.check(_pregunta_capacidad(respuestas[1]), "'quiero 5 bidones' pregunta la capacidad")
    v.check(_pregunta_capacidad(respuestas[2]), "'3 recarga y 2 nuevos' sin capacidad: la vuelve a preguntar")
    v.check(CLIENTE["direccion"] in respuestas[3], "'20' completa los bidones y pasa a la dirección")
    v.check("Dispensador Básico" in respuestas[6] and "Dispensador USB" in respuestas[6], "'dispensador' pregunta cuál")
    resumen = respuestas[-1]
    v.check(_es_resumen(resumen), "llega al resumen")
    v.check(
        all(f"{item['cantidad']}x {item['nombre_producto']}" in resumen for item in esperado)
        and "12L" not in resumen,
        "resumen con 3x 20L Recarga, 2x 20L Nuevo y 1x Dispensador USB (nada de 12L)",
    )
    v.check(f"Total: {_clp(total)}" in resumen, f"total correcto ({_clp(total)})")
    respuestas += await _conversar(phone, ["no", "cancelar"])
    v.check(respuestas[-2] == MENSAJE_NO_CONFIRMADO, "'no' ante '¿Confirmas?' pregunta qué cambiar")
    v.check(respuestas[-1] == MENSAJE_PEDIDO_CANCELADO and get_draft(phone) is None, "'cancelar' cancela")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no se creó pedido")


# --------------------------------------------------------------------------
# Casos de la 3ra prueba real del 2026-10-03 (18:58 hora Chile): ubicación en
# el resumen y "¿por qué asumes que quiero bidones de 12?" respondido como
# fuera de alcance.
# --------------------------------------------------------------------------

LINEAS_12L = [
    {"nombre_producto": "Bidón 12L Recarga", "cantidad": 2},
    {"nombre_producto": "Bidón 12L Nuevo", "cantidad": 1},
]

# Fragmentos del rechazo por "fuera de alcance" que redacta el LLM.
_RECHAZO_FUERA_DE_ALCANCE = ("solo puedo ayudar", "ejecutivo")


def _es_rechazo(texto: str) -> bool:
    return any(fragmento in texto.lower() for fragmento in _RECHAZO_FUERA_DE_ALCANCE)


async def caso_p(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    resumen = await _preparar_resumen(ctx)
    v.check("Ubicación" not in resumen and "ubicación" not in resumen, "el resumen no menciona la ubicación")
    v.check(
        f"Nombre: {CLIENTE['nombre']}" in resumen and f"Dirección de despacho: {CLIENTE['direccion']}" in resumen,
        "el resumen sigue con nombre y dirección",
    )
    respuestas = await _conversar(phone, ["si"])
    pedidos = await _pedidos_de(ctx["cliente_id"])
    v.check(len(pedidos) == 1 and "confirmado" in respuestas[0].lower(), "se confirma igual")
    v.check(
        bool(pedidos)
        and (pedidos[0]["latitud"], pedidos[0]["longitud"]) == (CLIENTE["latitud"], CLIENTE["longitud"]),
        "el pedido guarda las coordenadas igual que antes",
    )
    await _ejecutar(
        "delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id = :id)",
        id=ctx["cliente_id"],
    )
    await _ejecutar("delete from pedido where cliente_id = :id", id=ctx["cliente_id"])


async def caso_q(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx, LINEAS_12L)
    respuestas = await _conversar(phone, ["porque asumes que quiero bidones de 12?"])
    texto = respuestas[0]
    draft = get_draft(phone) or {}
    v.check(not _es_rechazo(texto), "no responde 'fuera de alcance'")
    v.check("Resumen de tu pedido" not in texto, "no repite el resumen pegado a la respuesta")
    v.check(
        PREGUNTA_OPCIONES_PEDIDO in texto or (bool(draft.get("correccion_pendiente")) and "20L" in texto),
        "ofrece corregir (cambiar a 20L) o las opciones cambiar/confirmar/cancelar",
    )
    v.check(draft.get("estado") == "esperando_modificacion", "pasa a esperando_modificacion")
    v.check(
        draft.get("productos") == LINEAS_12L and not draft.get("aclaracion_pendiente"),
        "la pregunta no cambia el pedido ni deja un bidón pendiente",
    )
    respuestas += await _conversar(phone, ["si"])
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no confirma nada (ni con un 'si' después)")
    clear_draft(phone)

    # Sin LLM: aunque el LLM la marque fuera de alcance, el código la trata
    # como duda sobre el pedido.
    await _preparar_resumen(ctx, LINEAS_12L)
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    resultado = {
        "intencion": "fuera_de_alcance",
        "productos": [],
        "respuesta_sugerida": "Solo puedo ayudar con pedidos de agua de esta distribuidora. "
        "Si necesitas algo diferente, te sugiero contactar a un ejecutivo.",
    }
    texto = await _aplicar_resultado_llm(
        phone, resultado, get_draft(phone), cliente, "porque asumnes que quiero bidones de 12?"
    )
    print(f"  [LLM simulado: fuera de alcance] Bot: {texto}")
    v.check(not _es_rechazo(texto) and PREGUNTA_OPCIONES_PEDIDO in texto, "el código corrige la clasificación del LLM")
    v.check((get_draft(phone) or {}).get("estado") == "esperando_modificacion", "y pasa a esperando_modificacion")
    clear_draft(phone)


async def caso_r(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    v.check(not _habla_del_pedido("cuanto cuesta una pizza"), "'cuánto cuesta una pizza' no habla del pedido")
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["cuánto cuesta una pizza"])
    texto = respuestas[0]
    v.check(_es_rechazo(texto), "responde 'fuera de alcance'")
    v.check("Resumen de tu pedido" not in texto, "sin el resumen pegado al rechazo")
    v.check(PREGUNTA_OPCIONES_PEDIDO in texto, "ofrece cambiar, confirmar o cancelar")
    v.check(
        (get_draft(phone) or {}).get("productos") == LINEAS_PEDIDO and not await _pedidos_de(ctx["cliente_id"]),
        "el pedido sigue en curso, sin confirmar",
    )
    clear_draft(phone)


# --------------------------------------------------------------------------
# Corrección propuesta estructurada y /health con el commit.
# --------------------------------------------------------------------------

LINEAS_20L = [
    {"nombre_producto": "Bidón 20L Recarga", "cantidad": 2},
    {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 1},
]


def _sin_orden(lineas: list[dict]) -> list[tuple]:
    return sorted((item["nombre_producto"], item["cantidad"]) for item in lineas)


async def _duda_simulada(ctx: dict, propuesta: dict | None, respuesta: str) -> str:
    """Resumen con 12L y una duda cuya interpretación del LLM se simula
    (determinista), con la corrección propuesta dada."""
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx, LINEAS_12L)
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    resultado = {
        "intencion": "duda_pedido",
        "productos": [],
        "correccion_propuesta": propuesta,
        "respuesta_sugerida": respuesta,
    }
    texto = await _aplicar_resultado_llm(
        phone, resultado, get_draft(phone), cliente, "porque asumes que quiero bidones de 12?"
    )
    print(f"  [LLM simulado] Bot: {texto}")
    return texto


async def caso_s(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx, LINEAS_12L)
    respuestas = await _conversar(phone, ["porque asumes que quiero bidones de 12?"])
    v.check("20L" in respuestas[0] and "?" in respuestas[0], "ofrece cambiar a 20L")
    v.check(bool((get_draft(phone) or {}).get("correccion_pendiente")), "la corrección queda guardada como pendiente")
    respuestas += await _conversar(phone, ["si"])
    draft = get_draft(phone) or {}
    precios = ctx["precios"]
    total = sum(precios[item["nombre_producto"]] * item["cantidad"] for item in LINEAS_20L)
    v.check(_sin_orden(draft.get("productos") or []) == _sin_orden(LINEAS_20L), "el draft queda con 2x 20L Recarga y 1x 20L Nuevo")
    # Antes del resumen va la confirmación del cambio ("Cambié 2x Bidón 12L
    # Recarga por ..."), que sí nombra los de 12L: se revisa solo el resumen.
    resumen = respuestas[1][respuestas[1].find("Resumen de tu pedido"):]
    v.check(
        _es_resumen(respuestas[1])
        and all(f"{i['cantidad']}x {i['nombre_producto']}" in resumen for i in LINEAS_20L)
        and "12L" not in resumen,
        "muestra el resumen nuevo, sin 12L",
    )
    v.check(f"Total: {_clp(total)}" in respuestas[1], f"con precios y total recalculados ({_clp(total)})")
    v.check(draft.get("estado") == "esperando_confirmacion", "pide confirmación explícita")
    v.check(not draft.get("correccion_pendiente"), "la corrección ya no está pendiente")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "el pedido NO se confirma en ese turno")
    clear_draft(phone)


async def caso_t(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx, LINEAS_12L)
    respuestas = await _conversar(phone, ["porque asumes que quiero bidones de 12?", "no"])
    draft = get_draft(phone) or {}
    v.check(bool(respuestas[0]) and "20L" in respuestas[0], "ofrece cambiar a 20L")
    v.check(respuestas[1] == MENSAJE_CORRECCION_RECHAZADA, "'no' pregunta qué quiere cambiar")
    v.check(draft.get("productos") == LINEAS_12L, "el pedido no cambia")
    v.check(not draft.get("correccion_pendiente") and draft.get("estado") == "esperando_modificacion", "descarta la propuesta")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no se confirma nada")
    clear_draft(phone)


async def caso_u(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx, LINEAS_12L)
    respuestas = await _conversar(phone, ["no", "si"])
    draft = get_draft(phone) or {}
    v.check(not draft.get("correccion_pendiente"), "no había corrección pendiente")
    v.check(
        respuestas[1].startswith(PREFIJO_REPETIR_RESUMEN) and _es_resumen(respuestas[1]),
        "'si' sin propuesta vuelve a mostrar el resumen",
    )
    v.check(draft.get("productos") == LINEAS_12L, "sin cambios en el pedido")
    v.check(
        draft.get("estado") == "esperando_confirmacion" and not await _pedidos_de(ctx["cliente_id"]),
        "y exige confirmación explícita (no confirma)",
    )
    clear_draft(phone)


async def caso_v(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    propuesta = {
        "productos_actuales": ["Bidón 12L Recarga", "Bidón 12L Nuevo"],
        "atributo": "capacidad",
        "valor_nuevo": "20",
        "alcance": "algunas",
    }
    texto = await _duda_simulada(ctx, propuesta, "Tienes razón. ¿Te gustaría cambiar alguno de ellos a 20L?")
    v.check("Cuántas unidades" in texto, "'alguno de ellos': pregunta cuántas unidades")
    v.check((get_draft(phone) or {}).get("productos") == LINEAS_12L, "no cambia nada todavía")
    respuestas = await _conversar(phone, ["todas"])
    v.check(
        _sin_orden((get_draft(phone) or {}).get("productos") or []) == _sin_orden(LINEAS_20L) and _es_resumen(respuestas[0]),
        "'todas' aplica el cambio y muestra el resumen",
    )
    clear_draft(phone)

    # Una sola línea, "algunas" y luego "1": cambia solo 1 unidad.
    propuesta = {**propuesta, "productos_actuales": ["Bidón 12L Recarga"]}
    await _duda_simulada(ctx, propuesta, "Tienes razón. ¿Te gustaría cambiar alguno a 20L?")
    await _conversar(phone, ["1"])
    esperado = [
        {"nombre_producto": "Bidón 12L Recarga", "cantidad": 1},
        {"nombre_producto": "Bidón 12L Nuevo", "cantidad": 1},
        {"nombre_producto": "Bidón 20L Recarga", "cantidad": 1},
    ]
    v.check(_sin_orden((get_draft(phone) or {}).get("productos") or []) == _sin_orden(esperado), "'1' cambia solo una unidad")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no se confirma nada")
    clear_draft(phone)


async def caso_w(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    propuesta = {
        "productos_actuales": ["Bidón 12L Recarga", "Bidón 12L Nuevo"],
        "atributo": "capacidad",
        "valor_nuevo": "5",
        "alcance": "todas",
    }
    texto = await _duda_simulada(ctx, propuesta, "Tienes razón. ¿Quieres que los cambie a 5L?")
    draft = get_draft(phone) or {}
    v.check("5L" not in texto, "la propuesta inválida (Bidón 5L no existe) no se ofrece")
    v.check(not draft.get("correccion_pendiente"), "ni queda guardada")
    v.check(PREGUNTA_OPCIONES_PEDIDO in texto and not _es_rechazo(texto), "responde la duda y ofrece las opciones")
    respuestas = await _conversar(phone, ["si"])
    v.check(
        (get_draft(phone) or {}).get("productos") == LINEAS_12L and _es_resumen(respuestas[0]),
        "un 'si' después no cambia nada: vuelve al resumen",
    )
    clear_draft(phone)

    # Producto que no está en el pedido: también se descarta.
    propuesta = {**propuesta, "productos_actuales": ["Dispensador USB"], "atributo": "modelo", "valor_nuevo": "Básico"}
    await _duda_simulada(ctx, propuesta, "¿Quieres cambiar el dispensador?")
    v.check(not (get_draft(phone) or {}).get("correccion_pendiente"), "una línea que no está en el pedido se descarta")
    clear_draft(phone)


async def caso_y(ctx: dict, v: Verificador) -> None:
    """Regresión: si el LLM marca fuera de alcance una RESPUESTA a la
    pregunta pendiente ("2 recargas y 2 nuevos"), el código la resuelve igual
    y no la trata como duda ni muestra el rechazo."""
    phone = CLIENTE["telefono"]
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    draft_previo = {
        "intencion": "pedido",
        "productos": [],
        "aclaracion_pendiente": {"capacidad_litros": 20, "cantidad": 4},
        "paso": "producto",
        "estado": "armando",
    }
    resultado = {
        "intencion": "fuera_de_alcance",
        "productos": [],
        "respuesta_sugerida": "Solo puedo ayudar con pedidos de agua de esta distribuidora.",
    }
    texto = await _aplicar_resultado_llm(phone, resultado, draft_previo, cliente, "2 recargas y 2 nuevos")
    print(f"  [LLM simulado: fuera de alcance] Bot: {texto}")
    draft = get_draft(phone) or {}
    v.check(draft.get("productos") == LINEAS_BIDONES, "resuelve los 2 recarga + 2 nuevos")
    v.check(not _es_rechazo(texto) and "Perdón" not in texto, "sin rechazo ni respuesta de duda")
    v.check(CLIENTE["direccion"] in texto, "avanza a confirmar la dirección")
    clear_draft(phone)


async def caso_z(ctx: dict, v: Verificador) -> None:
    """Bug del 2026-10-08: "3 bidones de 20 y 1 de 12" perdía la línea de
    12L. Ahora pregunta nuevo/recarga de ambas capacidades y registra las
    dos líneas."""
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(phone, ["quiero 3 bidones de 20 y 1 de 12"])
    v.check(
        "3 bidones de 20L" in respuestas[0] and "bidón de 12L" in respuestas[0],
        "pregunta nuevo/recarga de los de 20L y del de 12L",
    )
    respuestas = await _conversar(phone, ["las de 20 recarga y la de 12 nuevo"])
    draft = get_draft(phone) or {}
    v.check(
        draft.get("productos")
        == [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 3}, {"nombre_producto": "Bidón 12L Nuevo", "cantidad": 1}],
        "registra 3x 20L Recarga y 1x 12L Nuevo, sin perder unidades",
    )
    v.check(CLIENTE["direccion"] in respuestas[0], "avanza a confirmar la dirección")
    clear_draft(phone)


async def caso_aa(ctx: dict, v: Verificador) -> None:
    """Bug de la primera prueba real del 2026-10-08: a "¿Los 2 bidones de
    20L y el bidón de 12L los quieres nuevos o de recarga?" el cliente
    respondió "20 nuevo y 12 recarga" y el bot preguntó por "32 bidones"."""
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(phone, ["2 de 20 y 1 de 12", "20 nuevo y 12 recarga"])
    draft = get_draft(phone) or {}
    v.check("2 bidones de 20L" in respuestas[0] and "bidón de 12L" in respuestas[0], "pregunta por capacidad")
    v.check(
        draft.get("productos")
        == [{"nombre_producto": "Bidón 20L Nuevo", "cantidad": 2}, {"nombre_producto": "Bidón 12L Recarga", "cantidad": 1}]
        and not draft.get("aclaracion_pendiente"),
        "registra 2x 20L Nuevo y 1x 12L Recarga",
    )
    v.check("32" not in respuestas[1] and CLIENTE["direccion"] in respuestas[1], "sin pregunta por 32 bidones: pasa a la dirección")
    clear_draft(phone)


async def caso_x(ctx: dict, v: Verificador) -> None:
    transport = httpx.ASGITransport(app=app)
    original = os.environ.pop("RENDER_GIT_COMMIT", None)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sin = await client.get("/health")
            os.environ["RENDER_GIT_COMMIT"] = "abc1234def"
            con = await client.get("/health")
    finally:
        os.environ.pop("RENDER_GIT_COMMIT", None)
        if original is not None:
            os.environ["RENDER_GIT_COMMIT"] = original
    print(f"  /health sin variable: {sin.json()} | con variable: {con.json()}")
    v.check(sin.status_code == 200 and sin.json() == {"status": "ok", "commit": "desconocido"}, "sin RENDER_GIT_COMMIT: commit 'desconocido'")
    v.check(con.status_code == 200 and con.json() == {"status": "ok", "commit": "abc1234def"}, "con RENDER_GIT_COMMIT: devuelve el commit")


CASOS = [
    ("a", "Mismo wamid entregado 3 veces: se procesa y responde una sola vez; orden por teléfono", caso_a),
    ("b", "'4 bidones de 20' + '2 recargas y 2 nuevos': dos líneas y avanza a la dirección", caso_b),
    ("c", "Tras b: dirección, 'si, un dispensador usb', 'no': resumen con todo y total correcto", caso_c),
    ("d", "'no' ante '¿Confirmas?': pregunta qué cambiar, no repite el resumen", caso_d),
    ("e", "'no', 'no', 'canelar', 'si': pedido cancelado, sin pedido ni detalle en la BD", caso_e),
    ("f", "'no' y luego 'si': no confirma, vuelve al resumen", caso_f),
    ("g", "'no cancelen mi pedido' no cancela", caso_g),
    ("h", "Confirmación feliz: 'si' al resumen crea el pedido con todas las líneas", caso_h),
    ("i", "Draft con menos productos de los pedidos: no muestra el resumen", caso_i),
    ("j", "Detección determinista de cancelación y variantes; '2 recargas' de 4 no avanza", caso_j),
    ("k", "Índice único de wamid: la violación es 'duplicado' (200, sin procesar ni responder)", caso_k),
    ("l", "'quiero 5 bidones' pregunta la capacidad y no asume ninguna", caso_l),
    ("m", "'dispensador' sin modelo pregunta cuál, con precios; nunca elige uno", caso_m),
    ("n", "Producto fuera del catálogo: se avisa, no se descarta en silencio ni avanza", caso_n),
    ("o", "Conversación real completa de la 2da prueba: capacidad y dispensador elegidos", caso_o),
    ("p", "El resumen no muestra la ubicación (las coordenadas se guardan igual)", caso_p),
    ("q", "'¿por qué asumes que quiero bidones de 12?' se responde como duda, no fuera de alcance", caso_q),
    ("r", "'cuánto cuesta una pizza' sí es fuera de alcance, sin el resumen pegado", caso_r),
    ("s", "Duda por 12L → ofrece 20L → 'si': aplica, recalcula y muestra el resumen sin confirmar", caso_s),
    ("t", "Misma duda → 'no': el pedido no cambia y pregunta qué cambiar", caso_t),
    ("u", "'si' sin propuesta pendiente: vuelve al resumen y exige confirmación", caso_u),
    ("v", "Propuesta 'alguno de ellos': pregunta cuántas unidades", caso_v),
    ("w", "Propuesta inválida (producto inexistente o fuera del pedido): se descarta", caso_w),
    ("x", "/health incluye el commit desplegado", caso_x),
    ("y", "Respuesta a la pregunta pendiente marcada 'fuera de alcance' por el LLM: se resuelve igual", caso_y),
    ("z", "'3 bidones de 20 y 1 de 12': pregunta ambas capacidades y no pierde la de 12L", caso_z),
    ("aa", "'2 de 20 y 1 de 12' + '20 nuevo y 12 recarga': 2x 20L Nuevo y 1x 12L Recarga, sin '32 bidones'", caso_aa),
]


async def main() -> None:
    await _limpiar_rango()
    ctx = {"cliente_id": await _crear_cliente(), "precios": await _precios()}
    print(f"Cliente de prueba: id={ctx['cliente_id']} {CLIENTE}")

    resultados = []
    try:
        for letra, descripcion, caso in CASOS:
            print("#" * 70)
            print(f"Caso {letra}: {descripcion}")
            print("#" * 70)
            for phone in TELEFONOS_PRUEBA:
                clear_draft(phone)
            v = Verificador()
            try:
                await caso(ctx, v)
            except Exception as exc:
                print(f"    [ERROR] {exc!r}")
                v.errores.append(repr(exc))
            resultados.append((letra, descripcion, not v.errores))
            print()
    finally:
        for phone in TELEFONOS_PRUEBA:
            clear_draft(phone)
        await _limpiar_rango()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
