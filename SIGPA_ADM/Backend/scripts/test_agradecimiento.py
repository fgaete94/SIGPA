"""
Script temporal para validar la historia #169: el bot responde con un mensaje
propio cuando el cliente agradece (ver es_agradecimiento_puro y
_responder_agradecimiento en app/services/order_flow.py, y
_cierre_por_agradecimiento en app/api/routes/whatsapp.py):
- Con la conversación cerrada (tras confirmar el pedido): cierre con el número
  del pedido, sin saludo de derivación y sin avisar a la ejecutiva.
- Con un pedido en curso: "¡Con gusto!" más la pregunta pendiente, sin tocar el
  borrador ni confirmar nada.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 569333006xx.
Al empezar y al terminar (también si falla), el script BORRA los clientes de
ese rango (con sus pedidos, detalles, mensajes, productos habituales,
conversación y borrador). Las filas de auditoria no se borran (son el registro
histórico).

No envía nada a Meta: send_whatsapp_message se reemplaza por un fake que
registra el mensaje saliente como el real (para detectar el agradecimiento
repetido) y la ejecutiva de prueba es EJECUTIVA_PHONE_PRUEBA. Los casos con
pedido en curso llaman al LLM real (igual que los otros scripts de order_flow).

Uso: python -m scripts.test_agradecimiento
"""

import asyncio
import copy
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.draft_store as draft_store
import app.services.order_flow as order_flow
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.conversacion_bot_service import esta_activa, marcar_activa
from app.services.draft_store import clear_draft, get_draft
from app.services.horario_atencion import mensaje_derivacion_ejecutiva
from app.services.mensaje_whatsapp_service import registrar_mensaje
from app.services.order_flow import procesar_mensaje

CHILE = ZoneInfo("America/Santiago")
RANGO_TELEFONOS_SQL = r"^(56)?9333006\d{2}$"
EJECUTIVA_PHONE_PRUEBA = "56933300690"
JUEVES_10 = datetime(2026, 10, 8, 10, 0, tzinfo=CHILE)


def _cliente(n: int, nombre: str, direccion: str) -> dict:
    return {
        "telefono_bd": f"+569333006{n:02d}",
        "telefono_whatsapp": f"569333006{n:02d}",
        "nombre": nombre,
        "direccion": direccion,
        "latitud": Decimal("-33.024500"),
        "longitud": Decimal("-71.551800"),
    }


CLI_A = _cliente(1, "Agustina Agradecida", "Av. Libertad 6000, Viña del Mar")
CLI_VIEJO = _cliente(2, "Viviana Vieja", "Los Pinos 600, Quilpué")
CLI_SIN_PEDIDOS = _cliente(3, "Sergio Sinpedidos", "Calle Falsa 600, Valparaíso")
CLI_CANCELADO = _cliente(4, "Carla Cancelada", "Av. España 600, Valparaíso")
CLI_CURSO = _cliente(5, "Camilo Encurso", "Av. Alemania 600, Valparaíso")
CLI_HABITUAL = _cliente(6, "Hortensia Habitual", "Av. Brasil 600, Valparaíso")
CLI_REPETIR = _cliente(7, "Rodrigo Repetidor", "Av. Argentina 600, Valparaíso")
CLI_SIN_BORRADOR = _cliente(8, "Silvia Sinborrador", "Av. Chile 600, Valparaíso")
TEL_DUPLICADO = "56933300609"

TELEFONOS_PRUEBA = tuple(
    c["telefono_whatsapp"]
    for c in (CLI_A, CLI_VIEJO, CLI_SIN_PEDIDOS, CLI_CANCELADO, CLI_CURSO, CLI_HABITUAL, CLI_REPETIR, CLI_SIN_BORRADOR)
) + (TEL_DUPLICADO,)

HABITUAL = "Bidón 20L Recarga"


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar_rango() -> None:
    """Borra los clientes de prueba (rango de teléfonos) con todo lo asociado:
    pedidos, detalles, mensajes, productos habituales, conversación y
    borrador. Las filas de auditoria no se borran."""
    ids_sql = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    pedidos_sql = f"select id from pedido where cliente_id in ({ids_sql})"
    tel_sql = "regexp_replace(telefono, '\\D', '', 'g') ~ :rango"
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
    draft_store._drafts.clear()
    draft_store._persistidos.clear()
    draft_store._pendientes_borrar.clear()


