"""
Script de seed para datos iniciales de prueba.
Idempotente: se puede correr varias veces sin duplicar filas (verifica por nombre).

Uso: python -m scripts.seed_data
"""

import asyncio

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Cliente, Producto

PRODUCTOS_SEED = [
    {"nombre": "Bidón 12L Nuevo", "precio_unitario": 6000, "stock": 0, "capacidad_litros": 12},
    {"nombre": "Bidón 12L Recarga", "precio_unitario": 2000, "stock": 0, "capacidad_litros": 12},
    {"nombre": "Bidón 20L Nuevo", "precio_unitario": 6000, "stock": 0, "capacidad_litros": 20},
    {"nombre": "Bidón 20L Recarga", "precio_unitario": 2500, "stock": 0, "capacidad_litros": 20},
    {"nombre": "Dispensador Básico", "precio_unitario": 7000, "stock": 0, "capacidad_litros": None},
    {"nombre": "Dispensador USB", "precio_unitario": 7000, "stock": 0, "capacidad_litros": None},
]

CLIENTE_SEED = {
    "nombre": "Cliente de Prueba",
    "telefono": "56900000000",
    "latitud": -33.0472,
    "longitud": -71.6127,
    "activo": True,
}


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


async def seed_cliente_prueba() -> None:
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(Cliente).where(Cliente.telefono == CLIENTE_SEED["telefono"])
            )
            cliente = result.scalar_one_or_none()

            if cliente is not None:
                creado = False
            else:
                session.add(Cliente(**CLIENTE_SEED))
                creado = True

            await session.commit()
        except Exception:
            await session.rollback()
            raise

    print("=== Resumen del seed de cliente de prueba ===")
    if creado:
        print(f"Insertado: {CLIENTE_SEED['nombre']} ({CLIENTE_SEED['telefono']})")
    else:
        print(f"Ya existía: {CLIENTE_SEED['nombre']} ({CLIENTE_SEED['telefono']})")


async def main() -> None:
    try:
        await seed_productos()
        await seed_cliente_prueba()
    except Exception as exc:
        print("[ERROR] Falló el seed de datos:")
        print(repr(exc))


if __name__ == "__main__":
    asyncio.run(main())
