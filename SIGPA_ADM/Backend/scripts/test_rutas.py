"""
Script temporal para validar la generación de la ruta de reparto (EP-04):
GET /rutas/pedidos-pendientes, POST /rutas/planificar y su relación con
POST /pedidos/{id}/coordenadas (ver
app/services/ruta_service.py), contra la BD y con n8n SIMULADO (nunca se
llama al webhook real: ruta_service._cliente_http se reemplaza por un cliente
con httpx.MockTransport). La autenticación JWT del panel se reemplaza con
dependency_overrides.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: clientes en el rango de teléfonos 56932300xxx con sus
pedidos. Al empezar y al terminar se borran esos clientes, sus pedidos y
detalles, y las filas de auditoria que generó este script (usuario
USUARIO_PRUEBA). No toca ningún otro cliente ni pedido.

Uso: python -m scripts.test_rutas
"""

import asyncio
import json
from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import text

import app.services.ruta_service as ruta_service
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.main import app

RANGO_TELEFONOS_SQL = r"^(56)?932300\d{3}$"
USUARIO_PRUEBA = "test-rutas@sigpa.local"
URL_N8N_PRUEBA = "http://n8n.test/webhook/sigpa-ruta"
SECRETO_N8N_PRUEBA = "secreto-de-prueba"

CLIENTE_CON_COORDS = {
    "nombre": "Ruta Con Coords",
    "telefono": "56932300001",
    "direccion": "Av. Libertad 100, Viña del Mar",
    "latitud": Decimal("-33.024500"),
    "longitud": Decimal("-71.551800"),
}
CLIENTE_SIN_COORDS = {
    "nombre": "Ruta Sin Coords",
    "telefono": "56932300002",
    "direccion": "Los Pinos 456, Quilpué",
    "latitud": None,
    "longitud": None,
}
CLIENTE_SIN_DIRECCION = {
    "nombre": "Ruta Sin Direccion",
    "telefono": "56932300003",
    "direccion": None,
    "latitud": None,
    "longitud": None,
}


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
        f"delete from auditoria where entidad = 'pedido' and usuario = :usuario and entidad_id in ({ids_pedidos})",
        usuario=USUARIO_PRUEBA,
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from detalle_pedido where pedido_id in ({ids_pedidos})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)


async def _crear_cliente(datos: dict) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        **datos,
    )
    return result.scalar_one()


async def _crear_pedido(cliente: dict, cliente_id: int, estado: str, orden: int | None = None, con_coords=True) -> int:
    result = await _ejecutar(
        "insert into pedido (cliente_id, estado, direccion_despacho, total, latitud, longitud, orden_entrega) "
        "values (:cliente_id, cast(:estado as estado_pedido), :direccion, 1000, :latitud, :longitud, :orden) returning id",
        cliente_id=cliente_id,
        estado=estado,
        direccion=cliente["direccion"],
        latitud=cliente["latitud"] if con_coords else None,
        longitud=cliente["longitud"] if con_coords else None,
        orden=orden,
    )
    return result.scalar_one()


async def _pedido(pid: int) -> dict | None:
    fila = (await _ejecutar("select * from pedido where id = :id", id=pid)).first()
    return dict(fila._mapping) if fila else None


async def _cliente(cid: int) -> dict:
    fila = (await _ejecutar("select direccion, latitud, longitud from cliente where id = :id", id=cid)).first()
    return dict(fila._mapping)


async def _reiniciar(ctx: dict) -> None:
    """Deja los pedidos planificables de prueba como recién creados."""
    for pid in (ctx["p1"], ctx["p3"]):
        await _ejecutar(
            "update pedido set estado = 'pendiente', orden_entrega = null, motivo_revision_direccion = null, "
            "latitud = :lat, longitud = :lon, direccion_despacho = :dir where id = :id",
            id=pid,
            lat=CLIENTE_CON_COORDS["latitud"],
            lon=CLIENTE_CON_COORDS["longitud"],
            dir=CLIENTE_CON_COORDS["direccion"],
        )
    await _ejecutar(
        "update pedido set estado = 'pendiente', orden_entrega = null, motivo_revision_direccion = null, "
        "latitud = null, longitud = null, direccion_despacho = :dir where id = :id",
        id=ctx["p2"],
        dir=CLIENTE_SIN_COORDS["direccion"],
    )


# --------------------------------------------------------------------------
# n8n simulado y cliente HTTP del panel
# --------------------------------------------------------------------------