async def _crear_cliente(datos: dict, telefono: str | None = None) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        nombre=datos["nombre"],
        telefono=telefono or datos["telefono_bd"],
        direccion=datos["direccion"],
        latitud=datos["latitud"],
        longitud=datos["longitud"],
    )
    return result.scalar_one()


async def _crear_pedido(cliente_id: int, estado: str, horas_atras: float, lineas: dict[str, int]) -> int:
    result = await _ejecutar(
        "insert into pedido (cliente_id, estado, direccion_despacho, total, creado_en) "
        "values (:cid, cast(:estado as estado_pedido), 'Dirección de prueba', 0, "
        "now() - make_interval(secs => :segundos)) returning id",
        cid=cliente_id,
        estado=estado,
        segundos=horas_atras * 3600,
    )
    pedido_id = result.scalar_one()
    for nombre, cantidad in lineas.items():
        await _ejecutar(
            "insert into detalle_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario) "
            "select :pid, id, :cantidad, precio_unitario from producto where nombre = :nombre",
            pid=pedido_id,
            cantidad=cantidad,
            nombre=nombre,
        )
    return pedido_id


async def _pedidos_de(cliente_id: int) -> list[dict]:
    result = await _ejecutar("select * from pedido where cliente_id = :id order by id", id=cliente_id)
    return [dict(fila._mapping) for fila in result]


async def _fila_borrador(telefono: str):
    return (await _ejecutar("select 1 from borrador_pedido where telefono = :t", t=telefono)).first()


async def _olvidar_salientes(telefono: str) -> None:
    """Deja al teléfono sin mensajes salientes registrados (para que un
    agradecimiento no cuente como repetido). Solo ese teléfono exacto."""
    await _ejecutar("delete from mensaje_whatsapp where telefono = :t and direccion = 'saliente'", t=telefono)


# --------------------------------------------------------------------------
# Envío falso y conversación
# --------------------------------------------------------------------------


@contextmanager
def _fake_send(enviados: list):
    async def fake_send(to: str, message: str):
        enviados.append((to, message))
        print(f"  [fake send] a {to}: {message}")
        # Como el envío real: deja registrado el mensaje saliente.
        await registrar_mensaje(telefono=to, direccion="saliente", tipo="text", contenido=message, estado="enviado")
        return {"messages": [{"id": f"wamid.fake-{uuid.uuid4().hex}"}]}

    originales = (whatsapp_route.send_whatsapp_message, order_flow.send_whatsapp_message)
    whatsapp_route.send_whatsapp_message = fake_send
    order_flow.send_whatsapp_message = fake_send
    try:
        yield
    finally:
        whatsapp_route.send_whatsapp_message, order_flow.send_whatsapp_message = originales


@contextmanager
def _config_prueba():
    claves = ("EJECUTIVA_PHONE", "WHATSAPP_TEMPLATE_EJECUTIVA_NAME")
    originales = {c: getattr(settings, c) for c in claves}
    settings.EJECUTIVA_PHONE = EJECUTIVA_PHONE_PRUEBA
    settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""
    try:
        yield
    finally:
        for c, valor in originales.items():
            setattr(settings, c, valor)


async def _por_webhook(phone: str, texto: str, ahora: datetime = JUEVES_10) -> list[tuple[str, str]]:
    """Procesa un mensaje entrante por la ruta de WhatsApp y devuelve lo que se
    envió (destinatario, texto)."""
    enviados: list = []
    mensaje = {"from": phone, "id": f"wamid.test-gracias-{uuid.uuid4().hex}", "type": "text", "text": {"body": texto}}
    print(f"  Cliente: {texto}")
    with _fake_send(enviados):
        await whatsapp_route._procesar_mensaje_entrante(mensaje, ahora)
    return enviados


