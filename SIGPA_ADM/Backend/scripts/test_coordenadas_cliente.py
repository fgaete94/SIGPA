"""
Script temporal para validar la historia #159: las coordenadas de un pedido
se guardan también en su cliente (app/services/coordenadas_cliente.py).

Cubre:
- POST /pedidos/{id}/coordenadas (corrección manual desde el panel)
- POST /rutas/planificar (coordenadas que geocodifica n8n)
- PATCH /clientes/{id} (cambio de dirección del cliente)
- POST /pedidos (pedido creado desde el panel)

Corre contra la BD y con n8n SIMULADO (nunca se llama al webhook real:
ruta_service._cliente_http se reemplaza por un cliente con
httpx.MockTransport). La autenticación JWT del panel se reemplaza con
dependency_overrides.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: clientes en el rango de teléfonos 56932301xxx con sus
pedidos. Al empezar y al terminar se borran esos clientes, sus pedidos y
detalles, y las filas de auditoria que generó este script (usuario
USUARIO_PRUEBA). No toca ningún otro cliente ni pedido. Para los pedidos
creados con POST /pedidos se usa el primer producto existente, sin
modificarlo.

Uso: python -m scripts.test_coordenadas_cliente
"""

import asyncio
import json
from decimal import Decimal

import httpx
from sqlalchemy import text

import app.services.ruta_service as ruta_service
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.main import app

RANGO_TELEFONOS_SQL = r"^(56)?932301\d{3}$"
USUARIO_PRUEBA = "test-coordenadas-cliente@sigpa.local"
URL_N8N_PRUEBA = "http://n8n.test/webhook/sigpa-ruta"
SECRETO_N8N_PRUEBA = "secreto-de-prueba"

DIRECCION = "Notro 3689, Viña del Mar"
COORDS_CLIENTE = (Decimal("-33.010000"), Decimal("-71.540000"))
COORDS_CORREGIDAS = (-33.045800, -71.619700)
COORDS_N8N = (-33.050000, -71.600000)


# --------------------------------------------------------------------------
# BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar() -> None:
    ids_clientes = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    ids_pedidos = f"select id from pedido where cliente_id in ({ids_clientes})"
    await _ejecutar(
        f"delete from auditoria where usuario = :usuario and ("
        f"(entidad = 'pedido' and entidad_id in ({ids_pedidos})) or "
        f"(entidad = 'cliente' and entidad_id in ({ids_clientes})))",
        usuario=USUARIO_PRUEBA,
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from detalle_pedido where pedido_id in ({ids_pedidos})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)


_contador_telefono = 0


async def _crear_cliente(coords=(None, None), direccion=DIRECCION) -> int:
    global _contador_telefono
    _contador_telefono += 1
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        nombre=f"Coords Cliente {_contador_telefono}",
        telefono=f"56932301{_contador_telefono:03d}",
        direccion=direccion,
        latitud=coords[0],
        longitud=coords[1],
    )
    return result.scalar_one()


async def _crear_pedido(cliente_id: int, direccion_despacho=None, coords=(None, None), motivo=None) -> int:
    result = await _ejecutar(
        "insert into pedido (cliente_id, estado, direccion_despacho, total, latitud, longitud, motivo_revision_direccion) "
        "values (:cliente_id, 'confirmado', :direccion, 1000, :latitud, :longitud, :motivo) returning id",
        cliente_id=cliente_id,
        direccion=direccion_despacho,
        latitud=coords[0],
        longitud=coords[1],
        motivo=motivo,
    )
    return result.scalar_one()


async def _coords_cliente(cid: int) -> tuple:
    fila = (await _ejecutar("select latitud, longitud from cliente where id = :id", id=cid)).first()
    return (
        float(fila.latitud) if fila.latitud is not None else None,
        float(fila.longitud) if fila.longitud is not None else None,
    )


async def _coords_pedido(pid: int) -> tuple:
    fila = (await _ejecutar("select latitud, longitud from pedido where id = :id", id=pid)).first()
    return (
        float(fila.latitud) if fila.latitud is not None else None,
        float(fila.longitud) if fila.longitud is not None else None,
    )


async def _auditorias_cliente(cid: int, accion: str) -> int:
    result = await _ejecutar(
        "select count(*) from auditoria where entidad = 'cliente' and entidad_id = :id "
        "and accion = :accion and usuario = :usuario",
        id=cid,
        accion=accion,
        usuario=USUARIO_PRUEBA,
    )
    return result.scalar_one()


def _f(coords: tuple) -> tuple:
    return tuple(float(c) if c is not None else None for c in coords)


# --------------------------------------------------------------------------
# n8n simulado y verificador
# --------------------------------------------------------------------------


