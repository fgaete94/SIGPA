"""
Script temporal para validar la historia #93: GET /clientes/sugerencia-dia
sugiere el día de reparto de un cliente según su sector (el día más común
entre los clientes activos del sector que ya tienen día) o, si no tiene
sector, según su comuna (solo si ese día reúne al menos el 80%).
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: comunas y sectores con el prefijo "Prueba #93", y
clientes en el rango de teléfonos 56932303xxx. Al empezar y al terminar se
borran esos clientes, sectores y comunas. No toca ningún otro dato.
La autenticación JWT del panel se reemplaza con dependency_overrides.

Uso: python -m scripts.test_sugerencia_dia
"""

import asyncio

import httpx
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.main import app

RANGO_TELEFONOS_SQL = r"^(56)?932303\d{3}$"
COMUNA_PRUEBA = "Prueba #93"

_contador_telefono = 0


# --------------------------------------------------------------------------
# BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar() -> None:
    await _ejecutar(
        "delete from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango",
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(
        "delete from sector where comuna_id in (select id from comuna where nombre like :prefijo)",
        prefijo=f"{COMUNA_PRUEBA}%",
    )
    await _ejecutar("delete from comuna where nombre like :prefijo", prefijo=f"{COMUNA_PRUEBA}%")


async def _crear_comuna(nombre: str) -> int:
    result = await _ejecutar(
        "insert into comuna (nombre) values (:nombre) returning id", nombre=f"{COMUNA_PRUEBA} {nombre}"
    )
    return result.scalar_one()


async def _crear_sector(ctx: dict, nombre: str) -> int:
    result = await _ejecutar(
        "insert into sector (nombre, comuna_id) values (:nombre, :comuna_id) returning id",
        nombre=f"{COMUNA_PRUEBA} {nombre}",
        comuna_id=ctx["comuna_id"],
    )
    return result.scalar_one()


async def _crear_clientes(
    ctx: dict, sector_id: int | None, dias: list[str | None], activo: bool = True, comuna_id: int | None = None
) -> list[int]:
    global _contador_telefono
    ids = []
    for dia in dias:
        _contador_telefono += 1
        result = await _ejecutar(
            "insert into cliente (nombre, telefono, comuna_id, sector_id, dia_reparto, activo) "
            "values (:nombre, :telefono, :comuna_id, :sector_id, cast(:dia as dia_semana), :activo) returning id",
            nombre=f"Sugerencia {_contador_telefono:03d}",
            telefono=f"56932303{_contador_telefono:03d}",
            comuna_id=comuna_id or ctx["comuna_id"],
            sector_id=sector_id,
            dia=dia,
            activo=activo,
        )
        ids.append(result.scalar_one())
    return ids


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


async def _sugerencia(ctx: dict, **params) -> httpx.Response:
    respuesta = await ctx["http"].get("/clientes/sugerencia-dia", params=params)
    print(f"  GET /clientes/sugerencia-dia {params} -> {respuesta.status_code}: {respuesta.text}")
    return respuesta


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Mayoría")
    await _crear_clientes(ctx, sector, ["Viernes", "Viernes", "Viernes", "Lunes"])
    respuesta = await _sugerencia(ctx, sector_id=sector)
    cuerpo = respuesta.json()
    v.check(respuesta.status_code == 200, "200")
    v.check(cuerpo["dia_reparto"] == "Viernes", "sugiere el día más común (Viernes)")
    v.check(cuerpo["clientes_con_ese_dia"] == 3 and cuerpo["clientes_con_dia"] == 4, "informa 3 de 4 clientes")
    v.check(
        cuerpo["base"] == "sector" and cuerpo["base_id"] == sector and cuerpo["base_nombre"] == f"{COMUNA_PRUEBA} Mayoría",
        "la base es el sector, con su id y nombre",
    )


async def caso_b(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Empate")
    await _crear_clientes(ctx, sector, ["Jueves", "Jueves", "Viernes", "Viernes"])
    cuerpo = (await _sugerencia(ctx, sector_id=sector)).json()
    v.check(cuerpo["dia_reparto"] is None, "empate entre los días más comunes: sin sugerencia")
    v.check(cuerpo["clientes_con_ese_dia"] == 0 and cuerpo["clientes_con_dia"] == 4, "informa 4 clientes con día")


async def caso_c(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Sin días")
    await _crear_clientes(ctx, sector, [None, None])
    cuerpo = (await _sugerencia(ctx, sector_id=sector)).json()
    v.check(cuerpo["dia_reparto"] is None and cuerpo["clientes_con_dia"] == 0, "nadie con día: sin sugerencia")


async def caso_d(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Vacío")
    cuerpo = (await _sugerencia(ctx, sector_id=sector)).json()
    v.check(cuerpo["dia_reparto"] is None and cuerpo["clientes_con_dia"] == 0, "sector sin clientes: sin sugerencia")


async def caso_e(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Inactivos")
    await _crear_clientes(ctx, sector, ["Martes"])
    await _crear_clientes(ctx, sector, ["Lunes", "Lunes"], activo=False)
    cuerpo = (await _sugerencia(ctx, sector_id=sector)).json()
    v.check(cuerpo["dia_reparto"] == "Martes" and cuerpo["clientes_con_dia"] == 1, "los clientes inactivos no cuentan")


async def caso_f(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Excluir")
    ids = await _crear_clientes(ctx, sector, ["Miercoles", "Lunes"])
    cuerpo = (await _sugerencia(ctx, sector_id=sector)).json()
    v.check(cuerpo["dia_reparto"] is None, "sin excluir: empate 1 a 1, sin sugerencia")
    cuerpo = (await _sugerencia(ctx, sector_id=sector, excluir_cliente_id=ids[1])).json()
    v.check(
        cuerpo["dia_reparto"] == "Miercoles" and cuerpo["clientes_con_dia"] == 1,
        "excluyendo al cliente que se edita: su día no cuenta",
    )


async def caso_g(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Otro sector")
    otro = await _crear_sector(ctx, "Vecino")
    await _crear_clientes(ctx, otro, ["Jueves", "Jueves"])
    await _crear_clientes(ctx, sector, ["Lunes"])
    cuerpo = (await _sugerencia(ctx, sector_id=sector)).json()
    v.check(cuerpo["dia_reparto"] == "Lunes" and cuerpo["clientes_con_dia"] == 1, "solo cuentan los clientes del mismo sector")


async def caso_h(ctx: dict, v: Verificador) -> None:
    respuesta = await _sugerencia(ctx, sector_id=999999999)
    v.check(respuesta.status_code == 404, "sector inexistente: 404")
    respuesta = await _sugerencia(ctx, comuna_id=999999999)
    v.check(respuesta.status_code == 404, "comuna inexistente: 404")
    respuesta = await ctx["http"].get("/clientes/sugerencia-dia")
    v.check(respuesta.status_code == 422, "sin sector_id ni comuna_id: 422")


async def caso_i(ctx: dict, v: Verificador) -> None:
    sector = await _crear_sector(ctx, "Ficha")
    ids = await _crear_clientes(ctx, sector, ["Lunes"])
    respuesta = await ctx["http"].get(f"/clientes/{ids[0]}")
    v.check(respuesta.status_code == 200 and respuesta.json()["id"] == ids[0], "GET /clientes/{id} sigue funcionando")


async def _usuario_prueba():
    return {"user_id": "test", "email": "test-sugerencia-dia@sigpa.local"}


async def caso_j(ctx: dict, v: Verificador) -> None:
    comuna = await _crear_comuna("Dominante")
    sector = await _crear_sector(ctx, "De otra comuna")
    await _crear_clientes(ctx, None, ["Jueves"] * 8 + ["Viernes"], comuna_id=comuna)
    await _crear_clientes(ctx, sector, ["Lunes", "Lunes"])
    respuesta = await _sugerencia(ctx, comuna_id=comuna)
    cuerpo = respuesta.json()
    v.check(respuesta.status_code == 200 and cuerpo["base"] == "comuna", "sin sector: la base es la comuna")
    v.check(
        cuerpo["dia_reparto"] == "Jueves" and cuerpo["clientes_con_ese_dia"] == 8 and cuerpo["clientes_con_dia"] == 9,
        "día dominante de la comuna (8 de 9, 89%), sin contar clientes de otras comunas",
    )


async def caso_k(ctx: dict, v: Verificador) -> None:
    comuna = await _crear_comuna("Mezclada")
    sector_a = await _crear_sector_en(comuna, "A")
    sector_b = await _crear_sector_en(comuna, "B")
    await _crear_clientes(ctx, sector_a, ["Martes"] * 6, comuna_id=comuna)
    await _crear_clientes(ctx, sector_b, ["Viernes"] * 4, comuna_id=comuna)
    cuerpo = (await _sugerencia(ctx, comuna_id=comuna)).json()
    v.check(
        cuerpo["dia_reparto"] is None and cuerpo["clientes_con_ese_dia"] == 0 and cuerpo["clientes_con_dia"] == 10,
        "comuna con días mezclados (60%, bajo el 80%): sin sugerencia",
    )
    cuerpo = (await _sugerencia(ctx, sector_id=sector_b, comuna_id=comuna)).json()
    v.check(
        cuerpo["base"] == "sector" and cuerpo["dia_reparto"] == "Viernes",
        "con sector y comuna, manda el sector",
    )


async def caso_l(ctx: dict, v: Verificador) -> None:
    comuna = await _crear_comuna("Justo 80")
    await _crear_clientes(ctx, None, ["Lunes"] * 4 + ["Martes"], comuna_id=comuna)
    cuerpo = (await _sugerencia(ctx, comuna_id=comuna)).json()
    v.check(cuerpo["dia_reparto"] == "Lunes", "comuna con exactamente 80% (4 de 5): se sugiere")


async def _crear_sector_en(comuna_id: int, nombre: str) -> int:
    result = await _ejecutar(
        "insert into sector (nombre, comuna_id) values (:nombre, :comuna_id) returning id",
        nombre=f"{COMUNA_PRUEBA} {nombre}",
        comuna_id=comuna_id,
    )
    return result.scalar_one()


CASOS = [
    ("a", "Mayoría clara en el sector", caso_a),
    ("b", "Empate entre los días más comunes", caso_b),
    ("c", "Clientes del sector sin día", caso_c),
    ("d", "Sector sin clientes", caso_d),
    ("e", "Clientes inactivos no cuentan", caso_e),
    ("f", "Excluir al cliente que se está editando", caso_f),
    ("g", "Solo cuentan los clientes del mismo sector", caso_g),
    ("h", "Sector o comuna inexistente, o sin parámetros", caso_h),
    ("i", "La ruta nueva no tapa GET /clientes/{id}", caso_i),
    ("j", "Sin sector: día dominante de la comuna", caso_j),
    ("k", "Comuna con días mezclados, y el sector manda sobre la comuna", caso_k),
    ("l", "Comuna justo en el 80%", caso_l),
]


async def main() -> None:
    await _limpiar()
    ctx = {}
    result = await _ejecutar("insert into comuna (nombre) values (:nombre) returning id", nombre=COMUNA_PRUEBA)
    ctx["comuna_id"] = result.scalar_one()

    app.dependency_overrides[get_current_user] = _usuario_prueba
    resultados = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            ctx["http"] = http
            for letra, descripcion, caso in CASOS:
                print("#" * 70)
                print(f"Caso {letra}: {descripcion}")
                print("#" * 70)
                v = Verificador()
                try:
                    await caso(ctx, v)
                except Exception as exc:
                    print(f"    [ERROR] {exc!r}")
                    v.errores.append(repr(exc))
                resultados.append((letra, descripcion, not v.errores))
                print()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        await _limpiar()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())