"""
Script temporal para verificar la conexión a la base de datos (Supabase).
No forma parte del código final: se puede borrar una vez confirmada la conexión.

Uso: python scripts/test_db_connection.py
"""

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def test_connection() -> None:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("[OK] Conexión a la base de datos exitosa.")
    except Exception as exc:
        print("[ERROR] No se pudo conectar a la base de datos:")
        print(repr(exc))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_connection())
