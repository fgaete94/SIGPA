"""
Script temporal para validar que el bot confirma lo que cambió en el pedido
(_texto_cambios_pedido en app/services/order_flow.py) en vez de repetir
"¿Deseas agregar algo más a tu pedido?" como si no hubiera hecho nada (en
WhatsApp parecía un loop), a través de procesar_mensaje y contra la BD.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 569333002xx.
Al empezar y al terminar, el script BORRA de ese rango los clientes (con sus
pedidos y detalles), los mensajes de mensaje_whatsapp y las filas de
conversacion_bot. No toca ningún otro cliente ni pedido. Las filas de
auditoria no se borran (son el registro histórico).

No envía mensajes: procesar_mensaje devuelve el texto en vez de enviarlo.

Uso: python -m scripts.test_confirmacion_cambios
"""

import asyncio

from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import PREGUNTA_ALGO_MAS, _texto_cambios_pedido, procesar_mensaje

RANGO_TELEFONOS_SQL = r"^(56)?9333002\d{2}$"

CLIENTE = {
    "telefono": "56933300201",
    "nombre": "Cambios Prueba",
    "direccion": "Calle Confirmación 77, Quilpué",
}


# --------------------------------------------------------------------------
# Helpers
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
    clear_draft(CLIENTE["telefono"])


async def _crear_cliente() -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion) values (:nombre, :telefono, :direccion) returning id",
        **CLIENTE,
    )
    return result.scalar_one()


