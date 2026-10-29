"""
Script temporal para validar, a través de procesar_mensaje y contra la BD,
la identificación del cliente por teléfono y la persistencia en la tabla
cliente al confirmar un pedido (ver _datos_faltantes y _confirmar_pedido en
app/services/order_flow.py).
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 56931000xxx.
Al empezar, el script BORRA los clientes de ese rango (con sus pedidos y
detalles) y vuelve a crear el cliente existente de prueba CLIENTE_EXISTENTE,
guardado con "+" a propósito para probar la normalización del teléfono (los
mensajes llegan sin "+", como desde WhatsApp). Los casos que modifican al
cliente existente lo restauran antes de correr. Las filas de auditoria no se
borran (son el registro histórico).

El caso de teléfono duplicado reemplaza send_whatsapp_message por un fake
para no enviar mensajes reales a la ejecutiva.

Uso: python -m scripts.test_identificacion_cliente
"""

import asyncio
from decimal import Decimal

from sqlalchemy import text

import app.services.order_flow as order_flow
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.draft_store import clear_draft, get_draft, save_draft
from app.services.order_flow import procesar_mensaje

# Estas pruebas reemplazan send_whatsapp_message por un fake: sin plantilla
# configurada, el aviso a la ejecutiva también pasa por ese fake (#152).
settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""

RANGO_TELEFONOS_SQL = r"^(56)?931000\d{3}$"

CLIENTE_EXISTENTE = {
    "telefono_bd": "+56931000001",
    "telefono_whatsapp": "56931000001",
    "nombre": "Carla Existente",
    "direccion": "Av. Libertad 1000, Viña del Mar",
    "latitud": Decimal("-33.024500"),
    "longitud": Decimal("-71.551800"),
}

TELEFONO_NUEVO_E = "56931000010"
TELEFONO_NUEVO_F = "56931000011"
TELEFONO_NUEVO_G = "56931000012"
TELEFONO_DUPLICADO = "56931000020"

TELEFONOS_PRUEBA = (
    CLIENTE_EXISTENTE["telefono_whatsapp"],
    TELEFONO_NUEVO_E,
    TELEFONO_NUEVO_F,
    TELEFONO_NUEVO_G,
    TELEFONO_DUPLICADO,
)

UBICACION_NUEVA = {"latitude": -33.047800, "longitude": -71.441200}

PRODUCTO = "Quiero 2 bidones de 20 litros recarga"


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


async def _crear_cliente_existente() -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        nombre=CLIENTE_EXISTENTE["nombre"],
        telefono=CLIENTE_EXISTENTE["telefono_bd"],
        direccion=CLIENTE_EXISTENTE["direccion"],
        latitud=CLIENTE_EXISTENTE["latitud"],
        longitud=CLIENTE_EXISTENTE["longitud"],
    )
    return result.scalar_one()


async def _restaurar_cliente_existente(cliente_id: int) -> None:
    await _ejecutar(
        "update cliente set direccion = :direccion, latitud = :latitud, longitud = :longitud where id = :id",
        id=cliente_id,
        direccion=CLIENTE_EXISTENTE["direccion"],
        latitud=CLIENTE_EXISTENTE["latitud"],
        longitud=CLIENTE_EXISTENTE["longitud"],
    )


async def _clientes_por_telefono(telefono: str) -> list[dict]:
    result = await _ejecutar(
        "select * from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') in (:t, :t_sin_56) order by id",
        t=telefono,
        t_sin_56=telefono[2:],
    )
    return [dict(fila._mapping) for fila in result]


async def _cliente(cliente_id: int) -> dict | None:
    result = await _ejecutar("select * from cliente where id = :id", id=cliente_id)
    fila = result.first()
    return dict(fila._mapping) if fila else None


async def _pedidos_de(cliente_id: int) -> list[dict]:
    result = await _ejecutar("select * from pedido where cliente_id = :id order by id", id=cliente_id)
    return [dict(fila._mapping) for fila in result]


async def _max_auditoria_id() -> int:
    result = await _ejecutar("select coalesce(max(id), 0) from auditoria")
    return result.scalar_one()


