"""
Script temporal para probar interpret_message contra distintos casos de mensajes.
No es parte del código final: solo para validar manualmente el comportamiento del agente.

Uso: python -m scripts.test_agent
"""

import asyncio
import json

from app.services.agent_service import interpret_message

MENSAJES_PRUEBA = [
    "Hola, quiero un bidón de 20 litros",
    "Necesito 2 bidones de 20 litros recarga y 1 de 12 nuevo",
    "¿Cuánto cuesta el bidón de 12 litros?",
    "¿Tengo pedidos pendientes?",
    "Necesito una máquina dispensadora",
    "Ayúdame a escribir un poema",
    "Hola",
    "¿Qué productos tienen disponibles y a cuánto?",
]


async def main() -> None:
    for i, mensaje in enumerate(MENSAJES_PRUEBA, start=1):
        print("=" * 70)
        print(f"Caso {i}/{len(MENSAJES_PRUEBA)}")
        print(f"Mensaje: {mensaje}")
        print("-" * 70)

        try:
            resultado = await interpret_message(
                phone="56900000000",
                message=mensaje,
                es_cliente_nuevo=False,
                context=None,
            )
            print(json.dumps(resultado, ensure_ascii=False, indent=2))
        except Exception as exc:
            print("[ERROR] Falló interpret_message para este mensaje:")
            print(repr(exc))

        print()

    print("=" * 70)
    print("Fin de las pruebas.")


if __name__ == "__main__":
    asyncio.run(main())
