"""
Script temporal para validar manualmente que el lock por teléfono
(app/services/draft_store.get_lock) y la verificación de idempotencia en
_confirmar_pedido evitan que un reintento de webhook duplicado (dos mensajes
"Si" que llegan "al mismo tiempo") termine creando dos pedidos.
No es parte del código final.

PHONE debe ser un número que todavía no exista como Cliente. Al empezar, si
quedó un cliente de una corrida anterior abortada (se reconoce por la
dirección de prueba) se borra; si el número pertenece a otro cliente, el
script aborta sin tocarlo. Al terminar (también si falla) borra el cliente
que creó, con su pedido, detalles, mensajes, conversación y borrador. Las
filas de auditoria no se borran (son el registro histórico).

Uso: python -m scripts.test_concurrencia
"""

import asyncio

from sqlalchemy import func, select, text

from app.core.database import SessionLocal
from app.models import Cliente, Pedido
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import procesar_mensaje

PHONE = "56955556666"
# Dirección que el script le da al cliente que crea (ver TURNOS_BASE).
DIRECCION_PRUEBA = "Calle Falsa 123"

# Cliente nuevo: productos, ubicación, dirección y nombre (en ese orden)
# hasta antes de la confirmación final. Nota: a diferencia de
# test_order_flow_nuevo.py (que pide el nombre ANTES que la dirección), aquí
# se pide la dirección primero — con gpt-4o-mini y temperature=0 se
# comprobó que pedir el nombre antes que la dirección hace que el modelo
# vuelva a pedir la ubicación por error en el turno siguiente (bug del
# prompt/modelo, reproducible y no relacionado con el lock), aunque el
# contexto ya indica que la ubicación fue recibida.
TURNOS_BASE = [
    {
        "message_type": "text",
        "message_text": "Hola, quiero 1 bidón de 12 litros nuevo",
        "location": None,
    },
    {
        "message_type": "location",
        "message_text": None,
        "location": {"latitude": -33.0472, "longitude": -71.6127},
    },
    {
        "message_type": "text",
        "message_text": DIRECCION_PRUEBA,
        "location": None,
    },
    {
        "message_type": "text",
        "message_text": "Felipe Gaete",
        "location": None,
    },
]

RESPUESTA_ALGO_MAS = "No, eso sería todo"

MAX_TURNOS_ALGO_MAS = 3


def _descripcion_turno(turno: dict) -> str:
    if turno["message_type"] == "location":
        lat = turno["location"]["latitude"]
        lon = turno["location"]["longitude"]
        return f"[Ubicación compartida: lat={lat}, lon={lon}]"
    return turno["message_text"]


async def _enviar_turno(turno: dict) -> str:
    print("-" * 70)
    print(f"Cliente: {_descripcion_turno(turno)}")
    respuesta = await procesar_mensaje(
        phone=PHONE,
        message_type=turno["message_type"],
        message_text=turno["message_text"],
        location=turno["location"],
    )
    print(f"Bot: {respuesta}")
    return respuesta


async def _llegar_a_esperando_confirmacion() -> None:
    """Envía los turnos base y, si hace falta, responde "no" a la pregunta
    de "¿algo más?" hasta que el draft quede en estado esperando_confirmacion.
    Se maneja de forma dinámica (en vez de asumir un número fijo de turnos)
    porque el paso "¿algo más?" lo decide el LLM, no el código."""
    for turno in TURNOS_BASE:
        await _enviar_turno(turno)

    intentos = 0
    while True:
        draft = get_draft(PHONE)
        estado = draft.get("estado") if draft else None

        if estado == "esperando_confirmacion":
            return

        if estado == "armando" and intentos < MAX_TURNOS_ALGO_MAS:
            intentos += 1
            await _enviar_turno(
                {
                    "message_type": "text",
                    "message_text": RESPUESTA_ALGO_MAS,
                    "location": None,
                }
            )
            continue

        raise RuntimeError(
            f"No se alcanzó el estado 'esperando_confirmacion' (estado actual: {estado!r}, "
            f"draft: {draft!r})"
        )