async def _auditorias_cliente_desde(cliente_id: int, desde_id: int) -> list[dict]:
    result = await _ejecutar(
        "select * from auditoria where entidad = 'cliente' and entidad_id = :id and id > :desde order by id",
        id=cliente_id,
        desde=desde_id,
    )
    return [dict(fila._mapping) for fila in result]


def _num(valor) -> Decimal | None:
    return None if valor is None else Decimal(str(valor)).quantize(Decimal("0.000001"))


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _conversar(phone: str, turnos: list) -> list[str]:
    """Cada turno es un texto o un dict {"ubicacion": {...}}."""
    respuestas = []
    for turno in turnos:
        if isinstance(turno, dict):
            print(f"  Cliente: [ubicación {turno['ubicacion']}]")
            respuesta = await procesar_mensaje(phone, "location", None, turno["ubicacion"])
        else:
            print(f"  Cliente: {turno}")
            respuesta = await procesar_mensaje(phone, "text", turno, None)
        paso = (get_draft(phone) or {}).get("paso")
        print(f"  Bot [{paso}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


def _pide_nombre(texto: str) -> bool:
    texto = texto.lower()
    return "a nombre de quién" in texto or "a nombre de quien" in texto or "tu nombre" in texto


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
    phone = CLIENTE_EXISTENTE["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["quiero hacer un pedido"])
    r = respuestas[0]
    v.check("Carla" in r, "saluda con su nombre")
    v.check("producto" in r.lower(), "pregunta el producto")
    v.check(not _pide_nombre(r), "no pide el nombre")
    v.check("algo más" not in r.lower(), "no pregunta 'algo más'")
    v.check((get_draft(phone) or {}).get("paso") == "producto", "draft en paso 'producto'")
    clear_draft(phone)


async def caso_b(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE_EXISTENTE["telefono_whatsapp"]
    cliente_id = ctx["cliente_existente_id"]
    antes = await _cliente(cliente_id)
    auditoria_desde = await _max_auditoria_id()

    respuestas = await _conversar(phone, [PRODUCTO, "sí", "no, eso es todo", "si"])
    v.check(f"¿Despachamos a {CLIENTE_EXISTENTE['direccion']}?" in respuestas[0], "pregunta '¿Despachamos a {dirección registrada}?'")
    v.check(not any(_pide_nombre(r) for r in respuestas), "nunca pide el nombre")
    v.check(not any("ubicación de whatsapp" in r.lower() for r in respuestas), "nunca pide ubicación")
    v.check("confirmado" in respuestas[-1].lower(), "pedido confirmado")

    pedidos = await _pedidos_de(cliente_id)
    pedido = pedidos[-1] if pedidos else {}
    v.check(pedido.get("direccion_despacho") == CLIENTE_EXISTENTE["direccion"], "pedido con la dirección registrada")
    v.check(
        _num(pedido.get("latitud")) == _num(CLIENTE_EXISTENTE["latitud"])
        and _num(pedido.get("longitud")) == _num(CLIENTE_EXISTENTE["longitud"]),
        "pedido con las coordenadas registradas",
    )
    despues = await _cliente(cliente_id)
    v.check(antes == despues, "cliente sin cambios")
    v.check(not await _auditorias_cliente_desde(cliente_id, auditoria_desde), "sin auditoría de cliente (no hubo cambios)")


async def _caso_direccion_nueva(ctx: dict, v: Verificador, con_ubicacion: bool) -> None:
    phone = CLIENTE_EXISTENTE["telefono_whatsapp"]
    cliente_id = ctx["cliente_existente_id"]
    await _restaurar_cliente_existente(cliente_id)
    auditoria_desde = await _max_auditoria_id()
    pedidos_antes = len(await _pedidos_de(cliente_id))

    tercer_turno = {"ubicacion": UBICACION_NUEVA} if con_ubicacion else "no puedo compartir la ubicación"
    respuestas = await _conversar(
        phone,
        [PRODUCTO, "no, es otra", "Los Pinos 456, Quilpué", tercer_turno, "no, eso es todo"],
    )
    v.check("nueva dirección" in respuestas[1].lower(), "tras 'no, es otra' pide la dirección nueva")
    v.check("ubicación" in respuestas[2].lower(), "tras la dirección pide la ubicación")
    v.check("Los Pinos 456" in respuestas[-1], "resumen con la dirección nueva")
    if not con_ubicacion:
        v.check("ubicación de whatsapp" not in respuestas[3].lower(), "tras el rechazo no vuelve a pedir la ubicación")
        v.check("ubicación" not in respuestas[-1].lower(), "el resumen no muestra la ubicación")
    respuestas += await _conversar(phone, ["si"])
    v.check("confirmado" in respuestas[-1].lower(), "pedido confirmado")
    v.check(not any(_pide_nombre(r) for r in respuestas), "nunca pide el nombre")

    pedidos = await _pedidos_de(cliente_id)
    v.check(len(pedidos) == pedidos_antes + 1, "se creó un pedido")
    pedido = pedidos[-1] if pedidos else {}
    cliente = await _cliente(cliente_id)
    v.check("Los Pinos 456" in (pedido.get("direccion_despacho") or ""), "pedido con la dirección nueva")
    v.check("Los Pinos 456" in (cliente.get("direccion") or ""), "cliente actualizado con la dirección nueva")
    if con_ubicacion:
        esperado = (_num(UBICACION_NUEVA["latitude"]), _num(UBICACION_NUEVA["longitude"]))
    else:
        esperado = (None, None)
    v.check((_num(pedido.get("latitud")), _num(pedido.get("longitud"))) == esperado, f"pedido con coordenadas {esperado}")
    v.check((_num(cliente.get("latitud")), _num(cliente.get("longitud"))) == esperado, f"cliente con coordenadas {esperado}")
    auditorias = await _auditorias_cliente_desde(cliente_id, auditoria_desde)
    v.check(
        len(auditorias) == 1
        and auditorias[0]["accion"] == "actualizar"
        and auditorias[0]["antes"]["direccion"] == CLIENTE_EXISTENTE["direccion"]
        and "Los Pinos 456" in auditorias[0]["despues"]["direccion"],
        "auditoría 'actualizar' con antes/después",
    )
    await _restaurar_cliente_existente(cliente_id)


async def caso_c(ctx: dict, v: Verificador) -> None:
    await _caso_direccion_nueva(ctx, v, con_ubicacion=True)


async def caso_d(ctx: dict, v: Verificador) -> None:
    await _caso_direccion_nueva(ctx, v, con_ubicacion=False)


async def caso_e(ctx: dict, v: Verificador) -> None:
    phone = TELEFONO_NUEVO_E
    auditoria_desde = await _max_auditoria_id()
    respuestas = await _conversar(
        phone,
        [PRODUCTO, "Ana Pérez", "Calle Nueva 789, Valparaíso", {"ubicacion": UBICACION_NUEVA}, "no, eso es todo"],
    )
    resumen = respuestas[-1]
    v.check("Ana Pérez" in resumen and "Calle Nueva 789" in resumen, "resumen con nombre y dirección")
    respuestas += await _conversar(phone, ["si"])
    v.check(sum(_pide_nombre(r) for r in respuestas) == 1, "pide el nombre exactamente una vez")
    v.check("confirmado" in respuestas[-1].lower(), "pedido confirmado")

    clientes = await _clientes_por_telefono(phone)
    v.check(len(clientes) == 1, "cliente creado")
    if not clientes:
        return
    cliente = clientes[0]
    v.check(cliente["telefono"] == phone, "teléfono normalizado")
    v.check(cliente["nombre"] == "Ana Pérez", "nombre guardado")
    v.check("Calle Nueva 789" in (cliente["direccion"] or ""), "dirección guardada")
    v.check(
        (_num(cliente["latitud"]), _num(cliente["longitud"]))
        == (_num(UBICACION_NUEVA["latitude"]), _num(UBICACION_NUEVA["longitude"])),
        "coordenadas guardadas",
    )
    v.check(cliente["activo"] is True and cliente["opt_out_whatsapp"] is False, "activo=true, opt_out_whatsapp=false")
    pedidos = await _pedidos_de(cliente["id"])
    v.check(len(pedidos) == 1 and "Calle Nueva 789" in (pedidos[0]["direccion_despacho"] or ""), "pedido asociado al cliente nuevo")
    auditorias = await _auditorias_cliente_desde(cliente["id"], auditoria_desde)
    v.check(len(auditorias) == 1 and auditorias[0]["accion"] == "crear", "auditoría 'crear'")


async def caso_f(ctx: dict, v: Verificador) -> None:
    respuestas = await _conversar(TELEFONO_NUEVO_F, [PRODUCTO, "Luis Soto", "cancelar"])
    v.check("cancel" in respuestas[-1].lower(), "nuevo: cancelación confirmada")
    v.check(not await _clientes_por_telefono(TELEFONO_NUEVO_F), "nuevo: cliente no creado")

    phone = CLIENTE_EXISTENTE["telefono_whatsapp"]
    cliente_id = ctx["cliente_existente_id"]
    antes = await _cliente(cliente_id)
    pedidos_antes = len(await _pedidos_de(cliente_id))
    respuestas = await _conversar(phone, [PRODUCTO, "no, es otra", "Los Pinos 456, Quilpué", {"ubicacion": UBICACION_NUEVA}, "cancelar"])
    v.check("cancel" in respuestas[-1].lower(), "existente: cancelación confirmada")
    v.check(await _cliente(cliente_id) == antes, "existente: cliente sin cambios")
    v.check(len(await _pedidos_de(cliente_id)) == pedidos_antes, "existente: sin pedido nuevo")
    v.check(get_draft(phone) is None, "existente: draft limpio")


async def caso_g(ctx: dict, v: Verificador) -> None:
    respuestas = await _conversar(TELEFONO_NUEVO_G, ["quiero hacer un pedido"])
    r = respuestas[0]
    v.check("algo más" not in r.lower(), "no pregunta 'algo más'")
    v.check("producto" in r.lower(), "pregunta el producto")
    v.check("resumen" not in r.lower(), "no muestra resumen")
    v.check(not _pide_nombre(r) and "dirección" not in r.lower(), "no salta a nombre ni dirección")
    respuestas = await _conversar(TELEFONO_NUEVO_G, ["no me preguntaste qué quiero"])
    v.check("algo más" not in respuestas[0].lower() and "producto" in respuestas[0].lower(), "sigue en el paso producto")
    clear_draft(TELEFONO_NUEVO_G)


async def caso_h(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE_EXISTENTE["telefono_whatsapp"]
    respuestas = await _conversar(phone, [PRODUCTO, "sí", "no, eso es todo"])
    resumen = respuestas[-1]
    v.check("Resumen de tu pedido" in resumen, "muestra el resumen")
    v.check(f"Nombre: {CLIENTE_EXISTENTE['nombre']}" in resumen, "incluye el nombre")
    v.check(f"Dirección de despacho: {CLIENTE_EXISTENTE['direccion']}" in resumen, "incluye la dirección")
    v.check("Ubicación" not in resumen, "no muestra la ubicación")
    v.check("Total:" in resumen and "Bidón 20L Recarga" in resumen, "incluye productos y total")
    await _conversar(phone, ["cancelar"])


async def caso_i(ctx: dict, v: Verificador) -> None:
    await _ejecutar(
        "insert into cliente (nombre, telefono) values ('Duplicado Uno', :t1), ('Duplicado Dos', :t2)",
        t1=TELEFONO_DUPLICADO,
        t2="+56 9 " + TELEFONO_DUPLICADO[3:7] + " " + TELEFONO_DUPLICADO[7:],
    )
    enviados = []

    async def fake_send(to: str, message: str):
        enviados.append((to, message))

    original = order_flow.send_whatsapp_message
    order_flow.send_whatsapp_message = fake_send
    try:
        respuestas = await _conversar(TELEFONO_DUPLICADO, [PRODUCTO])
    finally:
        order_flow.send_whatsapp_message = original
    v.check("ejecutiva" in respuestas[0].lower(), "avisa al cliente que lo contactará una ejecutiva")
    v.check(len(enviados) == 1 and enviados[0][0] == settings.EJECUTIVA_PHONE, "notifica a la ejecutiva")
    v.check(get_draft(TELEFONO_DUPLICADO) is None, "no deja draft (no toma el pedido)")


async def caso_j(ctx: dict, v: Verificador) -> None:
    """Falla a mitad de _confirmar_pedido (producto inexistente al crear el
    detalle, DESPUÉS de crear el cliente y su auditoría en la sesión): debe
    hacer rollback de todo. El producto del draft es válido (con uno inválido
    el flujo ni siquiera llega a confirmar, ver _datos_faltantes); el
    inexistente va solo en resumen.lineas, que es lo que usa el detalle."""
    phone = TELEFONO_NUEVO_F
    auditoria_desde = await _max_auditoria_id()
    save_draft(
        phone,
        {
            "productos": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 1}],
            "nombre_cliente": "Rollback Test",
            "usa_direccion_habitual": False,
            "direccion_texto": "Calle Rollback 1",
            "ubicacion": None,
            "ubicacion_rechazada": True,
            "algo_mas_respondido": True,
            "paso": "confirmacion",
            "estado": "esperando_confirmacion",
            "resumen": {
                "lineas": [{"nombre": "Producto Inexistente", "cantidad": 1, "precio_unitario": 1000.0}],
                "total": 1000.0,
            },
        },
    )
    respuestas = await _conversar(phone, ["si"])
    v.check("problema" in respuestas[0].lower(), "responde con el mensaje de error")
    v.check(not await _clientes_por_telefono(phone), "no queda cliente creado (rollback)")
    result = await _ejecutar(
        "select count(*) from auditoria where id > :desde and despues->>'nombre' = 'Rollback Test'",
        desde=auditoria_desde,
    )
    v.check(result.scalar_one() == 0, "no queda auditoría (rollback)")
    clear_draft(phone)


async def caso_k(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE_EXISTENTE["telefono_whatsapp"]
    respuestas = await _conversar(
        phone, [PRODUCTO, "sí", "Si", "también 1 dispensador USB", "no, nada más"]
    )
    v.check("algo más" in respuestas[1].lower(), "pregunta '¿algo más?'")
    v.check("resumen" not in respuestas[2].lower() and "agregar" in respuestas[2].lower(), "'Si' a '¿algo más?' pregunta qué agregar (no cierra)")
    v.check("algo más" in respuestas[3].lower(), "tras agregar vuelve a preguntar '¿algo más?'")
    v.check(
        "Bidón 20L Recarga" in respuestas[4] and "Dispensador USB" in respuestas[4],
        "resumen con ambos productos",
    )
    await _conversar(phone, ["cancelar"])


CASOS = [
    ("a", "Cliente existente 'quiero hacer un pedido': saludo con nombre, pide producto, no pide nombre", caso_a),
    ("b", "Cliente existente confirma su dirección: pedido con dirección/coords guardadas, cliente sin cambios", caso_b),
    ("c", "Cliente existente da dirección nueva + ubicación: pedido y cliente con la nueva", caso_c),
    ("d", "Cliente existente da dirección nueva y rechaza ubicación: cliente con coords null", caso_d),
    ("e", "Cliente nuevo: nombre pedido una vez, cliente creado con todos sus datos", caso_e),
    ("f", "Cancelación antes de confirmar (nuevo y existente): cliente no se crea ni modifica", caso_f),
    ("g", "'quiero hacer un pedido' sin producto: no pregunta 'algo más', pregunta producto", caso_g),
    ("h", "Resumen incluye nombre y dirección (sin ubicación)", caso_h),
    ("i", "Teléfono en más de un cliente: escala a la ejecutiva", caso_i),
    ("j", "Falla al crear el pedido: rollback, no queda cliente ni auditoría", caso_j),
    ("k", "Paso '¿algo más?': 'Si' pide qué agregar, se agrega producto, 'no' muestra resumen", caso_k),
]


async def main() -> None:
    await _limpiar_rango()
    ctx = {"cliente_existente_id": await _crear_cliente_existente()}
    print(f"Cliente existente de prueba: id={ctx['cliente_existente_id']} {CLIENTE_EXISTENTE}")

    resultados = []
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

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