class N8nFalso:
    """Reemplaza el webhook de n8n. Resuelve todas las paradas con
    COORDS_N8N (las que no traen coordenadas), salvo las de sin_resolver."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.sin_resolver: set[int] = set()

    def cliente(self) -> httpx.AsyncClient:
        async def manejar(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.llamadas.append(payload)
            paradas = [p for p in payload["paradas"] if p["pedido_id"] not in self.sin_resolver]
            ruta = [
                {
                    "pedido_id": p["pedido_id"],
                    "orden_entrega": i,
                    "latitud": p["latitud"] if p["latitud"] is not None else COORDS_N8N[0],
                    "longitud": p["longitud"] if p["longitud"] is not None else COORDS_N8N[1],
                }
                for i, p in enumerate(paradas, start=1)
            ]
            sin_resolver = [
                {"pedido_id": p["pedido_id"], "motivo": "dirección no encontrada con precisión suficiente"}
                for p in payload["paradas"]
                if p["pedido_id"] in self.sin_resolver
            ]
            return httpx.Response(200, json={"ruta": ruta, "sin_resolver": sin_resolver})

        return httpx.AsyncClient(transport=httpx.MockTransport(manejar))


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


async def _corregir(ctx: dict, pid: int, coords=COORDS_CORREGIDAS) -> httpx.Response:
    respuesta = await ctx["http"].post(
        f"/pedidos/{pid}/coordenadas", json={"latitud": coords[0], "longitud": coords[1]}
    )
    print(f"  POST /pedidos/{pid}/coordenadas -> {respuesta.status_code}")
    return respuesta


async def _planificar(ctx: dict, ids: list[int]) -> httpx.Response:
    respuesta = await ctx["http"].post("/rutas/planificar", json={"pedido_ids": ids})
    print(f"  POST /rutas/planificar {ids} -> {respuesta.status_code}: {respuesta.text[:300]}")
    return respuesta


# --------------------------------------------------------------------------
# Casos: corrección manual (POST /pedidos/{id}/coordenadas)
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    pid = await _crear_pedido(cid, motivo="dirección no encontrada con precisión suficiente")
    respuesta = await _corregir(ctx, pid)
    v.check(respuesta.status_code == 200, "200")
    v.check(await _coords_cliente(cid) == COORDS_CORREGIDAS, "el cliente queda con las coordenadas corregidas")
    v.check(await _auditorias_cliente(cid, "corregir_coordenadas") == 1, "auditoría del cliente con el usuario del JWT")


async def caso_b(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    pid = await _crear_pedido(cid, direccion_despacho="notro  3689,  VIÑA DEL MAR", motivo="ambigua")
    await _corregir(ctx, pid)
    v.check(await _coords_cliente(cid) == COORDS_CORREGIDAS, "misma dirección con otro formato: se copia al cliente")


async def caso_c(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    pid = await _crear_pedido(cid, direccion_despacho="Oficina Calle Valparaíso 500, Viña del Mar", motivo="ambigua")
    respuesta = await _corregir(ctx, pid)
    v.check(respuesta.status_code == 200, "200")
    v.check(await _coords_pedido(pid) == COORDS_CORREGIDAS, "el pedido guarda la corrección")
    v.check(await _coords_cliente(cid) == (None, None), "otra dirección de despacho: el cliente no cambia")
    v.check(await _auditorias_cliente(cid, "corregir_coordenadas") == 0, "sin auditoría del cliente")


async def caso_d(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    pid = await _crear_pedido(cid, motivo="ambigua")
    await _corregir(ctx, pid)
    v.check(await _coords_cliente(cid) == COORDS_CORREGIDAS, "la corrección manual reemplaza las del cliente")


async def caso_e(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CORREGIDAS)
    pid = await _crear_pedido(cid, motivo="ambigua")
    await _corregir(ctx, pid)
    v.check(await _auditorias_cliente(cid, "corregir_coordenadas") == 0, "mismas coordenadas: sin auditoría del cliente")


# --------------------------------------------------------------------------
# Casos: planificación de ruta (n8n)
# --------------------------------------------------------------------------


async def caso_f(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    pid = await _crear_pedido(cid)
    respuesta = await _planificar(ctx, [pid])
    v.check(respuesta.status_code == 200, "200")
    v.check(await _coords_cliente(cid) == COORDS_N8N, "cliente sin coordenadas: recibe las geocodificadas por n8n")
    v.check(await _auditorias_cliente(cid, "planificar_ruta") == 1, "auditoría del cliente 'planificar_ruta'")


async def caso_g(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    pid = await _crear_pedido(cid)
    await _planificar(ctx, [pid])
    v.check(await _coords_pedido(pid) == COORDS_N8N, "el pedido guarda las de n8n")
    v.check(await _coords_cliente(cid) == _f(COORDS_CLIENTE), "cliente con coordenadas: n8n no las reemplaza")
    v.check(await _auditorias_cliente(cid, "planificar_ruta") == 0, "sin auditoría del cliente")


async def caso_h(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    pid = await _crear_pedido(cid)
    ctx["n8n"].sin_resolver = {pid}
    try:
        await _planificar(ctx, [pid])
    finally:
        ctx["n8n"].sin_resolver = set()
    v.check(await _coords_cliente(cid) == (None, None), "pedido sin resolver: el cliente no cambia")


async def caso_i(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    pid = await _crear_pedido(cid, direccion_despacho="Oficina Calle Valparaíso 500, Viña del Mar")
    await _planificar(ctx, [pid])
    v.check(await _coords_cliente(cid) == (None, None), "otra dirección de despacho: el cliente no cambia")


async def caso_j(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    p1 = await _crear_pedido(cid)
    p2 = await _crear_pedido(cid)
    respuesta = await _planificar(ctx, [p1, p2])
    v.check(respuesta.status_code == 200, "200")
    v.check(await _coords_cliente(cid) == COORDS_N8N, "dos pedidos del mismo cliente: el cliente recibe las coordenadas")
    v.check(await _auditorias_cliente(cid, "planificar_ruta") == 1, "una sola auditoría del cliente")


# --------------------------------------------------------------------------
# Casos: edición del cliente (PATCH /clientes/{id})
# --------------------------------------------------------------------------


async def caso_k(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    respuesta = await ctx["http"].patch(f"/clientes/{cid}", json={"direccion": "Los Pinos 456, Quilpué"})
    v.check(respuesta.status_code == 200, f"200 (recibido {respuesta.status_code})")
    v.check(await _coords_cliente(cid) == (None, None), "cambia la dirección: se borran las coordenadas")


async def caso_l(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    respuesta = await ctx["http"].patch(f"/clientes/{cid}", json={"direccion": f"  {DIRECCION} ", "nombre": "Otro nombre"})
    v.check(respuesta.status_code == 200, "200")
    v.check(await _coords_cliente(cid) == _f(COORDS_CLIENTE), "misma dirección (formulario del panel): se mantienen")


async def caso_m(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    respuesta = await ctx["http"].patch(
        f"/clientes/{cid}",
        json={"direccion": "Los Pinos 456, Quilpué", "latitud": COORDS_CORREGIDAS[0], "longitud": COORDS_CORREGIDAS[1]},
    )
    v.check(respuesta.status_code == 200, "200")
    v.check(await _coords_cliente(cid) == COORDS_CORREGIDAS, "dirección con coordenadas nuevas: se guardan esas")


# --------------------------------------------------------------------------
# Casos: pedido creado desde el panel (POST /pedidos)
# --------------------------------------------------------------------------


async def _crear_pedido_api(ctx: dict, cid: int, **extra) -> httpx.Response:
    cuerpo = {"cliente_id": cid, "lineas": [{"producto_id": ctx["producto_id"], "cantidad_solicitada": 1}], **extra}
    respuesta = await ctx["http"].post("/pedidos", json=cuerpo)
    print(f"  POST /pedidos -> {respuesta.status_code}")
    return respuesta


async def caso_n(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    respuesta = await _crear_pedido_api(ctx, cid)
    v.check(respuesta.status_code == 201, "201")
    v.check(await _coords_pedido(respuesta.json()["id"]) == _f(COORDS_CLIENTE), "sin dirección propia: hereda las del cliente")


async def caso_o(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    respuesta = await _crear_pedido_api(ctx, cid, direccion_despacho="Oficina Calle Valparaíso 500, Viña del Mar")
    v.check(respuesta.status_code == 201, "201")
    v.check(await _coords_pedido(respuesta.json()["id"]) == (None, None), "otra dirección: no hereda coordenadas")


async def caso_p(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente(coords=COORDS_CLIENTE)
    respuesta = await _crear_pedido_api(ctx, cid, latitud=COORDS_CORREGIDAS[0], longitud=COORDS_CORREGIDAS[1])
    v.check(respuesta.status_code == 201, "201")
    v.check(await _coords_pedido(respuesta.json()["id"]) == COORDS_CORREGIDAS, "con coordenadas propias: se respetan")


async def caso_q(ctx: dict, v: Verificador) -> None:
    cid = await _crear_cliente()
    respuesta = await _crear_pedido_api(ctx, cid)
    v.check(respuesta.status_code == 201, "201")
    v.check(await _coords_pedido(respuesta.json()["id"]) == (None, None), "cliente sin coordenadas: el pedido queda sin ellas")


# --------------------------------------------------------------------------
# Caso de punta a punta
# --------------------------------------------------------------------------


async def caso_r(ctx: dict, v: Verificador) -> None:
    """Criterio de la #159: después de corregir una vez, el siguiente pedido
    del cliente entra a la ruta sin volver a quedar por revisar."""
    cid = await _crear_cliente()
    p1 = await _crear_pedido(cid)
    ctx["n8n"].sin_resolver = {p1}
    try:
        await _planificar(ctx, [p1])
    finally:
        ctx["n8n"].sin_resolver = set()
    await _corregir(ctx, p1)
    await _ejecutar("update pedido set estado = 'entregado' where id = :id", id=p1)

    respuesta = await _crear_pedido_api(ctx, cid)
    p2 = respuesta.json()["id"]
    v.check(await _coords_pedido(p2) == COORDS_CORREGIDAS, "el pedido nuevo trae las coordenadas corregidas")

    # Si n8n no pudiera ubicar la dirección, el pedido igual entra porque ya trae coordenadas.
    ctx["n8n"].sin_resolver = set()
    respuesta = await _planificar(ctx, [p2])
    parada = next(p for p in ctx["n8n"].llamadas[-1]["paradas"] if p["pedido_id"] == p2)
    v.check(
        (parada["latitud"], parada["longitud"]) == COORDS_CORREGIDAS,
        "n8n recibe el pedido con coordenadas (no lo vuelve a geocodificar)",
    )
    cuerpo = respuesta.json()
    v.check([p["pedido_id"] for p in cuerpo["ruta"]] == [p2] and not cuerpo["sin_resolver"], "el pedido entra a la ruta")
    fila = (await _ejecutar("select motivo_revision_direccion from pedido where id = :id", id=p2)).first()
    v.check(fila.motivo_revision_direccion is None, "sin dirección por revisar")


async def _usuario_prueba():
    return {"user_id": "test", "email": USUARIO_PRUEBA}


CASOS = [
    ("a", "Corrección manual: el cliente recibe las coordenadas", caso_a),
    ("b", "Corrección manual: misma dirección con otro formato", caso_b),
    ("c", "Corrección manual: otra dirección de despacho no cambia al cliente", caso_c),
    ("d", "Corrección manual: reemplaza las coordenadas del cliente", caso_d),
    ("e", "Corrección manual: mismas coordenadas, sin auditoría del cliente", caso_e),
    ("f", "Ruta: n8n completa al cliente sin coordenadas", caso_f),
    ("g", "Ruta: n8n no reemplaza las coordenadas del cliente", caso_g),
    ("h", "Ruta: pedido sin resolver no cambia al cliente", caso_h),
    ("i", "Ruta: otra dirección de despacho no cambia al cliente", caso_i),
    ("j", "Ruta: dos pedidos del mismo cliente", caso_j),
    ("k", "Editar cliente: cambia la dirección, se borran las coordenadas", caso_k),
    ("l", "Editar cliente: misma dirección, se mantienen", caso_l),
    ("m", "Editar cliente: dirección con coordenadas nuevas", caso_m),
    ("n", "Nuevo pedido: hereda las coordenadas del cliente", caso_n),
    ("o", "Nuevo pedido: otra dirección no hereda", caso_o),
    ("p", "Nuevo pedido: coordenadas propias se respetan", caso_p),
    ("q", "Nuevo pedido: cliente sin coordenadas", caso_q),
    ("r", "Punta a punta: el siguiente pedido entra a la ruta sin corregir de nuevo", caso_r),
]


async def main() -> None:
    await _limpiar()
    ctx = {}
    producto = (await _ejecutar("select id from producto order by id limit 1")).first()
    if producto is None:
        print("No hay productos en la BD: no se pueden probar los casos de POST /pedidos")
        return
    ctx["producto_id"] = producto.id

    original = (settings.N8N_ROUTE_WEBHOOK_URL, settings.N8N_ROUTE_WEBHOOK_SECRET, ruta_service._cliente_http)
    ctx["n8n"] = N8nFalso()
    settings.N8N_ROUTE_WEBHOOK_URL = URL_N8N_PRUEBA
    settings.N8N_ROUTE_WEBHOOK_SECRET = SECRETO_N8N_PRUEBA
    ruta_service._cliente_http = ctx["n8n"].cliente
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
        settings.N8N_ROUTE_WEBHOOK_URL, settings.N8N_ROUTE_WEBHOOK_SECRET, ruta_service._cliente_http = original
        app.dependency_overrides.pop(get_current_user, None)
        await _limpiar()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())