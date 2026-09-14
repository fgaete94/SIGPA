"""
Script temporal para probar construir_resumen_pedido contra distintos casos.
No es parte del código final: solo para validar manualmente el comportamiento.

Uso: python -m scripts.test_resumen
"""

import asyncio
import json

from app.services.agent_service import construir_resumen_pedido

CASOS_OK = [
    {"nombre_producto": "Bidón 20L Recarga", "cantidad": 2},
    {"nombre_producto": "Bidón 12L Nuevo", "cantidad": 1},
]

CASOS_ERROR = [
    {"nombre_producto": "Bidón 5L", "cantidad": 1},
]


async def main() -> None:
    print("=" * 70)
    print("Caso 1: pedido válido")
    print(f"Productos: {CASOS_OK}")
    print("-" * 70)

    resultado = await construir_resumen_pedido(CASOS_OK)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    print()
    print(resultado["texto_resumen"])

    print()
    print("=" * 70)
    print("Caso 2: producto inventado (debe lanzar ValueError)")
    print(f"Productos: {CASOS_ERROR}")
    print("-" * 70)

    try:
        await construir_resumen_pedido(CASOS_ERROR)
        print("[ERROR] No se lanzó ValueError; se esperaba que fallara.")
    except ValueError as exc:
        print(f"[OK] Se lanzó ValueError como se esperaba: {exc}")

    print()
    print("=" * 70)
    print("Fin de las pruebas.")


if __name__ == "__main__":
    asyncio.run(main())
