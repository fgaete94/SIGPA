"""
Script temporal para simular, a través de procesar_mensaje, el nuevo paso
obligatorio "¿Deseas agregar algo más a tu pedido?" antes de marcar
pedido_completo=true (ver SYSTEM_PROMPT en app/services/agent_service.py).
No es parte del código final: solo para validar manualmente el comportamiento.

Nota: procesar_mensaje determina es_cliente_nuevo consultando la BD por
telefono == PHONE, así que ambos números usados aquí deben corresponder a
Clientes ya existentes (56900000000 y 56911111111, insertados por corridas
previas de scripts/test_order_flow.py y scripts/test_order_flow_nuevo.py).
El número 56922222222 sugerido en la consigna original NO existe todavía
como Cliente, así que se reemplazó por 56911111111 para mantener el foco en
el flujo de "algo más" de un cliente existente, sin mezclarlo con el flujo
de datos de cliente nuevo (nombre/dirección/ubicación).

Nota 2: los borradores de pedido (draft_store) viven solo en memoria del
proceso, así que cada corrida de este script empieza sin borrador previo.
Por eso, en el primer turno de cada conversación, un cliente existente
todavía debe pasar por la pregunta de confirmación de dirección habitual
(ver "SECUENCIA OBLIGATORIA" del SYSTEM_PROMPT) antes de llegar al paso
"¿algo más?"; ese turno extra se incluye explícitamente en cada conversación
más abajo para que el pedido efectivamente llegue a completarse.

Uso: python -m scripts.test_algo_mas
"""

import asyncio

from app.services.draft_store import get_draft
from app.services.order_flow import procesar_mensaje

CONVERSACIONES = [
    {
        "descripcion": "Cliente existente confirma dirección habitual y no agrega nada más",
        "phone": "56900000000",
        "turnos": [
            "Quiero 1 bidón de 20 litros recarga",
            "Sí, uso mi dirección habitual",
            "No, eso es todo",
        ],
    },
    {
        "descripcion": (
            "Cliente existente agrega un producto extra al responder \"¿algo más?\" "
            "y luego confirma que ya no quiere nada más"
        ),
        "phone": "56911111111",
        "turnos": [
            "Quiero 1 dispensador USB",
            "Sí, uso mi dirección habitual",
            "También agrégame 2 bidones de 12 litros nuevo",
            "No, nada más",
        ],
    },
]


async def main() -> None:
    for i, conversacion in enumerate(CONVERSACIONES, start=1):
        phone = conversacion["phone"]
        print("#" * 70)
        print(f"Conversación {i}/{len(CONVERSACIONES)}: {conversacion['descripcion']}")
        print(f"Phone: {phone}")
        print("#" * 70)
        print()

        for j, mensaje in enumerate(conversacion["turnos"], start=1):
            print("=" * 70)
            print(f"Turno {j}/{len(conversacion['turnos'])}")
            print(f"Cliente: {mensaje}")
            print("-" * 70)

            try:
                respuesta = await procesar_mensaje(
                    phone=phone,
                    message_type="text",
                    message_text=mensaje,
                    location=None,
                )
                print(f"Bot: {respuesta}")
            except Exception as exc:
                print("[ERROR] Falló procesar_mensaje para este turno:")
                print(repr(exc))

            print()

        print("-" * 70)
        print(f"Estado final del draft para {phone} (debería ser None si el pedido se confirmó):")
        print(get_draft(phone))
        print()

    print("=" * 70)
    print("Fin de las pruebas.")


if __name__ == "__main__":
    asyncio.run(main())
