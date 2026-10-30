"""
Script temporal para validar, a través de procesar_mensaje y contra la BD, la
historia #64: el bot ofrece repetir el último pedido ENTREGADO de un cliente
existente (ver _ofrecer_repetir_pedido y _responder_a_oferta_repetir en
app/services/order_flow.py).
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 56931002xxx
(distinto del de test_identificacion_cliente.py, que usa 56931000xxx). Al
empezar y al terminar (también si falla), el script BORRA los clientes de
ese rango (con sus pedidos, detalles, mensajes, conversación y borrador) y
crea tres clientes de prueba:
- CON_HISTORIAL: dos pedidos entregados (el más reciente es el que se debe
  ofrecer) y un pedido pendiente más nuevo (que NO cuenta como historial).
- SIN_HISTORIAL: cliente existente sin pedidos.
- SOLO_PENDIENTE: cliente existente con un único pedido pendiente.
Las filas de auditoria no se borran (son el registro histórico).

Los casos llaman al LLM real (igual que los otros scripts de order_flow).

Uso: python -m scripts.test_repetir_pedido
"""

import asyncio
from decimal import Decimal

from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import procesar_mensaje

RANGO_TELEFONOS_SQL = r"^(56)?931002\d{3}$"

CON_HISTORIAL = {
    "telefono_bd": "+56931002001",
    "telefono_whatsapp": "56931002001",
    "nombre": "Hugo Historial",
    "direccion": "Av. Libertad 2000, Viña del Mar",
    "latitud": Decimal("-33.024500"),
    "longitud": Decimal("-71.551800"),
}
SIN_HISTORIAL = {
    "telefono_bd": "+56931002002",
    "telefono_whatsapp": "56931002002",
    "nombre": "Sara Sinhistorial",
    "direccion": "Los Pinos 100, Quilpué",
    "latitud": Decimal("-33.047800"),
    "longitud": Decimal("-71.441200"),
}
SOLO_PENDIENTE = {
    "telefono_bd": "+56931002003",
    "telefono_whatsapp": "56931002003",
    "nombre": "Pablo Pendiente",
    "direccion": "Calle Falsa 123, Valparaíso",
    "latitud": Decimal("-33.045800"),
    "longitud": Decimal("-71.619700"),
}
TELEFONO_NUEVO = "56931002010"

TELEFONOS_PRUEBA = (
    CON_HISTORIAL["telefono_whatsapp"],
    SIN_HISTORIAL["telefono_whatsapp"],
    SOLO_PENDIENTE["telefono_whatsapp"],
    TELEFONO_NUEVO,
)

# Último pedido entregado de CON_HISTORIAL (lo que se debe ofrecer).
ULTIMO_PEDIDO = {"Bidón 20L Recarga": 2, "Bidón 12L Nuevo": 1}


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


async def _crear_cliente(datos: dict) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        nombre=datos["nombre"],
        telefono=datos["telefono_bd"],
        direccion=datos["direccion"],
        latitud=datos["latitud"],
        longitud=datos["longitud"],
    )
    return result.scalar_one()


async def _crear_pedido(cliente_id: int, estado: str, dias_atras: int, lineas: dict[str, int]) -> int:
    result = await _ejecutar(
        "insert into pedido (cliente_id, estado, direccion_despacho, total, creado_en) "
        "values (:cliente_id, cast(:estado as estado_pedido), 'Dirección de prueba', 0, "
        "now() - make_interval(days => :dias)) returning id",
        cliente_id=cliente_id,
        estado=estado,
        dias=dias_atras,
    )
    pedido_id = result.scalar_one()
    for nombre, cantidad in lineas.items():
        await _ejecutar(
            "insert into detalle_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario) "
            "select :pedido_id, id, :cantidad, precio_unitario from producto where nombre = :nombre",
            pedido_id=pedido_id,
            cantidad=cantidad,
            nombre=nombre,
        )
    return pedido_id


async def _pedidos_de(cliente_id: int) -> list[dict]:
    result = await _ejecutar("select * from pedido where cliente_id = :id order by id", id=cliente_id)
    return [dict(fila._mapping) for fila in result]


