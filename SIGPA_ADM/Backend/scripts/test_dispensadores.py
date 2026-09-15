"""
Script temporal para probar interpret_message con los dispensadores del catálogo.
No es parte del código final: solo para validar manualmente el comportamiento.

Uso: python -m scripts.test_dispensadores
"""

import asyncio
import json

from app.services.agent_service import interpret_message

CASOS_PRUEBA = [
    {
        "descripcion": "Pedido ambiguo: falta el modelo de dispensador",
        "phone": "56922222222",
        "message": "Quiero un dispensador",
    },
    {
        "descripcion": "Pedido directo, sin ambigüedad",
        "phone": "56922222222",
        "message": "Quiero un dispensador USB",
    },
    {
        "descripcion": "Consulta de precio de un modelo específico",
        "phone": "56922222222",
        "message": "¿Cuánto cuesta el dispensador básico?",
    },
]


async def main() -> None:
    for i, caso in enumerate(CASOS_PRUEBA, start=1):
        print("=" * 70)
        print(f"Caso {i}/{len(CASOS_PRUEBA)}: {caso['descripcion']}")
        print(f"Mensaje: {caso['message']}")
        print("-" * 70)

        try:
            resultado = await interpret_message(
                phone=caso["phone"],
                message=caso["message"],
                es_cliente_nuevo=False,
                context=None,
            )
            print(json.dumps(resultado, ensure_ascii=False, indent=2))
        except Exception as exc:
            print("[ERROR] Falló interpret_message para este caso:")
            print(repr(exc))

        print()

    print("=" * 70)
    print("Fin de las pruebas.")


if __name__ == "__main__":
    asyncio.run(main())