async def _conversar(turnos: list[str]) -> list[str]:
    phone = CLIENTE["telefono"]
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        draft = get_draft(phone) or {}
        print(f"  Bot [{draft.get('paso')}/{draft.get('estado')}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


def _es_resumen(texto: str) -> bool:
    return "Resumen de tu pedido" in texto and "¿Confirmas el pedido?" in texto


def _draft(*lineas: tuple[str, int], **extra) -> dict:
    return {"productos": [{"nombre_producto": n, "cantidad": c} for n, c in lineas], **extra}


class Verificador:
    def __init__(self) -> None:
        self.errores: list[str] = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'OK' if condicion else 'FALLO'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


B12N = ("Bidón 12L Nuevo", 2)
B20R = ("Bidón 20L Recarga", 5)
DISP_BASICO = ("Dispensador Básico", 1)
DISP_USB = ("Dispensador USB", 1)


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    esperados = [
        ("agregar uno", _draft(B12N, B20R), _draft(B12N, B20R, DISP_BASICO), "Agregué 1x Dispensador Básico."),
        (
            "agregar varios",
            _draft(),
            _draft(("Bidón 20L Recarga", 2), DISP_USB),
            "Agregué 2x Bidón 20L Recarga y 1x Dispensador USB.",
        ),
        ("quitar una línea", _draft(B12N, B20R), _draft(B20R), "Quité 2x Bidón 12L Nuevo."),
        (
            "quitar y agregar",
            _draft(B12N, B20R),
            _draft(B20R, DISP_USB),
            "Listo: quité 2x Bidón 12L Nuevo y agregué 1x Dispensador USB.",
        ),
        ("cambio de cantidad", _draft(B12N, ("Bidón 20L Recarga", 3)), _draft(B12N, B20R), "Dejé 5x Bidón 20L Recarga."),
        ("dispensador Básico -> USB", _draft(DISP_BASICO), _draft(DISP_USB), "Cambié el Dispensador Básico por Dispensador USB."),
        (
            "quitar y cambiar (conversación real)",
            _draft(B12N, B20R, DISP_BASICO),
            _draft(B20R, DISP_USB),
            "Listo: quité 2x Bidón 12L Nuevo y cambié el Dispensador Básico por Dispensador USB.",
        ),
        (
            "cambio de capacidad",
            _draft(("Bidón 12L Recarga", 3)),
            _draft(("Bidón 20L Recarga", 3)),
            "Cambié 3x Bidón 12L Recarga por 3x Bidón 20L Recarga.",
        ),
        ("sin cambios", _draft(B12N, B20R), _draft(B12N, B20R), None),
        (
            "pendientes de aclarar no se anuncian",
            _draft(B20R),
            _draft(
                B20R,
                aclaracion_pendiente={"capacidad_litros": 12, "cantidad": 1},
                pendientes_modelo=[{"familia": "Dispensador", "cantidad": 1}],
            ),
            None,
        ),
    ]
    for descripcion, previo, nuevo, esperado in esperados:
        obtenido = _texto_cambios_pedido(previo, nuevo)
        v.check(obtenido == esperado, f"{descripcion}: {obtenido!r}")


async def caso_b(ctx: dict, v: Verificador) -> None:
    """Conversación real de WhatsApp: después de cada cambio el bot dice qué
    hizo antes de "¿algo más?"."""
    respuestas = await _conversar(["2 de 12 nuevo y 5 de 20 recarga", "si"])
    v.check(respuestas[1] == PREGUNTA_ALGO_MAS, "primera llegada a '¿algo más?': texto exacto de siempre")

    respuesta = (await _conversar(["si un dispensador basico"]))[0]
    v.check(
        "Agregué 1x Dispensador Básico" in respuesta and respuesta.endswith(PREGUNTA_ALGO_MAS),
        "confirma que agregó el dispensador y pregunta '¿algo más?'",
    )

    respuesta = (await _conversar(["no"]))[0]
    v.check(_es_resumen(respuesta) and "1x Dispensador Básico" in respuesta, "resumen con el dispensador básico")

    respuesta = (await _conversar(["mejor borra los de 12 y cambia el dispensador por uno usb"]))[0]
    v.check(
        "quité 2x Bidón 12L Nuevo" in respuesta or "Quité 2x Bidón 12L Nuevo" in respuesta,
        "confirma que quitó los de 12L",
    )
    v.check(
        "Dispensador Básico por Dispensador USB" in respuesta,
        "confirma que cambió el dispensador a USB",
    )
    v.check(respuesta != PREGUNTA_ALGO_MAS, "no repite la pregunta sola")

    respuesta = (await _conversar(["no"]))[0]
    v.check(
        _es_resumen(respuesta)
        and "5x Bidón 20L Recarga" in respuesta
        and "1x Dispensador USB" in respuesta
        and "12L" not in respuesta
        and "Básico" not in respuesta,
        "resumen con 5x 20L Recarga y 1x Dispensador USB",
    )

    respuesta = (await _conversar(["si"]))[0]
    v.check("confirmado" in respuesta, "'si' confirma el pedido")
    result = await _ejecutar(
        "select p.nombre, d.cantidad_solicitada from detalle_pedido d join producto p on p.id = d.producto_id "
        "join pedido pe on pe.id = d.pedido_id where pe.cliente_id = :id",
        id=ctx["cliente_id"],
    )
    detalles = {nombre: cantidad for nombre, cantidad in result.all()}
    v.check(
        detalles == {"Bidón 20L Recarga": 5, "Dispensador USB": 1},
        f"pedido en la BD con 5x 20L Recarga y 1x Dispensador USB: {detalles}",
    )


async def caso_c(ctx: dict, v: Verificador) -> None:
    """Llegar a "¿algo más?" sin cambiar productos en ese turno (confirmando
    la dirección) mantiene el texto exacto."""
    respuestas = await _conversar(["quiero 1 dispensador usb", "si"])
    v.check(CLIENTE["direccion"] in respuestas[0], "pregunta la dirección")
    v.check(respuestas[1] == PREGUNTA_ALGO_MAS, f"texto exacto: {respuestas[1]!r}")
    await _conversar(["cancelar"])


CASOS = [
    ("a", "_texto_cambios_pedido: agregar, quitar, cantidad, cambio, sin cambios, pendientes", caso_a),
    ("b", "Conversación real: confirma lo agregado, quitado y cambiado; pedido en BD", caso_b),
    ("c", "Primera llegada a '¿algo más?' sin cambios: texto exacto", caso_c),
]


async def main() -> None:
    await _limpiar_rango()
    resultados = []
    try:
        ctx = {"cliente_id": await _crear_cliente()}
        print(f"Cliente de prueba: id={ctx['cliente_id']} {CLIENTE}")
        for letra, descripcion, caso in CASOS:
            print("#" * 70)
            print(f"Caso {letra}: {descripcion}")
            print("#" * 70)
            clear_draft(CLIENTE["telefono"])
            v = Verificador()
            try:
                await caso(ctx, v)
            except Exception as exc:
                print(f"    [ERROR] {exc!r}")
                v.errores.append(repr(exc))
            resultados.append((letra, descripcion, not v.errores))
            print()
    finally:
        await _limpiar_rango()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