async def _lineas_de(pedido_id: int) -> dict[str, int]:
    result = await _ejecutar(
        "select p.nombre, d.cantidad_solicitada from detalle_pedido d "
        "join producto p on p.id = d.producto_id where d.pedido_id = :id",
        id=pedido_id,
    )
    return {fila.nombre: fila.cantidad_solicitada for fila in result}


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _conversar(phone: str, turnos: list[str]) -> list[str]:
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        paso = (get_draft(phone) or {}).get("paso")
        print(f"  Bot [{paso}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


def _lineas_draft(phone: str) -> dict[str, int]:
    productos = (get_draft(phone) or {}).get("productos") or []
    return {p["nombre_producto"]: p["cantidad"] for p in productos}


def _ofrece_repetir(texto: str) -> bool:
    return "último pedido" in texto.lower()


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


async def caso_a(ctx: dict, v: Verificador) -> None:
    phone = CON_HISTORIAL["telefono_whatsapp"]
    r = (await _conversar(phone, ["Hola"]))[0]
    v.check("Hugo" in r, "saluda con su nombre")
    v.check(_ofrece_repetir(r), "ofrece repetir el último pedido")
    v.check("2x Bidón 20L Recarga" in r and "1x Bidón 12L Nuevo" in r, "muestra las líneas del último entregado")
    v.check("Dispensador" not in r, "no muestra el pedido entregado más antiguo")
    v.check("5x" not in r, "no muestra el pedido pendiente (no es historial)")
    v.check(bool((get_draft(phone) or {}).get("oferta_repetir")), "la oferta queda en el draft")
    clear_draft(phone)


async def caso_b(ctx: dict, v: Verificador) -> None:
    phone = CON_HISTORIAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["Hola", "sí"])
    draft = get_draft(phone) or {}
    v.check(_lineas_draft(phone) == ULTIMO_PEDIDO, "el draft queda con las líneas del último pedido")
    v.check("oferta_repetir" not in draft, "la oferta se quita del draft")
    v.check(f"¿Despachamos a {CON_HISTORIAL['direccion']}?" in respuestas[1], "sigue el flujo: confirma la dirección")
    clear_draft(phone)


async def caso_c(ctx: dict, v: Verificador) -> None:
    phone = CON_HISTORIAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["Hola", "no"])
    draft = get_draft(phone) or {}
    v.check("qué producto" in respuestas[1].lower(), "pregunta qué producto quiere")
    v.check(not draft.get("productos"), "el draft queda sin productos")
    v.check("oferta_repetir" not in draft, "la oferta se quita del draft")
    clear_draft(phone)


async def caso_d(ctx: dict, v: Verificador) -> None:
    phone = CON_HISTORIAL["telefono_whatsapp"]
    await _conversar(phone, ["Hola", "quiero 1 dispensador usb"])
    draft = get_draft(phone) or {}
    v.check("oferta_repetir" not in draft, "la oferta se descarta")
    v.check("Dispensador USB" in _lineas_draft(phone), "toma el producto que pidió")
    v.check("Bidón 20L Recarga" not in _lineas_draft(phone), "no carga el último pedido")
    clear_draft(phone)


async def caso_e(ctx: dict, v: Verificador) -> None:
    phone = CON_HISTORIAL["telefono_whatsapp"]
    r = (await _conversar(phone, ["Quiero 2 bidones de 20 litros recarga"]))[0]
    v.check(not _ofrece_repetir(r), "no ofrece repetir si ya pidió un producto")
    v.check(_lineas_draft(phone) == {"Bidón 20L Recarga": 2}, "toma solo lo que pidió")
    clear_draft(phone)


async def caso_f(ctx: dict, v: Verificador) -> None:
    phone = SIN_HISTORIAL["telefono_whatsapp"]
    r = (await _conversar(phone, ["Hola"]))[0]
    v.check(not _ofrece_repetir(r), "cliente sin pedidos: no ofrece repetir")
    v.check("oferta_repetir" not in (get_draft(phone) or {}), "sin oferta en el draft")
    clear_draft(phone)


