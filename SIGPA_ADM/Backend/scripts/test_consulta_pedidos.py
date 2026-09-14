"""
Script temporal para probar interpret_message con la intención "consulta_pedidos"
contra distintos clientes.
No es parte del código final: solo para validar manualmente el comportamiento.

Uso: python -m scripts.test_consulta_pedidos
"""

import asyncio
import json

from app.services.agent_service import interpret_message

CASOS_PRUEBA = [
    {
        "descripcion": "Cliente existente con un pedido pendiente real",
        "phone": "56900000000",
        "message": "¿Tengo pedidos pendientes?",
    },
    {
        "descripcion": "Cliente existente sin pedidos activos nuevos (pero con 1 pendiente)",
        "phone": "56911111111",
        "message": "¿Cuáles son mis pedidos activos?",
    },
    {
        "descripcion": "Número que no existe como cliente",
        "phone": "56999999999",
        "message": "¿Tengo algún pedido?",
    },
]


async def main() -> None:
    for i, caso in enumerate(CASOS_PRUEBA, start=1):
        print("=" * 70)
        print(f"Caso {i}/{len(CASOS_PRUEBA)}: {caso['descripcion']}")
        print(f"Phone: {caso['phone']}")
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
