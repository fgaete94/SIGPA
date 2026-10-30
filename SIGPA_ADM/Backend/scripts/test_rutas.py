"""
Script temporal para validar la generación de la ruta de reparto (EP-04):
GET /rutas/pedidos-pendientes, POST /rutas/planificar y su relación con
POST /pedidos/{id}/coordenadas (ver
app/services/ruta_service.py), contra la BD y con n8n SIMULADO (nunca se
llama al webhook real: ruta_service._cliente_http se reemplaza por un cliente
con httpx.MockTransport). La autenticación JWT del panel se reemplaza con
dependency_overrides.
No es parte del código final: solo para validar manualmente el comportamiento.

Contrato con viajes (restricción de peso): n8n devuelve la ruta repartida en
viajes y el backend valida y persiste pedido.viaje junto con orden_entrega.
Requiere aplicada la migración 20261026120000_agregar_viaje_pedido.sql (si la
columna pedido.viaje no existe, el script aborta). Para que los casos no
dependan de la configuración, el script fija RUTA_CAPACIDAD_KG = 10 * w,
RUTA_UMBRAL_CASA_KG = 6 * w y RUTA_MAX_VIAJES = 3, con w = peso_kg del
producto de prueba TEST_RUTAS_peso: un pedido de k unidades de ese producto
pesa k * w kg.

Productos de prueba: el script crea los suyos (prefijo TEST_RUTAS_: con peso,
con otro peso, sin peso, de peso 0 y de peso negativo), así que no depende de
los pesos que tengan los productos reales ni los modifica. Los borra al
empezar (por si una corrida anterior abortó) y al terminar, también si falla.

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
from sqlalchemy import event, text

import app.services.ruta_service as ruta_service
from app.core.config import settings
from app.core.database import SessionLocal, engine
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
    # Historia #159: planificar y corregir coordenadas también auditan al cliente.
    await _ejecutar(
        f"delete from auditoria where entidad = 'cliente' and usuario = :usuario and entidad_id in ({ids_clientes})",
        usuario=USUARIO_PRUEBA,
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from detalle_pedido where pedido_id in ({ids_pedidos})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)
    # Prefijo exacto (en LIKE el "_" sería un comodín). Ningún pedido real
    # apunta a estos productos; se sueltan los detalles por si quedó alguno.
    productos_prueba = "select id from producto where left(nombre, 11) = 'TEST_RUTAS_'"
    await _ejecutar(f"delete from detalle_pedido where producto_id in ({productos_prueba})")
    await _ejecutar(f"delete from producto where id in ({productos_prueba})")


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


async def _poner_items(pid: int, items: list[tuple[int, int]]) -> None:
    """Reemplaza los ítems del pedido por [(producto_id, cantidad), ...]."""
    await _ejecutar("delete from detalle_pedido where pedido_id = :pid", pid=pid)
    for producto_id, cantidad in items:
        await _ejecutar(
            "insert into detalle_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario) "
            "values (:pid, :producto, :cantidad, 1000)",
            pid=pid,
            producto=producto_id,
            cantidad=cantidad,
        )


async def _poner_unidades(ctx: dict, **unidades: int) -> None:
    """Deja cada pedido (clave p1, p2...) con esa cantidad de unidades del
    producto de referencia: pesa unidades * w kg."""
    for clave, cantidad in unidades.items():
        await _poner_items(ctx[clave], [(ctx["prod"], cantidad)])


async def _reiniciar(ctx: dict) -> None:
    """Deja los pedidos planificables de prueba como recién creados (1 unidad
    de peso cada uno, sin viaje)."""
    for clave in ("p1", "p2", "p3"):
        await _poner_items(ctx[clave], [(ctx["prod"], 1)])
    for pid in (ctx["p1"], ctx["p3"]):
        await _ejecutar(
            "update pedido set estado = 'pendiente', orden_entrega = null, viaje = null, motivo_revision_direccion = null, "
            "latitud = :lat, longitud = :lon, direccion_despacho = :dir where id = :id",
            id=pid,
            lat=CLIENTE_CON_COORDS["latitud"],
            lon=CLIENTE_CON_COORDS["longitud"],
            dir=CLIENTE_CON_COORDS["direccion"],
        )
    await _ejecutar(
        "update pedido set estado = 'pendiente', orden_entrega = null, viaje = null, motivo_revision_direccion = null, "
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


def _viajes_n8n(payload: dict, grupos: list[list[int]], sin_resolver: set[int] = frozenset()) -> httpx.Response:
    """Respuesta válida de n8n para un reparto dado: `grupos` es la lista de
    viajes (cada uno, los pedido_id en su orden de visita). Los pesos salen de
    los peso_kg del propio payload y termina_en se calcula aquí con la regla
    del negocio (último viaje, si hay al menos 2 y pesa menos que el umbral,
    termina en casa), sin reutilizar código del backend."""
    peso = {p["pedido_id"]: p["peso_kg"] for p in payload["paradas"]}
    ruta, viajes, i = [], [], 0
    for numero, grupo in enumerate(grupos, start=1):
        for orden, pid in enumerate(grupo, start=1):
            i += 1
            ruta.append(
                {"pedido_id": pid, "viaje": numero, "orden_entrega": orden,
                 "latitud": -33.05 - i / 1000, "longitud": -71.6 - i / 1000}
            )
        total = sum(peso[pid] for pid in grupo)
        casa = numero == len(grupos) and len(grupos) >= 2 and total < settings.RUTA_UMBRAL_CASA_KG
        viajes.append(
            {"viaje": numero, "peso_kg": total, "termina_en": "casa" if casa else "planta", "total_paradas": len(grupo)}
        )
    return _json(
        {
            "ruta": ruta,
            "viajes": viajes,
            "sin_resolver": [{"pedido_id": pid, "motivo": "No se pudo geocodificar"} for pid in sin_resolver],
        }
    )


def _ruta_completa(payload: dict, sin_resolver: set[int] = frozenset()) -> httpx.Response:
    """Respuesta válida con UN viaje: todas las paradas en orden inverso al
    enviado (para verificar que se respeta el orden de n8n), salvo las de
    sin_resolver."""
    paradas = [p["pedido_id"] for p in payload["paradas"] if p["pedido_id"] not in sin_resolver]
    return _viajes_n8n(payload, [list(reversed(paradas))] if paradas else [], sin_resolver)


def _en_viajes(ctx: dict, *claves_por_viaje: tuple[str, ...], transformar=None):
    """Responder de n8n con el reparto indicado con claves de ctx, p. ej.
    _en_viajes(ctx, ("p1",), ("p2", "p3")); `transformar` puede alterar el
    cuerpo (dict) para armar respuestas inválidas."""

    def responder(payload):
        grupos = [[ctx[k] for k in viaje] for viaje in claves_por_viaje]
        cuerpo = json.loads(_viajes_n8n(payload, grupos).content)
        if transformar:
            transformar(cuerpo)
        return _json(cuerpo)

    return responder


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
        and all(
            set(p) == {"pedido_id", "direccion_texto", "latitud", "longitud", "peso_kg"} for p in paradas.values()
        ),
        "payload: paradas desde la BD (con peso_kg), sin depósito ni datos extra",
    )
    cuerpo = respuesta.json()
    v.check(
        all(p["viaje"] == 1 for p in cuerpo["ruta"])
        and cuerpo["viajes"] == [{"viaje": 1, "peso_kg": 3 * ctx["w"], "termina_en": "planta", "total_paradas": 3}],
        "un solo viaje, que termina en la planta, con el peso recalculado",
    )
    v.check([p["orden_entrega"] for p in cuerpo["ruta"]] == [1, 2, 3], "respuesta ordenada por orden_entrega")
    v.check([p["pedido_id"] for p in cuerpo["ruta"]] == list(reversed(ids)), "respeta el orden que dio n8n")
    pedidos = {pid: await _pedido(pid) for pid in ids}
    v.check(all(p["estado"] == "confirmado" for p in pedidos.values()), "todos pasan a confirmado")
    v.check(
        [pedidos[pid]["orden_entrega"] for pid in reversed(ids)] == [1, 2, 3], "cada uno con su orden_entrega"
    )
    v.check(all(p["viaje"] == 1 for p in pedidos.values()), "y con viaje 1")
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
        {"pedido_id": ctx["fuera"], "viaje": None, "orden_entrega": 7}
        in respuesta.json()["confirmados_con_orden_fuera_de_solicitud"],
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
    # Historia #159: el cliente sin coordenadas recibe las de su pedido al
    # entrar a la ruta (el detalle se prueba en scripts/test_coordenadas_cliente.py).
    c2 = await _cliente(ctx["c2"])
    v.check(c2["direccion"] == ctx["cliente_antes"][ctx["c2"]]["direccion"], "cliente sin coordenadas: dirección intacta")
    v.check(c2["latitud"] is not None and c2["longitud"] is not None, "cliente sin coordenadas: recibe las de su pedido (#159)")

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
        parada == {"pedido_id": pid, "direccion_texto": None, "latitud": None, "longitud": None, "peso_kg": ctx["w"]},
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


async def _ruta_actual(ctx: dict) -> tuple[httpx.Response, dict]:
    respuesta = await ctx["http"].get("/rutas/actual")
    print(f"  GET /rutas/actual -> {respuesta.status_code}")
    return respuesta, respuesta.json()


def _paradas_de_prueba(ctx: dict, cuerpo: dict) -> list[dict]:
    """La BD es compartida: solo se miran las paradas de los pedidos de prueba."""
    propios = {ctx[k] for k in ("p1", "p2", "p3", "fuera", "cancelado", "entregado", "sin_direccion")}
    return [p for p in cuerpo["ruta"] if p["pedido_id"] in propios]


async def caso_z(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    ctx["n8n"].responder = _ruta_completa
    await _planificar(ctx, ids)
    respuesta, cuerpo = await _ruta_actual(ctx)
    v.check(respuesta.status_code == 200, "200")
    v.check(set(cuerpo) == {"ruta", "viajes", "total", "advertencias"}, "con ruta, viajes, total y advertencias")
    v.check(cuerpo["total"] == len(cuerpo["ruta"]), "total = cantidad de paradas")
    paradas = [p for p in _paradas_de_prueba(ctx, cuerpo) if p["pedido_id"] in ids]
    v.check([p["pedido_id"] for p in paradas] == list(reversed(ids)), "los 3 planificados en el orden que dio n8n")
    v.check([p["orden_entrega"] for p in paradas] == [1, 2, 3], "con orden_entrega 1, 2, 3")
    posiciones = [(p["viaje"] is None, p["viaje"] or 0, p["orden_entrega"]) for p in cuerpo["ruta"]]
    v.check(posiciones == sorted(posiciones), "toda la ruta ordenada por viaje y orden_entrega asc")
    p2 = next(p for p in paradas if p["pedido_id"] == ctx["p2"])
    v.check(
        set(p2)
        == {"pedido_id", "viaje", "orden_entrega", "cliente_id", "cliente_nombre", "cliente_telefono",
            "direccion_texto", "latitud", "longitud", "estado"}
        and p2["viaje"] == 1
        and p2["cliente_id"] == ctx["c2"]
        and p2["cliente_nombre"] == CLIENTE_SIN_COORDS["nombre"]
        and p2["cliente_telefono"] == CLIENTE_SIN_COORDS["telefono"]
        and p2["direccion_texto"] == CLIENTE_SIN_COORDS["direccion"]
        and p2["latitud"] is not None
        and p2["estado"] == "confirmado",
        "cada parada con cliente, teléfono, dirección, coordenadas geocodificadas y estado",
    )
    otra_vez = await ctx["http"].get("/rutas/actual")
    v.check(otra_vez.json() == cuerpo, "leerla de nuevo (recargar) da lo mismo")


async def caso_aa(ctx: dict, v: Verificador) -> None:
    await _ejecutar("update pedido set estado = 'confirmado', orden_entrega = null where id = :id", id=ctx["p1"])
    await _ejecutar("update pedido set estado = 'confirmado', orden_entrega = 1 where id = :id", id=ctx["p3"])
    _, cuerpo = await _ruta_actual(ctx)
    ids = {p["pedido_id"] for p in cuerpo["ruta"]}
    v.check(ctx["p1"] not in ids, "confirmado sin orden_entrega no aparece")
    v.check(ctx["p3"] in ids, "el confirmado con orden sí aparece")
    v.check(ctx["p2"] not in ids, "el pendiente no aparece")


async def caso_ab(ctx: dict, v: Verificador) -> None:
    await _ejecutar("update pedido set estado = 'cancelado', orden_entrega = 1 where id = :id", id=ctx["p1"])
    await _ejecutar("update pedido set estado = 'entregado', orden_entrega = 2 where id = :id", id=ctx["p2"])
    await _ejecutar("update pedido set estado = 'en_despacho', orden_entrega = 3 where id = :id", id=ctx["p3"])
    try:
        _, cuerpo = await _ruta_actual(ctx)
        ids = {p["pedido_id"] for p in cuerpo["ruta"]}
        v.check(ctx["p1"] not in ids, "cancelado con orden no aparece")
        v.check(ctx["p2"] not in ids, "entregado con orden no aparece")
        v.check(ctx["p3"] in ids, "en_despacho con orden SÍ aparece")
        v.check(
            all(p["estado"] in ("confirmado", "en_despacho") for p in cuerpo["ruta"]),
            "solo hay confirmados y en despacho",
        )
    finally:
        # Que _reiniciar no tenga que deshacer un entregado o cancelado.
        await _ejecutar(
            "update pedido set estado = 'pendiente', orden_entrega = null where id = any(:ids)",
            ids=[ctx["p1"], ctx["p2"], ctx["p3"]],
        )


async def caso_ac(ctx: dict, v: Verificador) -> None:
    # "fuera" es un confirmado con orden 7 de una ruta anterior: p1 con orden 7
    # simula una planificación nueva que no lo tocó.
    await _ejecutar(
        "update pedido set estado = 'confirmado', orden_entrega = 7, actualizado_en = now() where id = :id", id=ctx["p1"]
    )
    _, cuerpo = await _ruta_actual(ctx)
    advertencia = next(
        (a for a in cuerpo["advertencias"] if {ctx["p1"], ctx["fuera"]} <= set(a["pedido_ids"])), None
    )
    v.check(advertencia is not None, "orden_entrega repetido: advertencia con ambos pedido_id")
    v.check(
        advertencia is not None
        and advertencia["tipo"] == "orden_entrega_duplicado"
        and advertencia["orden_entrega"] == 7
        and advertencia["mensaje"],
        "con tipo, orden_entrega y mensaje",
    )
    paradas = [p["pedido_id"] for p in cuerpo["ruta"] if p["orden_entrega"] == 7]
    v.check(
        paradas.index(ctx["p1"]) < paradas.index(ctx["fuera"]),
        "a igual orden va primero el actualizado más recientemente",
    )
    posiciones = [(p["viaje"], p["orden_entrega"]) for p in cuerpo["ruta"]]
    repetidas = {x for x in posiciones if posiciones.count(x) > 1}
    v.check(
        {(a["viaje"], a["orden_entrega"]) for a in cuerpo["advertencias"]} == repetidas,
        "una advertencia por cada (viaje, orden_entrega) repetido",
    )


async def caso_ad(ctx: dict, v: Verificador) -> None:
    del app.dependency_overrides[get_current_user]
    try:
        sin_token = await ctx["http"].get("/rutas/actual")
        token_malo = await ctx["http"].get("/rutas/actual", headers={"Authorization": "Bearer no-es-un-jwt"})
    finally:
        app.dependency_overrides[get_current_user] = _usuario_prueba
    v.check(sin_token.status_code == 401 and sin_token.json() == {"detail": "No autenticado"}, "sin JWT: 401 'No autenticado'")
    v.check(
        token_malo.status_code == 401 and token_malo.json() == {"detail": "Token inválido o expirado"},
        "JWT inválido: 401 'Token inválido o expirado'",
    )


async def caso_ae(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = lambda payload: _ruta_completa(payload, sin_resolver={ctx["p2"]})
    await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    _, cuerpo = await _ruta_actual(ctx)
    ids = {p["pedido_id"] for p in cuerpo["ruta"]}
    v.check(ctx["p2"] not in ids, "el sin_resolver (confirmado, orden null) no aparece en la ruta")
    v.check({ctx["p1"], ctx["p3"]} <= ids, "los que sí entraron a la ruta aparecen")


async def _pedir_n8n_sin_escribir(ctx: dict, v: Verificador, responder, ids: list[int], texto: str) -> None:
    """502 con `texto` en los problemas y nada persistido."""
    ctx["n8n"].responder = responder
    respuesta = await _planificar(ctx, ids)
    problemas = respuesta.json()["detail"].get("problemas", []) if respuesta.status_code == 502 else []
    v.check(respuesta.status_code == 502 and any(texto in p for p in problemas), f"502 con el problema '{texto}'")
    for pid in ids:
        pedido = await _pedido(pid)
        v.check(
            pedido["estado"] == "pendiente" and pedido["orden_entrega"] is None and pedido["viaje"] is None,
            f"pedido {pid} sin cambios (sin viaje ni orden)",
        )


async def caso_af(ctx: dict, v: Verificador) -> None:
    # Redondeo: una sola vez, sobre la suma (no por línea) y con mínimo 1.
    v.check(ruta_service._redondear_kg(Decimal("10.2") + Decimal("10.2")) == 21, "10,2 + 10,2 = 20,4 -> 21 (no 11 + 11 = 22)")
    v.check(ruta_service._redondear_kg(Decimal("20.0")) == 20, "un total exacto no sube")
    v.check(ruta_service._redondear_kg(Decimal("0.3")) == 1, "mínimo 1 kg")
    # Cálculo contra la BD: dos productos, en UNA sola consulta.
    await _poner_items(ctx["p1"], [(ctx["prod"], 3), (ctx["prod_b"], 2)])
    consultas = []

    def contar(conn, cursor, statement, parameters, context, executemany):
        consultas.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", contar)
    try:
        async with SessionLocal() as session:
            pesos, sin_peso = await ruta_service.calcular_pesos(session, [ctx["p1"], ctx["p2"], ctx["p3"]])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", contar)
    v.check(len(consultas) == 1, f"una sola consulta para todos los pedidos ({len(consultas)})")
    v.check(
        pesos == {ctx["p1"]: 3 * ctx["w"] + 2 * ctx["w_b"], ctx["p2"]: ctx["w"], ctx["p3"]: ctx["w"]} and not sin_peso,
        "peso = SUM(cantidad * peso_kg) por pedido",
    )
    fila = await _fila_get(ctx, ctx["p1"])
    v.check(fila["peso_kg"] == 3 * ctx["w"] + 2 * ctx["w_b"] and isinstance(fila["peso_kg"], int), "GET pendientes lo muestra (int)")


async def caso_ag(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    llamadas = len(ctx["n8n"].llamadas)
    # p1: un producto sin peso; p2: sin ítems; p3: bien.
    await _poner_items(ctx["p1"], [(ctx["prod"], 1), (ctx["prod_sin_peso"], 1)])
    await _poner_items(ctx["p2"], [])
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    detalle = respuesta.json()["detail"]
    v.check(respuesta.status_code == 422, "422")
    v.check(sorted(detalle["sin_peso"]) == sorted([ctx["p1"], ctx["p2"]]), "con los pedidos sin peso calculable")
    v.check(
        detalle["productos_sin_peso"] == [{"pedido_id": ctx["p1"], "productos": [ctx["nombre_sin_peso"]]}],
        "y el producto sin peso del que sí tiene ítems",
    )
    v.check(detalle["mensaje"], "con mensaje")
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)


async def caso_ah(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    llamadas = len(ctx["n8n"].llamadas)
    await _poner_unidades(ctx, p1=11)  # 11 * w > capacidad (10 * w)
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
    detalle = respuesta.json()["detail"]
    v.check(respuesta.status_code == 422, "422")
    v.check(
        detalle["sobre_capacidad"] == [{"pedido_id": ctx["p1"], "peso_kg": 11 * ctx["w"]}]
        and detalle["capacidad_kg"] == settings.RUTA_CAPACIDAD_KG,
        "con el pedido que pesa más que la capacidad",
    )
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)
    await _poner_unidades(ctx, p1=10)  # justo en la capacidad: pasa
    justo = await _planificar(ctx, [ctx["p1"]])
    v.check(justo.status_code == 200, "un pedido que pesa justo la capacidad: 200")


async def caso_ai(ctx: dict, v: Verificador) -> None:
    await _poner_unidades(ctx, p1=10, p2=10, p3=10)  # 30 * w = capacidad * max_viajes
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p2",), ("p3",))
    justo = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(justo.status_code == 200, "justo en capacidad * max_viajes: 200 (3 viajes)")
    v.check([x["viaje"] for x in justo.json()["viajes"]] == [1, 2, 3], "con 3 viajes")
    await _reiniciar(ctx)
    await _poner_unidades(ctx, p1=10, p2=10, p3=10)
    llamadas = len(ctx["n8n"].llamadas)
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"], ctx["fuera"]])  # + 1 * w
    detalle = respuesta.json()["detail"]
    maximo = settings.RUTA_CAPACIDAD_KG * settings.RUTA_MAX_VIAJES
    v.check(respuesta.status_code == 422, "total sobre capacidad * max_viajes: 422")
    v.check(
        detalle["peso_total_kg"] == 31 * ctx["w"]
        and detalle["maximo_kg"] == maximo
        and str(31 * ctx["w"]) in detalle["mensaje"]
        and str(maximo) in detalle["mensaje"],
        "con el total y el máximo en el mensaje",
    )
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)


async def caso_aj(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    await _poner_unidades(ctx, p1=3, p2=2, p3=1)
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    payload = ctx["n8n"].llamadas[-1]["payload"]
    v.check(respuesta.status_code == 200, "200")
    v.check(set(payload) == {"paradas", "reglas"}, "el payload solo suma 'reglas'")
    v.check(
        payload["reglas"]
        == {
            "capacidad_kg": settings.RUTA_CAPACIDAD_KG,
            "umbral_casa_kg": settings.RUTA_UMBRAL_CASA_KG,
            "max_viajes": settings.RUTA_MAX_VIAJES,
        },
        "reglas: capacidad_kg, umbral_casa_kg y max_viajes",
    )
    pesos = {p["pedido_id"]: p["peso_kg"] for p in payload["paradas"]}
    v.check(
        pesos == {ctx["p1"]: 3 * ctx["w"], ctx["p2"]: 2 * ctx["w"], ctx["p3"]: ctx["w"]}
        and all(isinstance(x, int) and x >= 1 for x in pesos.values()),
        "cada parada con su peso_kg (int >= 1)",
    )
    v.check(
        ctx["n8n"].llamadas[-1]["headers"].get("x-route-secret") == SECRETO_N8N_PRUEBA,
        "la autenticación no cambia",
    )
    por_defecto = {k: type(settings).model_fields[k].default for k in ("RUTA_CAPACIDAD_KG", "RUTA_UMBRAL_CASA_KG", "RUTA_MAX_VIAJES")}
    v.check(por_defecto == {"RUTA_CAPACIDAD_KG": 1100, "RUTA_UMBRAL_CASA_KG": 800, "RUTA_MAX_VIAJES": 3}, "defaults 1100 / 800 / 3")


async def caso_ak(ctx: dict, v: Verificador) -> None:
    def viaje_3(cuerpo):  # viajes 1 y 3: falta el 2
        for x in [*cuerpo["ruta"], *cuerpo["viajes"]]:
            if x["viaje"] == 2:
                x["viaje"] = 3

    await _poner_unidades(ctx, p1=7, p2=2, p3=2)
    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1",), ("p2", "p3"), transformar=viaje_3), [ctx["p1"], ctx["p2"], ctx["p3"]], "consecutivos"
    )


async def caso_al(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=7, p2=2, p3=2)

    def hueco_en_viaje_2(cuerpo):
        [x for x in cuerpo["ruta"] if x["pedido_id"] == ctx["p3"]][0]["orden_entrega"] = 3

    def orden_global(cuerpo):  # 1, 2 en el viaje 1 y 3 en el viaje 2: era válido con la regla global anterior
        [x for x in cuerpo["ruta"] if x["pedido_id"] == ctx["p3"]][0]["orden_entrega"] = 2
        [x for x in cuerpo["ruta"] if x["pedido_id"] == ctx["p2"]][0]["orden_entrega"] = 3

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1",), ("p2", "p3"), transformar=hueco_en_viaje_2), ids, "viaje 2: orden_entrega no es consecutivo"
    )
    # viaje 1 = p1 (orden 1); viaje 2 = p2 (orden 3), p3 (orden 2): 1,2,3 en total, pero el viaje 2 no parte en 1.
    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1",), ("p2", "p3"), transformar=orden_global), ids, "viaje 2: orden_entrega no es consecutivo"
    )


async def caso_am(ctx: dict, v: Verificador) -> None:
    def declara_viaje_vacio(cuerpo):
        cuerpo["viajes"].append({"viaje": 2, "peso_kg": 0, "termina_en": "casa", "total_paradas": 0})

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1", "p2", "p3"), transformar=declara_viaje_vacio), [ctx["p1"], ctx["p2"], ctx["p3"]], "viaje 2 sin paradas"
    )


async def caso_an(ctx: dict, v: Verificador) -> None:
    await _poner_unidades(ctx, p1=7, p2=5, p3=1)  # p1 + p2 = 12 * w > capacidad; el total (13 * w) sí cabe en 2 viajes
    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1", "p2"), ("p3",)), [ctx["p1"], ctx["p2"], ctx["p3"]], "la capacidad es"
    )


async def caso_ao(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=3, p2=2, p3=2)

    def casa_en_el_primero(cuerpo):
        cuerpo["viajes"][0]["termina_en"] = "casa"

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1",), ("p2", "p3"), transformar=casa_en_el_primero), ids, "solo el último viaje puede terminar en casa"
    )
    # p1 (3 * w) en el viaje 1 y 4 * w en el 2: el último no es el más liviano.
    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1",), ("p2", "p3")), ids, "debe ser el más liviano"
    )


async def caso_ap(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=7, p2=3, p3=3)  # viaje 2 = 6 * w = umbral: NO es estrictamente menor

    def casa(cuerpo):
        cuerpo["viajes"][1]["termina_en"] = "casa"

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1",), ("p2", "p3"), transformar=casa), ids, "termina en casa con"
    )
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p2", "p3"))
    ok = await _planificar(ctx, ids)
    v.check(
        ok.status_code == 200 and ok.json()["viajes"][1]["termina_en"] == "planta",
        "con peso == umbral el último viaje termina en la planta (planta aceptada)",
    )
    await _reiniciar(ctx)
    await _poner_unidades(ctx, p1=7, p2=3, p3=2)  # 5 * w < umbral
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p2", "p3"), transformar=lambda c: c["viajes"][1].update(termina_en="planta"))
    await _pedir_n8n_sin_escribir(ctx, v, ctx["n8n"].responder, ids, "debe terminar en casa")


async def caso_aq(ctx: dict, v: Verificador) -> None:
    def casa(cuerpo):
        cuerpo["viajes"][0]["termina_en"] = "casa"

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1", "p2", "p3"), transformar=casa), [ctx["p1"], ctx["p2"], ctx["p3"]], "un único viaje siempre termina en planta"
    )
    # valor que no existe
    def raro(cuerpo):
        cuerpo["viajes"][0]["termina_en"] = "bodega"

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1", "p2", "p3"), transformar=raro), [ctx["p1"], ctx["p2"], ctx["p3"]], "termina_en inválido"
    )


async def caso_ar(ctx: dict, v: Verificador) -> None:
    def peso_distinto(cuerpo):
        cuerpo["viajes"][0]["peso_kg"] += 1

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1", "p2", "p3"), transformar=peso_distinto), [ctx["p1"], ctx["p2"], ctx["p3"]], "el backend calcula"
    )


async def caso_as(ctx: dict, v: Verificador) -> None:
    def sin_viajes(cuerpo):
        del cuerpo["viajes"]

    ctx["n8n"].responder = _en_viajes(ctx, ("p1", "p2", "p3"), transformar=sin_viajes)
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(
        respuesta.status_code == 502 and "falta la lista 'viajes'" in respuesta.json()["detail"]["problemas"],
        "sin 'viajes': 502 con el problema",
    )
    def sin_viaje_en_parada(cuerpo):
        del cuerpo["ruta"][0]["viaje"]

    await _pedir_n8n_sin_escribir(
        ctx, v, _en_viajes(ctx, ("p1", "p2", "p3"), transformar=sin_viaje_en_parada), [ctx["p1"], ctx["p2"], ctx["p3"]], "no existe en 'viajes'"
    )
    await _sin_cambios(ctx, v)


async def caso_at(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=7, p2=2, p3=2)  # viaje 1 = 7 * w; viaje 2 = 4 * w (< umbral 6 * w)
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p3", "p2"))
    respuesta = await _planificar(ctx, ids)
    cuerpo = respuesta.json()
    v.check(respuesta.status_code == 200, "200")
    v.check(
        [(x["pedido_id"], x["viaje"], x["orden_entrega"]) for x in cuerpo["ruta"]]
        == [(ctx["p1"], 1, 1), (ctx["p3"], 2, 1), (ctx["p2"], 2, 2)],
        "la ruta de la respuesta trae viaje y orden dentro del viaje",
    )
    v.check(
        cuerpo["viajes"]
        == [
            {"viaje": 1, "peso_kg": 7 * ctx["w"], "termina_en": "planta", "total_paradas": 1},
            {"viaje": 2, "peso_kg": 4 * ctx["w"], "termina_en": "casa", "total_paradas": 2},
        ],
        "viajes con el peso recalculado; el último, más liviano, termina en casa",
    )
    guardado = {pid: ((await _pedido(pid))["viaje"], (await _pedido(pid))["orden_entrega"]) for pid in ids}
    v.check(
        guardado == {ctx["p1"]: (1, 1), ctx["p3"]: (2, 1), ctx["p2"]: (2, 2)},
        "pedido.viaje y orden_entrega persistidos",
    )
    v.check(
        [(await _pedido(pid))["estado"] for pid in ids] == ["confirmado"] * 3,
        "todos confirmados",
    )
    despues = await _ejecutar(
        "select (despues->>'viaje')::int from auditoria where entidad = 'pedido' and accion = 'planificar_ruta' "
        "and usuario = :usuario and entidad_id = :id order by id desc limit 1",
        usuario=USUARIO_PRUEBA,
        id=ctx["p3"],
    )
    v.check(despues.scalar_one() == 2, "la auditoría guarda el viaje")
    # La vista previa también trae viaje y viajes, sin escribir.
    await _reiniciar(ctx)
    await _poner_unidades(ctx, p1=7, p2=2, p3=2)
    previa = await ctx["http"].post("/rutas/planificar", json={"pedido_ids": ids, "vista_previa": True})
    v.check(
        previa.status_code == 200
        and [x["viaje"] for x in previa.json()["ruta"]] == [1, 2, 2]
        and len(previa.json()["viajes"]) == 2,
        "vista previa: ruta con viaje y 'viajes'",
    )
    await _sin_cambios(ctx, v)


async def caso_au(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=7, p2=2, p3=2)
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p2", "p3"))
    await _planificar(ctx, ids)
    v.check((await _pedido(ctx["p2"]))["viaje"] == 2, "p2 queda en el viaje 2")
    # En la siguiente planificación p2 queda sin_resolver: viaje y orden vuelven a NULL.
    ctx["n8n"].responder = lambda payload: _viajes_n8n(payload, [[ctx["p1"], ctx["p3"]]], {ctx["p2"]})
    respuesta = await _planificar(ctx, ids)
    p2 = await _pedido(ctx["p2"])
    v.check(respuesta.status_code == 200, "200")
    v.check(
        p2["viaje"] is None and p2["orden_entrega"] is None and p2["motivo_revision_direccion"] is not None,
        "sin_resolver: viaje y orden_entrega NULL (con su motivo)",
    )
    v.check([x["pedido_id"] for x in respuesta.json()["sin_resolver"]] == [ctx["p2"]], "y sigue en sin_resolver en la respuesta")
    v.check(
        [x["viaje"] for x in respuesta.json()["viajes"]] == [1] and (await _pedido(ctx["p3"]))["viaje"] == 1,
        "los demás en el viaje 1",
    )


async def caso_av(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=6, p2=2, p3=2)  # 10 * w: también cabe en un solo viaje
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p2", "p3"))
    await _planificar(ctx, ids)
    antes = {pid: ((await _pedido(pid))["viaje"], (await _pedido(pid))["orden_entrega"]) for pid in ids}
    ctx["n8n"].responder = _en_viajes(ctx, ("p3", "p1", "p2"))  # todo en un viaje, otro orden
    respuesta = await _planificar(ctx, ids)
    despues = {pid: ((await _pedido(pid))["viaje"], (await _pedido(pid))["orden_entrega"]) for pid in ids}
    v.check(respuesta.status_code == 200, "replanificar: 200")
    v.check(antes != despues, "cambió el reparto")
    v.check(
        despues == {ctx["p3"]: (1, 1), ctx["p1"]: (1, 2), ctx["p2"]: (1, 3)},
        "viaje y orden se sobrescriben con la nueva planificación",
    )


async def caso_aw(ctx: dict, v: Verificador) -> None:
    await _poner_items(ctx["p1"], [(ctx["prod"], 3), (ctx["prod_b"], 2)])
    await _poner_items(ctx["p2"], [(ctx["prod"], 1), (ctx["prod_sin_peso"], 1)])
    await _poner_items(ctx["p3"], [])
    respuesta = await ctx["http"].get("/rutas/pedidos-pendientes")
    filas = {f["pedido_id"]: f for f in respuesta.json()}
    v.check(respuesta.status_code == 200, "200")
    v.check(
        filas[ctx["p1"]]["peso_kg"] == 3 * ctx["w"] + 2 * ctx["w_b"] and isinstance(filas[ctx["p1"]]["peso_kg"], int),
        "peso_kg entero = SUM(cantidad * peso_kg)",
    )
    v.check(filas[ctx["p2"]]["peso_kg"] is None, "con un producto sin peso: null")
    v.check(filas[ctx["p3"]]["peso_kg"] is None, "sin ítems: null")
    v.check(
        all("peso_kg" in f for f in filas.values()) and {"pedido_id", "cliente_nombre", "direccion_texto", "estado"} <= set(filas[ctx["p1"]]),
        "todos los items traen peso_kg y no se quitó ningún campo",
    )


async def caso_ax(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=7, p2=2, p3=2)
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p2", "p3"))
    await _planificar(ctx, ids)
    await _ejecutar("update pedido set estado = 'en_despacho' where id = :id", id=ctx["p3"])
    _, cuerpo = await _ruta_actual(ctx)
    propias = [(p["pedido_id"], p["viaje"], p["orden_entrega"], p["estado"]) for p in _paradas_de_prueba(ctx, cuerpo) if p["pedido_id"] in ids]
    v.check(
        propias == [
            (ctx["p1"], 1, 1, "confirmado"),
            (ctx["p2"], 2, 1, "confirmado"),
            (ctx["p3"], 2, 2, "en_despacho"),
        ],
        "ordenada por viaje y orden; el en_despacho aparece con su viaje",
    )
    ajenos = [p for p in cuerpo["ruta"] if p["pedido_id"] not in ctx["propios"] and p["viaje"] is not None]
    if ajenos:
        print(f"    [aviso] hay {len(ajenos)} pedidos ajenos con viaje en la BD: se omite la comparación exacta de 'viajes'")
    else:
        v.check(
            cuerpo["viajes"]
            == [
                {"viaje": 1, "peso_kg": 7 * ctx["w"], "termina_en": "planta", "total_paradas": 1},
                {"viaje": 2, "peso_kg": 4 * ctx["w"], "termina_en": "casa", "total_paradas": 2},
            ],
            "viajes: peso, termina_en derivado (último y < umbral: casa) y total_paradas",
        )
    # Advertencias por (viaje, orden): p3 en el viaje 1 orden 1 choca con p1; el viaje 2 orden 1 de p2 no choca.
    await _ejecutar("update pedido set viaje = 1, orden_entrega = 1, actualizado_en = now() where id = :id", id=ctx["p3"])
    _, cuerpo = await _ruta_actual(ctx)
    choques = [a for a in cuerpo["advertencias"] if {ctx["p1"], ctx["p3"]} <= set(a["pedido_ids"])]
    v.check(
        len(choques) == 1 and choques[0]["viaje"] == 1 and choques[0]["orden_entrega"] == 1 and "viaje 1" in choques[0]["mensaje"],
        "advertencia por (viaje, orden_entrega) repetido, con el viaje",
    )
    # Solo cuentan los choques con los pedidos de prueba de otro viaje: un
    # pedido real que ya esté en (viaje 2, orden 1) sí es un duplicado legítimo.
    v.check(
        not any(
            ctx["p2"] in a["pedido_ids"] and {ctx["p1"], ctx["p3"]} & set(a["pedido_ids"])
            for a in cuerpo["advertencias"]
        ),
        "mismo orden en otro viaje (p2: viaje 2, orden 1) no es duplicado",
    )
    await _ejecutar("update pedido set estado = 'pendiente', orden_entrega = null, viaje = null where id = any(:ids)", ids=ids)


async def caso_ay(ctx: dict, v: Verificador) -> None:
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    await _poner_unidades(ctx, p1=7, p2=2, p3=2)
    ctx["n8n"].responder = _en_viajes(ctx, ("p1",), ("p2", "p3"))
    previa = (await ctx["http"].post("/rutas/planificar", json={"pedido_ids": ids, "vista_previa": True})).json()
    por_id = {x["pedido_id"]: x for x in previa["ruta"]}

    def parada(clave, viaje=...):
        x = por_id[ctx[clave]]
        base = {"pedido_id": x["pedido_id"], "latitud": x["latitud"], "longitud": x["longitud"], "version": x["version"]}
        if viaje is not ...:
            base["viaje"] = viaje
        return base

    # viajes del panel: p3 antes que p2 en el viaje 2
    respuesta = await ctx["http"].post(
        "/rutas/confirmar", json={"paradas": [parada("p1", 1), parada("p3", 2), parada("p2", 2)], "sin_resolver": []}
    )
    print(f"  POST /rutas/confirmar -> {respuesta.status_code}: {respuesta.text[:300]}")
    v.check(respuesta.status_code == 200, "confirmar con viajes: 200")
    guardado = {pid: ((await _pedido(pid))["viaje"], (await _pedido(pid))["orden_entrega"]) for pid in ids}
    v.check(
        guardado == {ctx["p1"]: (1, 1), ctx["p3"]: (2, 1), ctx["p2"]: (2, 2)},
        "guarda viaje y orden dentro del viaje en el orden recibido",
    )
    v.check([x["viaje"] for x in respuesta.json()["viajes"]] == [1, 2], "responde 'viajes'")
    # todo en un viaje y sobre la capacidad: 422 sin escribir
    await _reiniciar(ctx)
    await _poner_unidades(ctx, p1=7, p2=2, p3=2)
    previa = (await ctx["http"].post("/rutas/planificar", json={"pedido_ids": ids, "vista_previa": True})).json()
    por_id = {x["pedido_id"]: x for x in previa["ruta"]}
    sobre = await ctx["http"].post(
        "/rutas/confirmar", json={"paradas": [parada("p1"), parada("p2"), parada("p3")], "sin_resolver": []}
    )
    v.check(
        sobre.status_code == 422 and any("capacidad" in p for p in sobre.json()["detail"]["problemas"]),
        "sin viaje se asume el 1: 11 * w sobre la capacidad -> 422",
    )
    await _sin_cambios(ctx, v)


async def caso_az(ctx: dict, v: Verificador) -> None:
    # Solo dispensadores (peso 0): se planifica y el pedido pesa 1 kg (mínimo sobre el total).
    await _poner_items(ctx["p1"], [(ctx["prod_cero"], 2)])
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"]])
    v.check(respuesta.status_code == 200, "pedido solo con productos de peso 0: 200 (no 422)")
    v.check(
        ctx["n8n"].llamadas[-1]["payload"]["paradas"][0]["peso_kg"] == 1
        and respuesta.json()["viajes"][0]["peso_kg"] == 1,
        "pesa 1 kg (mínimo sobre el total) en el payload y en viajes",
    )
    await _reiniciar(ctx)
    await _poner_items(ctx["p1"], [(ctx["prod_cero"], 2)])
    v.check((await _fila_get(ctx, ctx["p1"]))["peso_kg"] == 1, "GET pendientes: peso_kg 1")


async def caso_ba(ctx: dict, v: Verificador) -> None:
    # Dispensador (0 kg) + bidón: pesa solo el bidón.
    await _poner_items(ctx["p1"], [(ctx["prod_cero"], 1), (ctx["prod"], 1)])
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"]])
    v.check(respuesta.status_code == 200, "200")
    v.check(
        ctx["n8n"].llamadas[-1]["payload"]["paradas"][0]["peso_kg"] == ctx["w"],
        f"dispensador + bidón pesa lo del bidón ({ctx['w']} kg)",
    )
    # El mínimo de 1 kg es sobre el total: no se suma por línea de peso 0.
    async with SessionLocal() as session:
        await _poner_items(ctx["p2"], [(ctx["prod_cero"], 3), (ctx["prod_cero"], 4)])
        pesos, sin_peso = await ruta_service.calcular_pesos(session, [ctx["p2"]])
    v.check(pesos == {ctx["p2"]: 1} and not sin_peso, "solo peso 0: total 1 kg")


async def caso_bb(ctx: dict, v: Verificador) -> None:
    # Peso 0 junto a un producto con peso NULL: 422 y solo se nombra el NULL.
    await _poner_items(ctx["p1"], [(ctx["prod_cero"], 1), (ctx["prod_sin_peso"], 1)])
    llamadas = len(ctx["n8n"].llamadas)
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
    detalle = respuesta.json()["detail"]
    v.check(respuesta.status_code == 422 and detalle["sin_peso"] == [ctx["p1"]], "422 con el pedido de peso NULL en sin_peso")
    v.check(
        detalle["productos_sin_peso"] == [{"pedido_id": ctx["p1"], "productos": [ctx["nombre_sin_peso"]]}],
        "productos_sin_peso nombra solo el de peso NULL, no el de peso 0",
    )
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)


async def caso_bc(ctx: dict, v: Verificador) -> None:
    await _poner_items(ctx["p1"], [(ctx["prod"], 1), (ctx["prod_negativo"], 1)])
    llamadas = len(ctx["n8n"].llamadas)
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
    detalle = respuesta.json()["detail"]
    v.check(respuesta.status_code == 422 and detalle["sin_peso"] == [ctx["p1"]], "peso_kg negativo: 422 con sin_peso")
    v.check(
        detalle["productos_sin_peso"] == [{"pedido_id": ctx["p1"], "productos": ["TEST_RUTAS_negativo"]}],
        "con el producto de peso negativo en productos_sin_peso",
    )
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)


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
    ("i", "Coordenadas del cliente: solo se completan si no tenía (#159)", caso_i),
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
    ("z", "GET /rutas/actual tras planificar 3 pedidos los devuelve en orden", caso_z),
    ("aa", "GET /rutas/actual: confirmado sin orden_entrega no aparece", caso_aa),
    ("ab", "GET /rutas/actual: cancelado y entregado con orden no aparecen; en_despacho sí", caso_ab),
    ("ac", "GET /rutas/actual: (viaje, orden_entrega) duplicado genera advertencia", caso_ac),
    ("ad", "GET /rutas/actual sin JWT: 401", caso_ad),
    ("ae", "GET /rutas/actual: los sin_resolver no aparecen", caso_ae),
    ("af", "Peso: SUM(cantidad * peso_kg) en una consulta y redondeo único hacia arriba", caso_af),
    ("ag", "Pedido sin ítems o con producto sin peso: 422 con sin_peso, sin llamar a n8n", caso_ag),
    ("ah", "Pedido individual sobre la capacidad: 422 sin llamar a n8n", caso_ah),
    ("ai", "Total sobre capacidad * max_viajes: 422 con total y máximo", caso_ai),
    ("aj", "El payload a n8n trae peso_kg en cada parada y 'reglas'", caso_aj),
    ("ak", "Viaje con hueco en la numeración: 502", caso_ak),
    ("al", "orden_entrega no consecutivo dentro de un viaje: 502", caso_al),
    ("am", "Viaje sin paradas: 502", caso_am),
    ("an", "Peso de un viaje sobre la capacidad: 502", caso_an),
    ("ao", "'casa' en un viaje que no es el último, o último más pesado: 502", caso_ao),
    ("ap", "'casa' con peso >= umbral: 502; con peso == umbral termina en planta", caso_ap),
    ("aq", "'casa' con un solo viaje o termina_en inválido: 502", caso_aq),
    ("ar", "peso_kg de n8n distinto del recalculado: 502", caso_ar),
    ("as", "Falta 'viajes' (o el viaje de una parada): 502", caso_as),
    ("at", "Persistencia de viaje + respuesta con viajes + vista previa", caso_at),
    ("au", "sin_resolver deja viaje y orden_entrega en NULL", caso_au),
    ("av", "Replanificar sobrescribe viaje y orden_entrega", caso_av),
    ("aw", "GET pendientes con peso_kg (null si no calculable)", caso_aw),
    ("ax", "GET /rutas/actual: viajes, en_despacho y advertencias por (viaje, orden)", caso_ax),
    ("ay", "POST /rutas/confirmar con viajes: guarda viaje y valida el peso", caso_ay),
    ("az", "Pedido solo con productos de peso 0 se planifica y pesa 1 kg", caso_az),
    ("ba", "Dispensador (0 kg) + bidón pesa lo del bidón; mínimo 1 kg sobre el total", caso_ba),
    ("bb", "Peso 0 junto a producto de peso NULL: 422 que nombra solo el NULL", caso_bb),
    ("bc", "Producto con peso_kg negativo: 422 con sin_peso y productos_sin_peso", caso_bc),
]


async def _verificar_esquema() -> None:
    """Aborta con un mensaje claro si falta la migración de viaje."""
    existe = await _ejecutar(
        "select count(*) from information_schema.columns where table_schema = 'public' "
        "and table_name = 'pedido' and column_name = 'viaje'"
    )
    if existe.scalar_one() == 0:
        raise SystemExit(
            "ABORTADO: la columna pedido.viaje no existe. Aplica primero la migración "
            "supabase/migrations/20261026120000_agregar_viaje_pedido.sql (la aplica Julio en Supabase)."
        )


# Productos de prueba (nombre, peso_kg). "peso" y "peso_b" fijan w y w_b.
PESO_PRODUCTO = 12
PESO_PRODUCTO_B = 40


async def _crear_producto_temporal(nombre: str, peso_kg: int) -> int:
    """Producto de prueba (se borra en _limpiar): los reales no se modifican.
    peso_kg None = sin peso."""
    result = await _ejecutar(
        "insert into producto (nombre, precio_unitario, stock, peso_kg) values (:nombre, 1000, 0, :peso) returning id",
        nombre=nombre,
        peso=peso_kg,
    )
    return result.scalar_one()


async def main() -> None:
    await _verificar_esquema()
    await _limpiar()
    ctx: dict = {}
    original = (settings.N8N_ROUTE_WEBHOOK_URL, settings.N8N_ROUTE_WEBHOOK_SECRET, ruta_service._cliente_http)
    reglas_originales = (settings.RUTA_CAPACIDAD_KG, settings.RUTA_UMBRAL_CASA_KG, settings.RUTA_MAX_VIAJES)
    resultados = []
    try:
        ctx["w"], ctx["w_b"] = PESO_PRODUCTO, PESO_PRODUCTO_B
        ctx["prod"] = await _crear_producto_temporal("TEST_RUTAS_peso", PESO_PRODUCTO)
        ctx["prod_b"] = await _crear_producto_temporal("TEST_RUTAS_peso_b", PESO_PRODUCTO_B)
        ctx["nombre_sin_peso"] = "TEST_RUTAS_sin_peso"
        ctx["prod_sin_peso"] = await _crear_producto_temporal(ctx["nombre_sin_peso"], None)
        ctx["prod_cero"] = await _crear_producto_temporal("TEST_RUTAS_cero", 0)
        ctx["prod_negativo"] = await _crear_producto_temporal("TEST_RUTAS_negativo", -5)
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
        for clave in ("p1", "p2", "p3", "fuera", "cancelado", "entregado", "sin_direccion"):
            await _poner_items(ctx[clave], [(ctx["prod"], 1)])
        ctx["propios"] = {ctx[k] for k in ("p1", "p2", "p3", "fuera", "cancelado", "entregado", "sin_direccion")}
        ctx["cliente_antes"] = {cid: await _cliente(cid) for cid in (ctx["c1"], ctx["c2"])}
        print(f"Datos de prueba: {ctx}")

        settings.RUTA_CAPACIDAD_KG, settings.RUTA_UMBRAL_CASA_KG, settings.RUTA_MAX_VIAJES = 10 * ctx["w"], 6 * ctx["w"], 3
        ctx["n8n"] = N8nFalso()
        settings.N8N_ROUTE_WEBHOOK_URL = URL_N8N_PRUEBA
        settings.N8N_ROUTE_WEBHOOK_SECRET = SECRETO_N8N_PRUEBA
        ruta_service._cliente_http = ctx["n8n"].cliente
        app.dependency_overrides[get_current_user] = _usuario_prueba

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
        settings.RUTA_CAPACIDAD_KG, settings.RUTA_UMBRAL_CASA_KG, settings.RUTA_MAX_VIAJES = reglas_originales
        app.dependency_overrides.pop(get_current_user, None)
        await _limpiar()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
