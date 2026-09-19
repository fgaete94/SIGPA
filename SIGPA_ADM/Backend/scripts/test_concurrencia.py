"""
Script temporal para validar manualmente que el lock por teléfono
(app/services/draft_store.get_lock) y la verificación de idempotencia en
_confirmar_pedido evitan que un reintento de webhook duplicado (dos mensajes
"Si" que llegan "al mismo tiempo") termine creando dos pedidos.
No es parte del código final.

Nota: PHONE debe ser un número que todavía no exista como Cliente (si ya
corriste este script antes y el pedido quedó confirmado, usa otro número o
límpialo en la BD antes de reintentar).

Uso: python -m scripts.test_concurrencia
"""

import asyncio

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import Cliente, Pedido
from app.services.draft_store import get_draft
from app.services.order_flow import procesar_mensaje

PHONE = "56955556666"

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
        "message_text": "Calle Falsa 123",
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


async def main() -> None:
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