class N8nFalso:
    """Reemplaza el webhook de n8n. `responder` recibe el payload enviado y
    devuelve un httpx.Response (o lanza una excepción de httpx)."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.responder = None

    def cliente(self) -> httpx.AsyncClient:
        async def manejar(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.llamadas.append({"headers": dict(request.headers), "payload": payload, "url": str(request.url)})
            resultado = self.responder(payload)
            if asyncio.iscoroutine(resultado):
                resultado = await resultado
            return resultado

        return httpx.AsyncClient(transport=httpx.MockTransport(manejar))


def _json(cuerpo: dict, codigo: int = 200) -> httpx.Response:
    return httpx.Response(codigo, json=cuerpo)


def _ruta_completa(payload: dict, sin_resolver: set[int] = frozenset()) -> httpx.Response:
    """Respuesta válida: todas las paradas en orden inverso al enviado (para
    verificar que se respeta el orden de n8n), salvo las de sin_resolver."""
    paradas = [p for p in payload["paradas"] if p["pedido_id"] not in sin_resolver]
    ruta = [
        {"pedido_id": p["pedido_id"], "orden_entrega": i, "latitud": -33.05 - i / 1000, "longitud": -71.6 - i / 1000}
        for i, p in enumerate(reversed(paradas), start=1)
    ]
    return _json(
        {
            "ruta": ruta,
            "sin_resolver": [{"pedido_id": pid, "motivo": "No se pudo geocodificar"} for pid in sin_resolver],
        }
    )


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


async def _planificar(ctx: dict, ids: list[int]) -> httpx.Response:
    respuesta = await ctx["http"].post("/rutas/planificar", json={"pedido_ids": ids})
    print(f"  POST /rutas/planificar {ids} -> {respuesta.status_code}: {respuesta.text[:400]}")
    return respuesta


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    n8n = ctx["n8n"]
    n8n.responder = _ruta_completa
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    respuesta = await _planificar(ctx, ids)
    v.check(respuesta.status_code == 200, "200")
    llamada = n8n.llamadas[-1]
    v.check(llamada["url"] == URL_N8N_PRUEBA, "llama al webhook configurado")
    v.check(llamada["headers"].get("x-route-secret") == SECRETO_N8N_PRUEBA, "con el header X-Route-Secret")
    paradas = {p["pedido_id"]: p for p in llamada["payload"]["paradas"]}
    v.check(
        set(paradas) == set(ids)
        and paradas[ctx["p2"]]["latitud"] is None
        and paradas[ctx["p1"]]["direccion_texto"] == CLIENTE_CON_COORDS["direccion"]
        and all(set(p) == {"pedido_id", "direccion_texto", "latitud", "longitud"} for p in paradas.values()),
        "payload: paradas desde la BD, sin depósito ni datos extra",
    )
    cuerpo = respuesta.json()
    v.check([p["orden_entrega"] for p in cuerpo["ruta"]] == [1, 2, 3], "respuesta ordenada por orden_entrega")
    v.check([p["pedido_id"] for p in cuerpo["ruta"]] == list(reversed(ids)), "respeta el orden que dio n8n")
    pedidos = {pid: await _pedido(pid) for pid in ids}
    v.check(all(p["estado"] == "confirmado" for p in pedidos.values()), "todos pasan a confirmado")
    v.check(
        [pedidos[pid]["orden_entrega"] for pid in reversed(ids)] == [1, 2, 3], "cada uno con su orden_entrega"
    )
    v.check(
        pedidos[ctx["p2"]]["latitud"] is not None and float(pedidos[ctx["p2"]]["latitud"]) < -33.0,
        "el pedido sin coordenadas guarda las geocodificadas por n8n",
    )
    v.check(
        (pedidos[ctx["p1"]]["latitud"], pedidos[ctx["p1"]]["longitud"])
        == (CLIENTE_CON_COORDS["latitud"], CLIENTE_CON_COORDS["longitud"]),
        "el pedido que ya tenía coordenadas no se sobrescribe",
    )
    auditorias = await _ejecutar(
        "select count(*) from auditoria where entidad = 'pedido' and accion = 'planificar_ruta' "
        "and usuario = :usuario and entidad_id = any(:ids)",
        usuario=USUARIO_PRUEBA,
        ids=ids,
    )
    v.check(auditorias.scalar_one() == 3, "auditoría 'planificar_ruta' con el usuario del JWT, una por pedido")


async def caso_b(ctx: dict, v: Verificador) -> None:
    n8n = ctx["n8n"]
    n8n.responder = lambda payload: _ruta_completa(payload, sin_resolver={ctx["p2"]})
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    cuerpo = respuesta.json()
    v.check(respuesta.status_code == 200, "200")
    v.check(
        [p["pedido_id"] for p in cuerpo["sin_resolver"]] == [ctx["p2"]]
        and cuerpo["sin_resolver"][0]["motivo"] == "No se pudo geocodificar",
        "sin_resolver al final, con su motivo",
    )
    p2 = await _pedido(ctx["p2"])
    v.check(p2["estado"] == "confirmado" and p2["orden_entrega"] is None, "el sin_resolver queda confirmado con orden null")
    v.check(p2["latitud"] is None, "y sin coordenadas inventadas")
    otros = [await _pedido(pid) for pid in (ctx["p1"], ctx["p3"])]
    v.check(
        all(p["estado"] == "confirmado" and p["orden_entrega"] in (1, 2) for p in otros),
        "los demás confirmados con su orden",
    )


async def caso_c(ctx: dict, v: Verificador) -> None:
    antes = await _pedido(ctx["fuera"])
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
    despues = await _pedido(ctx["fuera"])
    v.check(respuesta.status_code == 200, "200")
    v.check(antes == despues, "el pedido fuera de la solicitud queda exactamente igual (estado, orden, fechas)")
    v.check((await _pedido(ctx["p3"]))["estado"] == "pendiente", "el pendiente no incluido sigue pendiente")
    v.check(
        {"pedido_id": ctx["fuera"], "orden_entrega": 7} in respuesta.json()["confirmados_con_orden_fuera_de_solicitud"],
        "se informa el confirmado con orden viejo que quedó fuera",
    )


async def _sin_cambios(ctx: dict, v: Verificador) -> None:
    for pid in (ctx["p1"], ctx["p2"], ctx["p3"]):
        pedido = await _pedido(pid)
        v.check(
            pedido["estado"] == "pendiente" and pedido["orden_entrega"] is None,
            f"pedido {pid} sigue pendiente, sin orden",
        )
    v.check((await _pedido(ctx["p2"]))["latitud"] is None, "sin coordenadas escritas")


async def caso_d(ctx: dict, v: Verificador) -> None:
    def timeout(payload):
        raise httpx.ReadTimeout("simulado")

    ctx["n8n"].responder = timeout
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 504 and "no respondió a tiempo" in respuesta.json()["detail"]["mensaje"], "timeout: 504 con mensaje claro")
    await _sin_cambios(ctx, v)

    ctx["n8n"].responder = lambda payload: _json({"error": "boom"}, 500)
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 502, "error 500 de n8n: 502")
    ctx["n8n"].responder = lambda payload: httpx.Response(200, text="<html>no json</html>")
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 502, "respuesta que no es JSON: 502")
    await _sin_cambios(ctx, v)


async def caso_e(ctx: dict, v: Verificador) -> None:
    def ajeno(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["ruta"][0]["pedido_id"] = 999999999
        return _json(cuerpo)

    def orden_duplicado(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["ruta"][1]["orden_entrega"] = cuerpo["ruta"][0]["orden_entrega"]
        return _json(cuerpo)

    def en_ambos(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["sin_resolver"].append({"pedido_id": cuerpo["ruta"][0]["pedido_id"], "motivo": "x"})
        return _json(cuerpo)

    def latitud_invalida(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["ruta"][0]["latitud"] = 123.0
        return _json(cuerpo)

    for nombre, responder in (
        ("pedido_id ajeno", ajeno),
        ("orden_entrega duplicado", orden_duplicado),
        ("pedido en ruta y sin_resolver", en_ambos),
        ("latitud fuera de rango", latitud_invalida),
    ):
        ctx["n8n"].responder = responder
        respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
        v.check(respuesta.status_code == 502 and respuesta.json()["detail"].get("problemas"), f"{nombre}: 502 con el problema")
    await _sin_cambios(ctx, v)


async def caso_f(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    llamadas = len(ctx["n8n"].llamadas)
    ids = [ctx["p1"], ctx["cancelado"], ctx["entregado"], 999999999]
    respuesta = await _planificar(ctx, ids)
    detalle = respuesta.json()["detail"]
    v.check(respuesta.status_code == 409, "409")
    v.check(
        sorted(detalle["pedido_ids"]) == sorted([ctx["cancelado"], ctx["entregado"], 999999999]),
        "con los IDs problemáticos (cancelado, entregado e inexistente)",
    )
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)


async def caso_g(ctx: dict, v: Verificador) -> None:
    llamadas = len(ctx["n8n"].llamadas)
    vacia = await _planificar(ctx, [])
    repetidos = await _planificar(ctx, [ctx["p1"], ctx["p1"]])
    v.check(vacia.status_code == 422, "lista vacía: 422")
    v.check(repetidos.status_code == 422 and repetidos.json()["detail"]["pedido_ids"] == [ctx["p1"]], "IDs repetidos: 422 con el ID")
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")


async def caso_h(ctx: dict, v: Verificador) -> None:
    liberar = asyncio.Event()

    async def lento(payload):
        await liberar.wait()
        return _ruta_completa(payload)

    ctx["n8n"].responder = lento
    llamadas = len(ctx["n8n"].llamadas)
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    primera = asyncio.create_task(_planificar(ctx, ids))
    await asyncio.sleep(0.5)  # la primera ya está esperando a n8n
    segunda = await _planificar(ctx, ids)
    liberar.set()
    primera = await primera
    v.check(segunda.status_code == 409, "la segunda llamada simultánea recibe 409")
    v.check(primera.status_code == 200, "la primera termina bien")
    v.check(len(ctx["n8n"].llamadas) == llamadas + 1, "n8n se llama una sola vez")


async def caso_i(ctx: dict, v: Verificador) -> None:
    v.check(await _cliente(ctx["c1"]) == ctx["cliente_antes"][ctx["c1"]], "cliente con coordenadas: dirección y coordenadas intactas")
    v.check(await _cliente(ctx["c2"]) == ctx["cliente_antes"][ctx["c2"]], "cliente sin coordenadas: sigue sin coordenadas (no se le copian las del pedido)")


async def caso_j(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    ctx["n8n"].responder = lambda payload: _ruta_completa(
        {"paradas": list(reversed(payload["paradas"]))}
    )
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 200, "regenerar con pedidos ya confirmados: 200")
    v.check(
        [(await _pedido(pid))["orden_entrega"] for pid in (ctx["p1"], ctx["p2"], ctx["p3"])] == [1, 2, 3],
        "con el orden nuevo",
    )


async def caso_k(ctx: dict, v: Verificador) -> None:
    respuesta = await ctx["http"].get("/rutas/pedidos-pendientes")
    filas = respuesta.json()
    ids = {f["pedido_id"] for f in filas}
    no_planificables = await _ejecutar(
        "select count(*) from pedido where id = any(:ids) and not (estado = 'pendiente' or "
        "(estado = 'confirmado' and (orden_entrega is null or motivo_revision_direccion is not null)))",
        ids=list(ids),
    )
    v.check(respuesta.status_code == 200, "200")
    v.check(
        no_planificables.scalar_one() == 0,
        "solo pendientes y confirmados sin orden o con dirección por revisar",
    )
    fechas = [datetime.fromisoformat(f["creado_en"]) for f in filas]
    v.check(fechas == sorted(fechas), "ordenados por creado_en asc")
    v.check({ctx["p1"], ctx["p2"], ctx["p3"]} <= ids, "incluye los pendientes de prueba")
    v.check(not ids & {ctx["fuera"], ctx["cancelado"], ctx["entregado"]}, "excluye confirmados, cancelados y entregados")
    fila_p2 = next(f for f in filas if f["pedido_id"] == ctx["p2"])
    v.check(
        fila_p2["cliente_nombre"] == CLIENTE_SIN_COORDS["nombre"]
        and fila_p2["direccion_texto"] == CLIENTE_SIN_COORDS["direccion"]
        and fila_p2["latitud"] is None
        and fila_p2["creado_en"]
        and fila_p2["estado"] == "pendiente"
        and "motivo_revision_direccion" in fila_p2
        and fila_p2["motivo_revision_direccion"] is None,
        "con cliente, dirección, coordenadas (null si no tiene), fecha, estado y motivo (null)",
    )


async def caso_l(ctx: dict, v: Verificador) -> None:
    url = settings.N8N_ROUTE_WEBHOOK_URL
    settings.N8N_ROUTE_WEBHOOK_URL = ""
    try:
        respuesta = await _planificar(ctx, [ctx["p1"]])
    finally:
        settings.N8N_ROUTE_WEBHOOK_URL = url
    v.check(respuesta.status_code == 503 and "N8N_ROUTE_WEBHOOK_URL" in respuesta.json()["detail"]["mensaje"], "sin URL configurada: 503 claro (no 500)")
    await _sin_cambios(ctx, v)

    del app.dependency_overrides[get_current_user]
    try:
        sin_token = await ctx["http"].post("/rutas/planificar", json={"pedido_ids": [ctx["p1"]]})
        sin_token_get = await ctx["http"].get("/rutas/pedidos-pendientes")
    finally:
        app.dependency_overrides[get_current_user] = _usuario_prueba
    v.check(sin_token.status_code == 401 and sin_token_get.status_code == 401, "sin JWT: 401 en ambos endpoints")


async def _fila_get(ctx: dict, pid: int) -> dict | None:
    filas = (await ctx["http"].get("/rutas/pedidos-pendientes")).json()
    return next((f for f in filas if f["pedido_id"] == pid), None)


async def caso_m(ctx: dict, v: Verificador) -> None:
    # p2 queda sin_resolver en una ruta y luego se le corrigen las coordenadas.
    ctx["n8n"].responder = lambda payload: _ruta_completa(payload, sin_resolver={ctx["p2"]})
    await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    fila = await _fila_get(ctx, ctx["p2"])
    v.check(
        fila is not None and fila["estado"] == "confirmado" and fila["motivo_revision_direccion"] == "No se pudo geocodificar",
        "el sin_resolver aparece en el GET como confirmado y con su motivo",
    )
    correccion = await ctx["http"].post(
        f"/pedidos/{ctx['p2']}/coordenadas", json={"latitud": -33.0461, "longitud": -71.4012}
    )
    print(f"  POST /pedidos/{ctx['p2']}/coordenadas -> {correccion.status_code}")
    v.check(correccion.status_code == 200, "corrección de coordenadas: 200")
    fila = await _fila_get(ctx, ctx["p2"])
    v.check(
        fila is not None
        and fila["estado"] == "confirmado"
        and fila["motivo_revision_direccion"] is None
        and fila["latitud"] == -33.0461,
        "con coordenadas corregidas reaparece en el GET (orden null, sin motivo)",
    )
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p2"]])
    paradas = ctx["n8n"].llamadas[-1]["payload"]["paradas"]
    v.check(respuesta.status_code == 200, "se replanifica con éxito")
    v.check(
        paradas[0]["latitud"] == -33.0461 and paradas[0]["longitud"] == -71.4012,
        "se envía a n8n con las coordenadas corregidas",
    )
    p2 = await _pedido(ctx["p2"])
    v.check(p2["orden_entrega"] == 1 and float(p2["latitud"]) == -33.0461, "queda con orden y conserva las coordenadas corregidas")
    v.check(await _fila_get(ctx, ctx["p2"]) is None, "ya en la ruta, deja de aparecer en el GET")


async def caso_n(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    await _planificar(ctx, [ctx["p1"]])
    p1 = await _pedido(ctx["p1"])
    v.check(
        p1["estado"] == "confirmado" and p1["orden_entrega"] is not None and p1["motivo_revision_direccion"] is None,
        "p1 queda confirmado con orden y sin motivo",
    )
    v.check(await _fila_get(ctx, ctx["p1"]) is None, "confirmado con orden y sin motivo no aparece en el GET")
    v.check(await _fila_get(ctx, ctx["fuera"]) is None, "tampoco el confirmado con orden 7 de antes")


async def caso_o(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = lambda payload: _ruta_completa(payload, sin_resolver={ctx["p1"], ctx["p2"]})
    await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    p1, p2, p3 = [await _pedido(ctx[k]) for k in ("p1", "p2", "p3")]
    v.check(
        p1["motivo_revision_direccion"] == "No se pudo geocodificar"
        and p2["motivo_revision_direccion"] == "No se pudo geocodificar",
        "sin_resolver guarda el motivo en motivo_revision_direccion",
    )
    v.check(p3["motivo_revision_direccion"] is None, "el que entró a la ruta queda sin motivo")
    # p1 tiene coordenadas: al replanificar sin corregir nada, n8n lo ubica.
    fila = await _fila_get(ctx, ctx["p1"])
    v.check(fila is not None and fila["motivo_revision_direccion"] == "No se pudo geocodificar", "aparece en el GET con su motivo")
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"]])
    p1 = await _pedido(ctx["p1"])
    v.check(respuesta.status_code == 200, "replanificar: 200")
    v.check(
        p1["orden_entrega"] == 1 and p1["motivo_revision_direccion"] is None,
        "al entrar a la ruta se limpia motivo_revision_direccion",
    )


async def _auditorias_planificar(ctx: dict) -> int:
    result = await _ejecutar(
        "select count(*) from auditoria where entidad = 'pedido' and accion = 'planificar_ruta' "
        "and usuario = :usuario and entidad_id = any(:ids)",
        usuario=USUARIO_PRUEBA,
        ids=[ctx["p1"], ctx["p2"], ctx["p3"]],
    )
    return result.scalar_one()


def _mutar(transformar):
    """Responder que toma la ruta válida de _ruta_completa y la modifica."""

    def responder(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        transformar(cuerpo)
        return _json(cuerpo)

    return responder


async def caso_p(ctx: dict, v: Verificador) -> None:
    secreto = settings.N8N_ROUTE_WEBHOOK_SECRET
    ctx["n8n"].responder = _ruta_completa
    llamadas = len(ctx["n8n"].llamadas)
    settings.N8N_ROUTE_WEBHOOK_SECRET = ""
    try:
        respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
    finally:
        settings.N8N_ROUTE_WEBHOOK_SECRET = secreto
    v.check(
        respuesta.status_code == 503 and respuesta.json()["detail"] == {"mensaje": "Servicio de rutas no configurado"},
        "secret vacío: 503 'Servicio de rutas no configurado'",
    )
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)


async def caso_q(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    ctx["n8n"].responder = lambda payload: httpx.Response(403, text="Forbidden")
    respuesta = await _planificar(ctx, ids)
    detalle = respuesta.json()["detail"]
    v.check(
        respuesta.status_code == 502
        and detalle == {"mensaje": "Servicio de rutas mal configurado o rechazó la solicitud", "codigo": 403},
        "n8n 403 sin cuerpo: 502 con codigo 403",
    )
    ctx["n8n"].responder = lambda payload: _json({"error": "secreto_invalido"}, 401)
    respuesta = await _planificar(ctx, ids)
    v.check(
        respuesta.status_code == 502 and respuesta.json()["detail"]["codigo"] == "secreto_invalido",
        "n8n 401 con error: 502 con el codigo de n8n",
    )
    ctx["n8n"].responder = lambda payload: _json(
        {"error": "configuracion_incompleta", "detalle": "falta ORS_API_KEY en n8n"}, 500
    )
    respuesta = await _planificar(ctx, ids)
    detalle = respuesta.json()["detail"]
    v.check(
        respuesta.status_code == 502
        and detalle == {
            "mensaje": "Servicio de rutas mal configurado o rechazó la solicitud",
            "codigo": "configuracion_incompleta",
        },
        "n8n 500 {error: configuracion_incompleta}: 502 con ese codigo",
    )
    v.check("ORS_API_KEY" not in respuesta.text, "el 'detalle' de n8n no llega al panel")
    ctx["n8n"].responder = lambda payload: _json({"error": "datos_invalidos", "detalle": "x"}, 400)
    respuesta = await _planificar(ctx, ids)
    v.check(
        respuesta.status_code == 502 and respuesta.json()["detail"]["codigo"] == "datos_invalidos",
        "n8n 400 {error: datos_invalidos}: 502 con ese codigo",
    )
    await _sin_cambios(ctx, v)


async def caso_r(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]

    def timeout(payload):
        raise httpx.ReadTimeout("simulado")

    def red_caida(payload):
        raise httpx.ConnectError("simulado")

    respuestas = {}
    for nombre, responder in (
        ("timeout", timeout),
        ("red caída", red_caida),
        ("no-200 sin cuerpo útil", lambda payload: httpx.Response(500, text="Internal Server Error")),
        ("no-JSON", lambda payload: httpx.Response(200, text="<html>no json</html>")),
    ):
        ctx["n8n"].responder = responder
        respuesta = await _planificar(ctx, ids)
        respuestas[nombre] = (respuesta.status_code, respuesta.json()["detail"])
    v.check(respuestas["timeout"][0] == 504, "timeout: 504")
    v.check(
        all(respuestas[n][0] == 502 for n in ("red caída", "no-200 sin cuerpo útil", "no-JSON")),
        "red caída, no-200 sin cuerpo útil y no-JSON: 502",
    )
    v.check(
        "codigo" not in respuestas["no-200 sin cuerpo útil"][1],
        "no-200 sin cuerpo útil no se trata como 'mal configurado'",
    )
    mensajes = [d["mensaje"] for _, d in respuestas.values()]
    v.check(len(set(mensajes)) == 4 and all(mensajes), "con un mensaje claro y distinto para cada uno")
    await _sin_cambios(ctx, v)


async def caso_s(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    llamadas = len(ctx["n8n"].llamadas)
    maximo = settings.RUTA_MAX_PEDIDOS
    ids = list(range(900000000, 900000000 + maximo + 1))
    respuesta = await _planificar(ctx, ids)
    v.check(
        respuesta.status_code == 422
        and respuesta.json()["detail"]
        == {"mensaje": f"Máximo {maximo} pedidos por planificación", "maximo": maximo, "recibidos": maximo + 1},
        f"{maximo + 1} pedidos (default {maximo}): 422 con maximo y recibidos",
    )
    settings.RUTA_MAX_PEDIDOS = 2
    try:
        sobre = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
        v.check(
            sobre.status_code == 422 and sobre.json()["detail"]["maximo"] == 2,
            "el tope es configurable (RUTA_MAX_PEDIDOS=2, 3 pedidos: 422)",
        )
        v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
        await _sin_cambios(ctx, v)
        justo = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
        v.check(justo.status_code == 200, "justo en el tope: 200")
    finally:
        settings.RUTA_MAX_PEDIDOS = maximo


async def caso_t(ctx: dict, v: Verificador) -> None:
    # p1 solo con latitud, p3 solo con longitud.
    await _ejecutar("update pedido set longitud = null where id = :id", id=ctx["p1"])
    await _ejecutar("update pedido set latitud = null where id = :id", id=ctx["p3"])
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p3"]])
    v.check(respuesta.status_code == 200, "200")
    paradas = {p["pedido_id"]: p for p in ctx["n8n"].llamadas[-1]["payload"]["paradas"]}
    v.check(
        all(paradas[pid]["latitud"] is None and paradas[pid]["longitud"] is None for pid in (ctx["p1"], ctx["p3"])),
        "el par a medias se envía a n8n como sin coordenadas",
    )
    geocodificadas = {p["pedido_id"]: p for p in respuesta.json()["ruta"]}
    for pid in (ctx["p1"], ctx["p3"]):
        pedido = await _pedido(pid)
        v.check(
            pedido["latitud"] is not None
            and pedido["longitud"] is not None
            and float(pedido["latitud"]) == round(geocodificadas[pid]["latitud"], 6)
            and float(pedido["longitud"]) == round(geocodificadas[pid]["longitud"], 6),
            f"pedido {pid}: se sobrescribe el par completo con el geocodificado",
        )


async def caso_u(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    auditorias = await _auditorias_planificar(ctx)

    async def cambia_en_la_espera(payload):
        cambio = await ctx["http"].patch(f"/pedidos/{ctx['p1']}", json={"direccion_despacho": "Nueva 789, Viña"})
        print(f"  PATCH /pedidos/{ctx['p1']} durante la espera -> {cambio.status_code}")
        return _ruta_completa(payload)

    ctx["n8n"].responder = cambia_en_la_espera
    respuesta = await _planificar(ctx, ids)
    v.check(
        respuesta.status_code == 409
        and respuesta.json()["detail"]
        == {"mensaje": f"El pedido {ctx['p1']} cambió durante la planificación, reintenta"},
        "pedido cambiado durante la espera: 409 con su ID",
    )
    await _sin_cambios(ctx, v)
    v.check((await _pedido(ctx["p1"]))["direccion_despacho"] == "Nueva 789, Viña", "queda el cambio hecho durante la espera")
    v.check(await _auditorias_planificar(ctx) == auditorias, "sin auditorías de planificar_ruta")


async def caso_v(ctx: dict, v: Verificador) -> None:
    def huecos(cuerpo):
        cuerpo["ruta"][1]["orden_entrega"] = 3

    ctx["n8n"].responder = _mutar(huecos)
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
    problemas = respuesta.json()["detail"].get("problemas", [])
    v.check(
        respuesta.status_code == 502 and any("consecutivo" in p for p in problemas),
        "orden 1,3 (con hueco): 502 con el problema",
    )
    ctx["n8n"].responder = _mutar(lambda cuerpo: [p.update(orden_entrega=p["orden_entrega"] + 1) for p in cuerpo["ruta"]])
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(
        respuesta.status_code == 502 and any("consecutivo" in p for p in respuesta.json()["detail"]["problemas"]),
        "orden 2,3,4 (no parte en 1): 502",
    )
    await _sin_cambios(ctx, v)


async def caso_w(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]

    def omite(cuerpo):
        cuerpo["ruta"].pop()  # el último orden: el resto sigue siendo 1..N-1

    def repetido(cuerpo):
        copia = dict(cuerpo["ruta"][0], orden_entrega=len(cuerpo["ruta"]) + 1)
        cuerpo["ruta"].append(copia)

    def sin_motivo(cuerpo):
        sacado = cuerpo["ruta"].pop()
        cuerpo["sin_resolver"] = [{"pedido_id": sacado["pedido_id"], "motivo": "  "}]

    def motivo_ausente(cuerpo):
        sacado = cuerpo["ruta"].pop()
        cuerpo["sin_resolver"] = [{"pedido_id": sacado["pedido_id"]}]

    for nombre, transformar, texto in (
        ("ruta que omite un pedido", omite, "no vinieron en la respuesta"),
        ("mismo pedido repetido dentro de ruta", repetido, "repetidos"),
        ("sin_resolver con motivo vacío", sin_motivo, "sin motivo"),
        ("sin_resolver sin campo motivo", motivo_ausente, "sin motivo"),
    ):
        ctx["n8n"].responder = _mutar(transformar)
        respuesta = await _planificar(ctx, ids)
        problemas = respuesta.json()["detail"].get("problemas", []) if respuesta.status_code == 502 else []
        v.check(any(texto in p for p in problemas), f"{nombre}: 502 con el problema")
    await _sin_cambios(ctx, v)


async def caso_x(ctx: dict, v: Verificador) -> None:
    pid = ctx["sin_direccion"]

    def n8n_sin_datos(payload):
        # n8n no puede ubicar una parada sin dirección ni coordenadas.
        vacias = {
            p["pedido_id"]
            for p in payload["paradas"]
            if not p["direccion_texto"] and p["latitud"] is None and p["longitud"] is None
        }
        respuesta = json.loads(_ruta_completa(payload, sin_resolver=vacias).content)
        for item in respuesta["sin_resolver"]:
            item["motivo"] = "Sin dirección ni coordenadas"
        return _json(respuesta)

    await _ejecutar(
        "update pedido set estado = 'pendiente', orden_entrega = null, motivo_revision_direccion = null where id = :id",
        id=pid,
    )
    ctx["n8n"].responder = n8n_sin_datos
    respuesta = await _planificar(ctx, [ctx["p1"], pid])
    parada = next(p for p in ctx["n8n"].llamadas[-1]["payload"]["paradas"] if p["pedido_id"] == pid)
    v.check(
        parada == {"pedido_id": pid, "direccion_texto": None, "latitud": None, "longitud": None},
        "se envía a n8n sin dirección ni coordenadas",
    )
    cuerpo = respuesta.json()
    v.check(
        respuesta.status_code == 200 and [p["pedido_id"] for p in cuerpo["sin_resolver"]] == [pid],
        "200, con el pedido en sin_resolver",
    )
    pedido = await _pedido(pid)
    v.check(
        pedido["estado"] == "confirmado"
        and pedido["orden_entrega"] is None
        and pedido["latitud"] is None
        and pedido["motivo_revision_direccion"] == "Sin dirección ni coordenadas",
        "queda confirmado, sin orden, sin coordenadas y con el motivo",
    )
    v.check(
        [p["orden_entrega"] for p in cuerpo["ruta"]] == [1] and cuerpo["ruta"][0]["pedido_id"] == ctx["p1"],
        "el otro pedido entra a la ruta con orden 1",
    )


async def caso_y(ctx: dict, v: Verificador) -> None:
    filas = (await ctx["http"].get("/rutas/pedidos-pendientes")).json()
    fila = next(f for f in filas if f["pedido_id"] == ctx["p1"])
    texto = fila["creado_en"]
    fecha = datetime.fromisoformat(texto)
    en_bd = (await _pedido(ctx["p1"]))["creado_en"]
    v.check(texto.endswith("Z") or texto.endswith("+00:00"), f"creado_en con sufijo UTC ({texto})")
    v.check(
        fecha.tzinfo is not None and fecha.utcoffset().total_seconds() == 0, "ISO 8601 con zona horaria UTC"
    )
    v.check(fecha.replace(tzinfo=None) == en_bd, "misma hora que en la BD (que se guarda en UTC)")
    v.check(
        all(datetime.fromisoformat(f["creado_en"]).tzinfo is not None for f in filas),
        "todas las filas traen zona horaria",
    )


def _usuario_prueba() -> dict:
    return {"user_id": "test", "email": USUARIO_PRUEBA}


CASOS = [
    ("a", "Éxito con todos resueltos: confirmados con su orden", caso_a),
    ("b", "Éxito con algunos sin_resolver: confirmados, sin_resolver con orden null", caso_b),
    ("c", "Pedidos fuera de la solicitud no cambian (y se informan)", caso_c),
    ("d", "Timeout o error de n8n: nada se persiste", caso_d),
    ("e", "Respuesta inválida de n8n: nada se persiste", caso_e),
    ("f", "Cancelado, entregado o inexistente: 409 con los IDs, sin llamar a n8n", caso_f),
    ("g", "Lista vacía o IDs repetidos: error de validación", caso_g),
    ("h", "Segunda llamada simultánea: 409", caso_h),
    ("i", "Dirección y coordenadas del cliente nunca cambian", caso_i),
    ("j", "Regenerar la ruta con pedidos ya confirmados", caso_j),
    ("k", "GET /rutas/pedidos-pendientes devuelve solo planificables", caso_k),
    ("l", "Sin URL de n8n: 503; sin JWT: 401", caso_l),
    ("m", "Sin_resolver con coordenadas corregidas reaparece en el GET y se replanifica", caso_m),
    ("n", "Confirmado con orden y sin motivo no aparece en el GET", caso_n),
    ("o", "motivo_revision_direccion se guarda en sin_resolver y se limpia al entrar a la ruta", caso_o),
    ("p", "Secret de n8n vacío: 503 sin llamar a n8n", caso_p),
    ("q", "n8n 401/403 o 400/500 con error: 502 con codigo, sin el detalle", caso_q),
    ("r", "Timeout, red caída, no-200 sin cuerpo y no-JSON: mensajes distintos", caso_r),
    ("s", "Lista sobre RUTA_MAX_PEDIDOS: 422 antes de BD y n8n", caso_s),
    ("t", "Coordenadas parciales: se sobrescribe el par completo", caso_t),
    ("u", "Pedido cambiado durante la espera: 409 y nada escrito", caso_u),
    ("v", "orden_entrega con huecos: 502", caso_v),
    ("w", "Ruta que omite o repite un pedido, sin_resolver sin motivo: 502", caso_w),
    ("x", "Pedido sin dirección ni coordenadas: sin_resolver", caso_x),
    ("y", "creado_en en ISO 8601 con zona horaria UTC", caso_y),
]


async def main() -> None:
    await _limpiar()
    ctx = {}
    ctx["c1"] = await _crear_cliente(CLIENTE_CON_COORDS)
    ctx["c2"] = await _crear_cliente(CLIENTE_SIN_COORDS)
    ctx["c3"] = await _crear_cliente(CLIENTE_SIN_DIRECCION)
    ctx["p1"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "pendiente")
    ctx["p2"] = await _crear_pedido(CLIENTE_SIN_COORDS, ctx["c2"], "pendiente", con_coords=False)
    ctx["p3"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "pendiente")
    ctx["fuera"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "confirmado", orden=7)
    ctx["cancelado"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "cancelado")
    ctx["entregado"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "entregado")
    ctx["sin_direccion"] = await _crear_pedido(CLIENTE_SIN_DIRECCION, ctx["c3"], "confirmado", orden=8)
    ctx["cliente_antes"] = {cid: await _cliente(cid) for cid in (ctx["c1"], ctx["c2"])}
    print(f"Datos de prueba: {ctx}")

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
                await _reiniciar(ctx)
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
