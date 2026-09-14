"""
Script temporal para simular una conversación completa a través de procesar_mensaje.
No es parte del código final: solo para validar manualmente el flujo de pedidos.

Nota: procesar_mensaje ahora determina es_cliente_nuevo consultando la BD por
telefono == PHONE. Este script simula un cliente EXISTENTE, así que PHONE debe
corresponder a un Cliente ya creado (por ejemplo, uno insertado por una corrida
previa de scripts/test_order_flow_nuevo.py); si no existe, el flujo tratará a
este número como cliente nuevo y pedirá ubicación/nombre en vez de preguntar
por la dirección habitual.

Uso: python -m scripts.test_order_flow
"""

import asyncio

from app.services.draft_store import get_draft
from app.services.order_flow import procesar_mensaje

PHONE = "56900000000"

TURNOS = [
    {"message_type": "text", "message_text": "Quiero 2 bidones de 20 litros"},
    {"message_type": "text", "message_text": "Recarga"},
    {"message_type": "text", "message_text": "Sí, uso mi dirección habitual"},
    {"message_type": "text", "message_text": "Si"},
]


async def main() -> None:
    for i, turno in enumerate(TURNOS, start=1):
        print("=" * 70)
        print(f"Turno {i}/{len(TURNOS)}")
        print(f"Cliente: {turno['message_text']}")
        print("-" * 70)

        try:
            respuesta = await procesar_mensaje(
                phone=PHONE,
                message_type=turno["message_type"],
                message_text=turno["message_text"],
                location=None,
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
