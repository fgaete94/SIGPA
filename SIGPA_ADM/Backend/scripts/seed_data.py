"""
Script de seed para datos iniciales de prueba.
Idempotente: se puede correr varias veces sin duplicar filas (verifica por nombre).

Uso: python -m scripts.seed_data
"""

import asyncio

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Producto

PRODUCTOS_SEED = [
    {"nombre": "Bidón 12L Nuevo", "precio_unitario": 6000, "stock": 0, "capacidad_litros": 12},
    {"nombre": "Bidón 12L Recarga", "precio_unitario": 2000, "stock": 0, "capacidad_litros": 12},
    {"nombre": "Bidón 20L Nuevo", "precio_unitario": 6000, "stock": 0, "capacidad_litros": 20},
    {"nombre": "Bidón 20L Recarga", "precio_unitario": 2500, "stock": 0, "capacidad_litros": 20},
]


async def seed_productos() -> None:
    insertados: list[str] = []
    existentes: list[str] = []

    async with SessionLocal() as session:
        try:
            for datos in PRODUCTOS_SEED:
                result = await session.execute(
                    select(Producto).where(Producto.nombre == datos["nombre"])
                )
                producto = result.scalar_one_or_none()

                if producto is not None:
                    existentes.append(datos["nombre"])
                    continue

                session.add(Producto(**datos))
                insertados.append(datos["nombre"])

            await session.commit()
        except Exception:
            await session.rollback()
            raise

    print("=== Resumen del seed de producto ===")
    if insertados:
        print(f"Insertados ({len(insertados)}):")
        for nombre in insertados:
            print(f"  - {nombre}")
    else:
        print("Insertados: ninguno")

    if existentes:
        print(f"Ya existían ({len(existentes)}):")
        for nombre in existentes:
            print(f"  - {nombre}")
    else:
        print("Ya existían: ninguno")


async def main() -> None:
    try:
        await seed_productos()
    except Exception as exc:
        print("[ERROR] Falló el seed de datos:")
        print(repr(exc))


if __name__ == "__main__":
    asyncio.run(main())