async def _contar_pedidos(phone: str) -> int:
    async with SessionLocal() as session:
        result = await session.execute(select(Cliente).where(Cliente.telefono == phone))
        cliente = result.scalar_one_or_none()

        if cliente is None:
            return 0

        result = await session.execute(
            select(func.count()).select_from(Pedido).where(Pedido.cliente_id == cliente.id)
        )
        return result.scalar_one()


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


_CLIENTES_SQL = (
    "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') in (:p, :p_sin_56)"
)


async def _limpiar() -> None:
    """Borra el cliente de PHONE con todo lo asociado (auditoria no)."""
    params = {"p": PHONE, "p_sin_56": PHONE[2:]}
    pedidos_sql = f"select id from pedido where cliente_id in ({_CLIENTES_SQL})"
    for sql in (
        f"delete from detalle_pedido where pedido_id in ({pedidos_sql})",
        f"delete from mensaje_whatsapp where pedido_id in ({pedidos_sql}) or cliente_id in ({_CLIENTES_SQL}) "
        "or telefono in (:p, :p_sin_56)",
        f"delete from pedido where cliente_id in ({_CLIENTES_SQL})",
        f"delete from cliente_producto_habitual where cliente_id in ({_CLIENTES_SQL})",
        f"delete from cliente where id in ({_CLIENTES_SQL})",
        "delete from conversacion_bot where telefono in (:p, :p_sin_56)",
        "delete from borrador_pedido where telefono in (:p, :p_sin_56)",
    ):
        await _ejecutar(sql, **params)
    clear_draft(PHONE)


async def _preparar() -> None:
    """Deja el número libre. Solo borra un cliente previo si es un residuo de
    este script (dirección de prueba); si es de otra persona, aborta."""
    existentes = (
        await _ejecutar(
            "select id, nombre, direccion from cliente "
            "where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') in (:p, :p_sin_56)",
            p=PHONE,
            p_sin_56=PHONE[2:],
        )
    ).all()
    ajenos = [c for c in existentes if c.direccion != DIRECCION_PRUEBA]
    if ajenos:
        raise SystemExit(
            f"ABORTADO: el teléfono {PHONE} ya pertenece a un cliente que no es de este script: "
            f"{[(c.id, c.nombre) for c in ajenos]}. No se toca."
        )
    await _limpiar()


async def main() -> None:
    await _preparar()
    try:
        await _correr()
    finally:
        await _limpiar()


async def _correr() -> None:
    print("=" * 70)
    print(f"Preparando pedido para phone={PHONE} hasta 'esperando_confirmacion'")
    print("=" * 70)
    await _llegar_a_esperando_confirmacion()

    print()
    print("=" * 70)
    print("Draft en esperando_confirmacion. Disparando 2 mensajes 'Si' EN PARALELO")
    print("(simulando un reintento de entrega de Meta)")
    print("=" * 70)

    respuesta_1, respuesta_2 = await asyncio.gather(
        procesar_mensaje(phone=PHONE, message_type="text", message_text="Si", location=None),
        procesar_mensaje(phone=PHONE, message_type="text", message_text="Si", location=None),
    )

    print(f"Respuesta 1: {respuesta_1}")
    print(f"Respuesta 2: {respuesta_2}")

    print()
    print("=" * 70)
    print("Verificando cuántos Pedido quedaron creados en la BD")
    print("=" * 70)

    total_pedidos = await _contar_pedidos(PHONE)
    print(f"Pedidos encontrados para {PHONE}: {total_pedidos}")

    if total_pedidos == 1:
        print("OK: se creó exactamente 1 pedido, el lock/idempotencia funcionaron.")
    else:
        print(f"FALLO: se esperaba 1 pedido, se encontraron {total_pedidos}.")


if __name__ == "__main__":
    asyncio.run(main())
