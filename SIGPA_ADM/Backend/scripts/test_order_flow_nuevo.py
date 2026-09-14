"""
Script temporal para simular una conversación completa de un cliente NUEVO
a través de procesar_mensaje.
No es parte del código final: solo para validar manualmente el flujo de pedidos.

Nota: procesar_mensaje ahora determina es_cliente_nuevo consultando la BD por
telefono == PHONE, así que PHONE debe ser un número que todavía no exista como
Cliente (si ya corriste este script antes y el pedido quedó confirmado, el
Cliente ya existe: usa otro número o límpialo en la BD antes de reintentar).

Uso: python -m scripts.test_order_flow_nuevo
"""

import asyncio

from app.services.draft_store import get_draft
from app.services.order_flow import procesar_mensaje

PHONE = "56911111111"

TURNOS = [
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
        "message_text": "Felipe Gaete",
        "location": None,
    },
    {
        "message_type": "text",
        "message_text": "Calle Falsa 123",
        "location": None,
    },
    {
        "message_type": "text",
        "message_text": "Si",
        "location": None,
    },
]


def _descripcion_turno(turno: dict) -> str:
    if turno["message_type"] == "location":
        lat = turno["location"]["latitude"]
        lon = turno["location"]["longitude"]
        return f"[Ubicación compartida: lat={lat}, lon={lon}]"
    return turno["message_text"]


async def main() -> None:
    for i, turno in enumerate(TURNOS, start=1):
        print("=" * 70)
        print(f"Turno {i}/{len(TURNOS)}")
        print(f"Cliente: {_descripcion_turno(turno)}")
        print("-" * 70)

        try:
            respuesta = await procesar_mensaje(
                phone=PHONE,
                message_type=turno["message_type"],
                message_text=turno["message_text"],
                location=turno["location"],
            )
            print(f"Bot: {respuesta}")
        except Exception as exc:
            print("[ERROR] Falló procesar_mensaje para este turno:")
            print(repr(exc))

        print()

    print("=" * 70)
    print("Estado final del draft (debería ser None si el pedido quedó confirmado):")
    print(get_draft(PHONE))

    print()
    print("=" * 70)
    print("Fin de las pruebas.")


if __name__ == "__main__":
    asyncio.run(main())