async def _conversar(phone: str, turnos: list[str]) -> list[str]:
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        paso = (get_draft(phone) or {}).get("paso")
        print(f"  Bot [{paso}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


async def _hasta_resumen(phone: str, maximo: int = 6) -> None:
    for _ in range(maximo):
        await draft_store.cargar_draft(phone)
        draft = get_draft(phone) or {}
        if draft.get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION:
            return
        turno = {"confirmar_direccion": "sí", "algo_mas": "no"}.get(draft.get("paso"))
        if turno is None:
            return
        await _conversar(phone, [turno])


async def _confirmar_pedido_real(phone: str, cliente_id: int) -> int:
    """Crea un pedido con el flujo real de procesar_mensaje y lo confirma;
    devuelve su id. La conversación queda cerrada (marcar_inactiva)."""
    await marcar_activa(phone)
    await _conversar(phone, ["quiero 2 bidones de 20 litros recarga"])
    await _hasta_resumen(phone)
    await _conversar(phone, ["sí"])
    pedidos = await _pedidos_de(cliente_id)
    assert len(pedidos) == 1, f"se esperaba 1 pedido, hay {len(pedidos)}"
    assert not await esta_activa(phone), "la conversación debió quedar cerrada al confirmar"
    return pedidos[0]["id"]


def _a_ejecutiva(enviados: list) -> list:
    return [(to, m) for to, m in enviados if to == EJECUTIVA_PHONE_PRUEBA]


def _cierre(pedido_id: int) -> str:
    return whatsapp_route.MENSAJE_CIERRE_AGRADECIMIENTO.format(pedido_id=pedido_id)


class Verificador:
    def __init__(self) -> None:
        self.errores: list[str] = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'OK' if condicion else 'FALLO'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    phone = CLI_A["telefono_whatsapp"]
    pedido_id = await _confirmar_pedido_real(phone, ctx["a"])
    ctx["pedido_a"] = pedido_id
    for texto in ("gracias", "muchas gracias", "ok gracias", "Gracias!!", "grax"):
        await _olvidar_salientes(phone)
        enviados = await _por_webhook(phone, texto)
        respuesta = enviados[0][1] if enviados else ""
        v.check(enviados == [(phone, _cierre(pedido_id))], f"{texto!r}: responde solo el cierre con el pedido #{pedido_id}")
        v.check(not _a_ejecutiva(enviados) and len(enviados) == 1, f"{texto!r}: no se avisa a la ejecutiva")
        v.check(
            "hola" not in respuesta.lower() and settings.NOMBRE_ASISTENTE not in respuesta and "ejecutiva" not in respuesta.lower(),
            f"{texto!r}: sin saludo ni saludo de derivación",
        )
        v.check(
            not any(frase in respuesta.lower() for frase in ("buen día", "buen dia", "que tengas", "hasta luego")),
            f"{texto!r}: sin despedida adicional",
        )
    v.check(not await esta_activa(phone), "la conversación sigue inactiva")
    v.check(len(await _pedidos_de(ctx["a"])) == 1, "no se creó un pedido nuevo")
    v.check(get_draft(phone) is None and await _fila_borrador(phone) is None, "sin borrador")


async def caso_b(ctx: dict, v: Verificador) -> None:
    phone = CLI_A["telefono_whatsapp"]
    await _olvidar_salientes(phone)
    primero = await _por_webhook(phone, "gracias")
    v.check(primero == [(phone, _cierre(ctx["pedido_a"]))], "el primer agradecimiento recibe el cierre")
    for texto in ("gracias de nuevo", "muchas gracias"):
        enviados = await _por_webhook(phone, texto)
        v.check(enviados == [(phone, whatsapp_route.MENSAJE_AGRADECIMIENTO_REPETIDO)], f"{texto!r}: respuesta breve '¡Con gusto!'")
        v.check(not _a_ejecutiva(enviados), f"{texto!r}: sin aviso a la ejecutiva")
    v.check(not await esta_activa(phone), "la conversación sigue inactiva")


async def caso_c(ctx: dict, v: Verificador) -> None:
    phone = CLI_A["telefono_whatsapp"]
    for texto in ("gracias, ¿pueden agregar un bidón?", "gracias, quiero 2 de 20"):
        await _olvidar_salientes(phone)
        enviados = await _por_webhook(phone, texto)
        v.check(
            enviados[:1] == [(phone, mensaje_derivacion_ejecutiva(JUEVES_10))],
            f"{texto!r}: no es agradecimiento puro: saludo de derivación",
        )
        v.check(len(_a_ejecutiva(enviados)) == 1, f"{texto!r}: se avisa a la ejecutiva")


async def caso_d(ctx: dict, v: Verificador) -> None:
    ventana = settings.AGRADECIMIENTO_VENTANA_HORAS
    v.check(ventana == 12, "AGRADECIMIENTO_VENTANA_HORAS por defecto = 12")
    casos = (
        (CLI_VIEJO, f"pedido de hace {ventana + 1} h (fuera de la ventana)"),
        (CLI_SIN_PEDIDOS, "cliente sin pedidos"),
        (CLI_CANCELADO, "pedido reciente cancelado"),
    )
    for cliente, etiqueta in casos:
        phone = cliente["telefono_whatsapp"]
        enviados = await _por_webhook(phone, "gracias")
        v.check(enviados[:1] == [(phone, mensaje_derivacion_ejecutiva(JUEVES_10))], f"{etiqueta}: saludo de derivación")
        v.check(len(_a_ejecutiva(enviados)) == 1, f"{etiqueta}: aviso a la ejecutiva")
        v.check(not any("quedó registrado" in m for _, m in enviados), f"{etiqueta}: sin cierre")
    # Un teléfono desconocido (sin cliente) tampoco recibe el cierre.
    enviados = await _por_webhook("56933300698", "gracias")
    v.check(enviados[:1] == [("56933300698", mensaje_derivacion_ejecutiva(JUEVES_10))], "teléfono sin cliente: derivación")
    # Justo dentro de la ventana sí recibe el cierre.
    pedido = await _crear_pedido(ctx["viejo"], "pendiente", ventana - 1, {HABITUAL: 1})
    enviados = await _por_webhook(CLI_VIEJO["telefono_whatsapp"], "gracias")
    v.check(
        enviados == [(CLI_VIEJO["telefono_whatsapp"], _cierre(pedido))],
        f"con un pedido de hace {ventana - 1} h (dentro de la ventana) sí hay cierre",
    )


async def caso_e(ctx: dict, v: Verificador) -> None:
    phone = CLI_CURSO["telefono_whatsapp"]
    cliente_id = ctx["curso"]
    await marcar_activa(phone)

    async def agradecer_sin_cambios(etiqueta: str, debe_contener: list[str]) -> str:
        antes = copy.deepcopy(get_draft(phone))
        respuesta = (await _conversar(phone, ["gracias"]))[0]
        v.check(respuesta.startswith("¡Con gusto!"), f"{etiqueta}: responde '¡Con gusto!'")
        for fragmento in debe_contener:
            v.check(fragmento.lower() in respuesta.lower(), f"{etiqueta}: repite la pregunta pendiente ({fragmento[:40]!r})")
        v.check(get_draft(phone) == antes, f"{etiqueta}: el borrador queda idéntico")
        v.check(not await _pedidos_de(cliente_id), f"{etiqueta}: no se confirma nada")
        return respuesta

    # 1. Aclaración pendiente por capacidad.
    await _conversar(phone, ["3 de 20 y 1 de 12"])
    v.check(bool((get_draft(phone) or {}).get("aclaracion_pendiente")), "queda una aclaración pendiente")
    await agradecer_sin_cambios("aclaración pendiente", ["recarga"])
    await _conversar(phone, ["20 nuevo y 12 recarga"])
    v.check(bool(_productos(phone)) and not (get_draft(phone) or {}).get("aclaracion_pendiente"), "el flujo continúa: se resuelve la aclaración")
    clear_draft(phone)

    # 2-4. Dirección, "¿algo más?" y resumen.
    await _conversar(phone, ["quiero 2 bidones de 20 litros recarga"])
    v.check((get_draft(phone) or {}).get("paso") == "confirmar_direccion", "pregunta la dirección")
    await agradecer_sin_cambios("dirección", [f"¿Despachamos a {CLI_CURSO['direccion']}?"])
    await _conversar(phone, ["sí"])
    v.check((get_draft(phone) or {}).get("paso") == "algo_mas", "avanza a '¿algo más?'")
    await agradecer_sin_cambios("algo más", [order_flow.PREGUNTA_ALGO_MAS])
    await _conversar(phone, ["no"])
    v.check((get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION, "llega al resumen")
    respuesta = await agradecer_sin_cambios("esperando confirmación", ["Resumen de tu pedido", "¿Confirmas el pedido?"])
    v.check("confirmado" not in respuesta.lower(), "un gracias nunca confirma el pedido")
    await _conversar(phone, ["sí"])
    pedidos = await _pedidos_de(cliente_id)
    v.check(len(pedidos) == 1, "luego el flujo continúa normal y se confirma el pedido con un sí")

    # 5. Oferta del producto habitual.
    phone_h = CLI_HABITUAL["telefono_whatsapp"]
    await marcar_activa(phone_h)
    await _conversar(phone_h, ["hola"])
    v.check(bool((get_draft(phone_h) or {}).get("oferta_habitual")), "queda la oferta del producto habitual")
    antes = copy.deepcopy(get_draft(phone_h))
    respuesta = (await _conversar(phone_h, ["gracias"]))[0]
    v.check(respuesta.startswith("¡Con gusto!") and "de siempre" in respuesta and HABITUAL in respuesta, "oferta habitual: repite la oferta")
    v.check(get_draft(phone_h) == antes, "oferta habitual: el borrador queda idéntico")
    await _conversar(phone_h, ["sí, 2"])
    v.check(_productos(phone_h) == {HABITUAL: 2}, "y la oferta sigue funcionando después del gracias")
    clear_draft(phone_h)

    # 6. Oferta de repetir el último pedido.
    phone_r = CLI_REPETIR["telefono_whatsapp"]
    await marcar_activa(phone_r)
    await _conversar(phone_r, ["hola"])
    v.check(bool((get_draft(phone_r) or {}).get("oferta_repetir")), "queda la oferta de repetir el último pedido")
    antes = copy.deepcopy(get_draft(phone_r))
    respuesta = (await _conversar(phone_r, ["gracias"]))[0]
    v.check(respuesta.startswith("¡Con gusto!") and "último pedido" in respuesta, "oferta de repetir: repite la oferta")
    v.check(get_draft(phone_r) == antes, "oferta de repetir: el borrador queda idéntico")
    clear_draft(phone_r)


def _productos(phone: str) -> dict[str, int]:
    return {p["nombre_producto"]: p["cantidad"] for p in (get_draft(phone) or {}).get("productos") or []}


async def caso_f(ctx: dict, v: Verificador) -> None:
    phone = CLI_SIN_BORRADOR["telefono_whatsapp"]
    await marcar_activa(phone)
    respuesta = (await _conversar(phone, ["gracias"]))[0]
    v.check(respuesta == order_flow.MENSAJE_AGRADECIMIENTO_SIN_BORRADOR, "sin borrador: respuesta breve")
    v.check(get_draft(phone) is None and await _fila_borrador(phone) is None, "no se crea borrador")
    v.check(await esta_activa(phone), "la conversación sigue activa")
    # Por la ruta de WhatsApp (primer mensaje de la conversación activa).
    enviados = await _por_webhook(phone, "Muchas gracias")
    v.check(
        len(enviados) == 1 and enviados[0][0] == phone and "¡Con gusto!" in enviados[0][1] and not _a_ejecutiva(enviados),
        "por la ruta de WhatsApp: respuesta breve, sin aviso a la ejecutiva",
    )
    v.check(get_draft(phone) is None and await esta_activa(phone), "sin borrador y conversación activa")


async def caso_g(ctx: dict, v: Verificador) -> None:
    phone = CLI_A["telefono_whatsapp"]
    await _olvidar_salientes(phone)
    cierre = await _por_webhook(phone, "gracias")
    v.check(cierre == [(phone, _cierre(ctx["pedido_a"]))], "el agradecimiento recibe el cierre")
    enviados = await _por_webhook(phone, "quiero 2 de 20 recarga")
    v.check(enviados[:1] == [(phone, mensaje_derivacion_ejecutiva(JUEVES_10))], "después del gracias, un pedido sigue el flujo de hoy: derivación")
    v.check(len(_a_ejecutiva(enviados)) == 1, "y se avisa a la ejecutiva")
    v.check(not await esta_activa(phone), "la conversación sigue cerrada")


async def caso_h(ctx: dict, v: Verificador) -> None:
    enviados = await _por_webhook(TEL_DUPLICADO, "gracias")
    v.check(
        enviados[:1] == [(TEL_DUPLICADO, mensaje_derivacion_ejecutiva(JUEVES_10))],
        "teléfono con dos clientes: no se aplica el cierre, va la derivación",
    )
    v.check(len(_a_ejecutiva(enviados)) == 1, "y se avisa a la ejecutiva")
    v.check(not any("quedó registrado" in m for _, m in enviados), "sin cierre")


CASOS = [
    ("a", "Tras confirmar: el agradecimiento recibe el cierre con el pedido", caso_a),
    ("b", "Agradecimiento repetido: '¡Con gusto!' sin aviso", caso_b),
    ("c", "Agradecimiento mixto: sigue el flujo de hoy", caso_c),
    ("d", "Fuera de la ventana, sin pedidos o cancelado: flujo de hoy", caso_d),
    ("e", "Pedido en curso en cada paso: '¡Con gusto!' + pregunta, sin cambios", caso_e),
    ("f", "Conversación activa sin borrador: respuesta breve", caso_f),
    ("g", "Después del cierre, un pedido sigue el flujo de hoy", caso_g),
    ("h", "Teléfono con más de un cliente: sin cierre", caso_h),
]


async def main() -> None:
    await _limpiar_rango()
    resultados = []
    try:
        with _config_prueba():
            ctx = {
                "a": await _crear_cliente(CLI_A),
                "viejo": await _crear_cliente(CLI_VIEJO),
                "sin_pedidos": await _crear_cliente(CLI_SIN_PEDIDOS),
                "cancelado": await _crear_cliente(CLI_CANCELADO),
                "curso": await _crear_cliente(CLI_CURSO),
                "habitual": await _crear_cliente(CLI_HABITUAL),
                "repetir": await _crear_cliente(CLI_REPETIR),
                "sin_borrador": await _crear_cliente(CLI_SIN_BORRADOR),
            }
            await _ejecutar(
                "insert into cliente_producto_habitual (cliente_id, producto_id) "
                "select :cid, id from producto where nombre = :nombre",
                cid=ctx["habitual"],
                nombre=HABITUAL,
            )
            ventana = settings.AGRADECIMIENTO_VENTANA_HORAS
            await _crear_pedido(ctx["viejo"], "pendiente", ventana + 1, {HABITUAL: 1})
            await _crear_pedido(ctx["cancelado"], "cancelado", 1, {HABITUAL: 1})
            await _crear_pedido(ctx["repetir"], "entregado", 240, {"Bidón 12L Nuevo": 1})
            # Dos clientes con el mismo teléfono (uno con pedido reciente).
            dup1 = await _crear_cliente(_cliente(9, "Duplicado Uno", "Calle Uno 600"), "56933300609")
            await _crear_cliente(_cliente(9, "Duplicado Dos", "Calle Dos 600"), "+56 9 3330 0609")
            await _crear_pedido(dup1, "pendiente", 1, {HABITUAL: 1})
            print(f"Clientes de prueba creados: {ctx}")

            for letra, descripcion, caso in CASOS:
                print("#" * 70)
                print(f"Caso {letra}: {descripcion}")
                print("#" * 70)
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
    print(f"  {sum(ok for *_, ok in resultados)} de {len(resultados)} OK")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