async def caso_g(ctx: dict, v: Verificador) -> None:
    phone = SOLO_PENDIENTE["telefono_whatsapp"]
    r = (await _conversar(phone, ["Hola"]))[0]
    v.check(not _ofrece_repetir(r), "cliente con solo un pedido pendiente: no ofrece repetir")
    clear_draft(phone)


async def caso_h(ctx: dict, v: Verificador) -> None:
    phone = CON_HISTORIAL["telefono_whatsapp"]
    r = (await _conversar(phone, ["¿Cuánto cuesta el bidón de 20 litros?"]))[0]
    v.check(not _ofrece_repetir(r), "una consulta de precio no dispara la oferta")
    v.check("oferta_repetir" not in (get_draft(phone) or {}), "sin oferta en el draft")
    clear_draft(phone)


async def caso_i(ctx: dict, v: Verificador) -> None:
    r = (await _conversar(TELEFONO_NUEVO, ["Hola"]))[0]
    v.check(not _ofrece_repetir(r), "cliente nuevo: no ofrece repetir")
    clear_draft(TELEFONO_NUEVO)


async def caso_j(ctx: dict, v: Verificador) -> None:
    phone = CON_HISTORIAL["telefono_whatsapp"]
    cliente_id = ctx["con_historial_id"]
    pedidos_antes = len(await _pedidos_de(cliente_id))
    respuestas = await _conversar(phone, ["Hola", "sí", "sí", "no, eso es todo", "si"])
    v.check("resumen" in respuestas[3].lower(), "muestra el resumen con las líneas repetidas")
    v.check("confirmado" in respuestas[4].lower(), "pedido confirmado")
    pedidos = await _pedidos_de(cliente_id)
    v.check(len(pedidos) == pedidos_antes + 1, "se creó un pedido nuevo")
    nuevo = pedidos[-1]
    v.check(nuevo["estado"] == "pendiente", "el pedido nuevo queda pendiente")
    v.check(await _lineas_de(nuevo["id"]) == ULTIMO_PEDIDO, "el pedido nuevo tiene las mismas líneas")


CASOS = [
    ("a", "Cliente con historial dice 'Hola': saludo con nombre y oferta con las líneas del último entregado", caso_a),
    ("b", "Responde 'sí' a la oferta: carga las líneas y sigue con la dirección", caso_b),
    ("c", "Responde 'no' a la oferta: pregunta el producto, sin líneas cargadas", caso_c),
    ("d", "Responde con otro producto: descarta la oferta y toma lo que pidió", caso_d),
    ("e", "Primer mensaje ya trae producto: no ofrece", caso_e),
    ("f", "Cliente existente sin pedidos: no ofrece", caso_f),
    ("g", "Cliente con solo un pedido pendiente: no ofrece (solo cuentan los entregados)", caso_g),
    ("h", "Consulta de precio: no ofrece", caso_h),
    ("i", "Cliente nuevo: no ofrece", caso_i),
    ("j", "Flujo completo: oferta, sí, dirección, resumen y confirmación crean el pedido repetido", caso_j),
]


async def main() -> None:
    await _limpiar_rango()
    resultados = []
    try:
        ctx = {"con_historial_id": await _crear_cliente(CON_HISTORIAL)}
        await _crear_cliente(SIN_HISTORIAL)
        solo_pendiente_id = await _crear_cliente(SOLO_PENDIENTE)

        await _crear_pedido(ctx["con_historial_id"], "entregado", 10, {"Dispensador USB": 1})
        await _crear_pedido(ctx["con_historial_id"], "entregado", 3, ULTIMO_PEDIDO)
        await _crear_pedido(ctx["con_historial_id"], "pendiente", 1, {"Bidón 12L Recarga": 5})
        await _crear_pedido(solo_pendiente_id, "pendiente", 1, {"Bidón 20L Recarga": 1})
        print(f"Clientes de prueba creados (historial id={ctx['con_historial_id']})")

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
