"""
Script para validar el flujo del repartidor contra la BD: roles, asignar la ruta,
marcar entregado (con todo, menos o más de lo pedido, o con productos que no
pidió), no entregado, corregir, reagendar, cancelar y combustible. La
autenticación JWT se reemplaza con dependency_overrides. Requiere aplicadas las
migraciones 20261106210000_repartidor_y_resultado_entrega.sql y
20261110180000_productos_agregados_en_la_entrega.sql.

Datos de prueba: clientes en el rango de teléfonos 56932400xxx, productos con
prefijo TEST_REPARTIDOR_ y repartidores con nombre TEST_REPARTIDOR_. Al empezar y
al terminar, también si falla, se borra solo eso y su auditoría (usuarios
test-repartidor-*). No toca ningún otro dato.

Uso: python -m scripts.test_repartidor
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.core.security import get_current_user
from app.main import app
from app.models.cliente import Cliente
from app.models.detalle_pedido import DetallePedido
from app.models.enums import EstadoPedido
from app.models.pedido import Pedido
from app.models.producto import Producto
from app.models.repartidor import Repartidor
from app.services.entrega import hoy_chile

TELEFONO_BASE = "56932400"
PREFIJO = "TEST_REPARTIDOR_"
USUARIO_ADMIN = "test-repartidor-admin@sigpa.local"
USUARIO_RENZO = "test-repartidor-renzo@sigpa.local"
USUARIO_OTRO = "test-repartidor-otro@sigpa.local"
UUID_RENZO = str(uuid4())
UUID_OTRO = str(uuid4())

usuario_actual: dict = {}
fallos: list[str] = []
pasadas = 0


def revisar(condicion: bool, descripcion: str, detalle=None) -> None:
    global pasadas
    if condicion:
        pasadas += 1
        print(f"  ok   {descripcion}")
    else:
        fallos.append(descripcion)
        print(f"  FALLA {descripcion}" + (f" -> {detalle}" if detalle is not None else ""))


def como(rol: str | None, email: str = USUARIO_ADMIN, user_id: str = "test") -> None:
    usuario_actual.clear()
    usuario_actual.update({"user_id": user_id, "email": email, "rol": rol})


async def limpiar() -> None:
    async with SessionLocal() as session:
        await session.execute(
            text(
                "delete from public.auditoria where usuario like 'test-repartidor-%'"
            )
        )
        await session.execute(
            text(
                "delete from public.carga_combustible where repartidor_id in "
                "(select id from public.repartidor where nombre like :p)"
            ),
            {"p": f"{PREFIJO}%"},
        )
        await session.execute(
            text(
                "delete from public.pedido where cliente_id in "
                "(select id from public.cliente where telefono like :t)"
            ),
            {"t": f"{TELEFONO_BASE}%"},
        )
        await session.execute(text("delete from public.cliente where telefono like :t"), {"t": f"{TELEFONO_BASE}%"})
        await session.execute(text("delete from public.producto where nombre like :p"), {"p": f"{PREFIJO}%"})
        await session.execute(text("delete from public.repartidor where nombre like :p"), {"p": f"{PREFIJO}%"})
        await session.commit()


async def preparar() -> dict:
    async with SessionLocal() as session:
        barato = Producto(nombre=f"{PREFIJO}recarga", precio_unitario=2500, stock=0)
        caro = Producto(nombre=f"{PREFIJO}nuevo", precio_unitario=6000, stock=0)
        # No está en ningún pedido: el repartidor lo agrega al entregar.
        dispensador = Producto(nombre=f"{PREFIJO}dispensador", precio_unitario=7000, stock=0)
        renzo = Repartidor(auth_user_id=UUID_RENZO, nombre=f"{PREFIJO}Renzo")
        otro = Repartidor(auth_user_id=UUID_OTRO, nombre=f"{PREFIJO}Otro")
        session.add_all([barato, caro, dispensador, renzo, otro])
        await session.flush()

        pedidos = {}
        for i, nombre in enumerate(["todo", "menos", "mas", "falla", "cancelar", "ajeno", "pendiente"], start=1):
            cliente = Cliente(nombre=f"Prueba {nombre}", telefono=f"{TELEFONO_BASE}{i:03d}", direccion=f"Calle {i}")
            session.add(cliente)
            await session.flush()
            pedido = Pedido(
                cliente_id=cliente.id,
                estado=EstadoPedido.PENDIENTE if nombre == "pendiente" else EstadoPedido.CONFIRMADO,
                total=13000,
                orden_entrega=None if nombre == "pendiente" else i,
                viaje=None if nombre == "pendiente" else 1,
            )
            pedido.detalles = [
                DetallePedido(producto_id=barato.id, cantidad_solicitada=2, precio_unitario=2500),
                DetallePedido(producto_id=caro.id, cantidad_solicitada=1, precio_unitario=6000),
            ]
            session.add(pedido)
            await session.flush()
            pedidos[nombre] = pedido.id
        await session.commit()
        return {
            "pedidos": pedidos,
            "renzo": renzo.id,
            "otro": otro.id,
            "barato": barato.id,
            "dispensador": dispensador.id,
        }


async def parada_de(api, pedido_id: int) -> dict | None:
    respuesta = await api.get("/repartidor/ruta")
    return next((p for p in respuesta.json()["paradas"] if p["id"] == pedido_id), None)


async def probar_roles(api, ctx) -> None:
    print("Roles")
    como(None)
    revisar((await api.get("/pedidos")).status_code == 403, "sin rol no entra al panel")
    revisar((await api.get("/repartidor/ruta")).status_code == 403, "sin rol no entra a la ruta")
    revisar((await api.get("/productos")).status_code == 200, "los precios de la landing siguen públicos")
    como("repartidor", USUARIO_RENZO, UUID_RENZO)
    for ruta in ["/pedidos", "/clientes", "/reportes/productos", "/rutas/actual", "/repartidores"]:
        revisar((await api.get(ruta)).status_code == 403, f"el repartidor no entra a {ruta}")
    revisar((await api.post("/productos", json={"nombre": "x", "precio_unitario": 1})).status_code == 403, "el repartidor no crea productos")
    revisar((await api.get("/productos")).status_code == 200, "el repartidor ve el catálogo para agregar productos")
    como("repartidor", USUARIO_RENZO, str(uuid4()))
    revisar((await api.get("/repartidor/ruta")).status_code == 403, "con el rol pero sin registro de repartidor no hay ruta")
    como("admin")
    revisar((await api.get("/repartidor/ruta")).status_code == 403, "el admin no usa la vista del repartidor")
    revisar((await api.get("/pedidos")).status_code == 200, "el admin entra al panel")


async def probar_asignacion(api, ctx) -> None:
    print("Asignar la ruta")
    p = ctx["pedidos"]
    como("admin")
    nombres = [r["nombre"] for r in (await api.get("/repartidores")).json()]
    revisar(f"{PREFIJO}Renzo" in nombres, "lista los repartidores")
    mios = [p[n] for n in ("todo", "menos", "mas", "falla", "cancelar")]
    r = await api.post("/rutas/asignar", json={"repartidor_id": ctx["renzo"], "pedido_ids": mios})
    revisar(r.status_code == 200 and r.json()["asignados"] == 5, "asigna las paradas", r.text)
    r = await api.post("/rutas/asignar", json={"repartidor_id": ctx["otro"], "pedido_ids": [p["ajeno"]]})
    revisar(r.status_code == 200, "asigna otra parada a otro repartidor")
    r = await api.post("/rutas/asignar", json={"repartidor_id": ctx["renzo"], "pedido_ids": [p["pendiente"]]})
    revisar(r.status_code == 409, "un pendiente no se asigna", r.status_code)
    r = await api.post("/rutas/asignar", json={"repartidor_id": 99999999, "pedido_ids": mios})
    revisar(r.status_code == 404, "un repartidor inexistente responde 404", r.status_code)
    revisar((await api.post("/rutas/asignar", json={"repartidor_id": None, "pedido_ids": []})).json()["asignados"] == 0, "sin paradas no hace nada")

    como("repartidor", USUARIO_RENZO, UUID_RENZO)
    datos = (await api.get("/repartidor/ruta")).json()
    ids = [x["id"] for x in datos["paradas"]]
    revisar(ids == mios, "ve solo sus paradas, en orden", ids)
    revisar(datos["repartidor"]["nombre"] == f"{PREFIJO}Renzo", "ve su nombre")
    primera = datos["paradas"][0]
    revisar(primera["direccion"] == "Calle 1" and len(primera["lineas"]) == 2, "la parada trae dirección y productos", primera)


async def probar_entregas(api, ctx) -> None:
    print("Entregado")
    p = ctx["pedidos"]
    como("repartidor", USUARIO_RENZO, UUID_RENZO)
    resultado = lambda pid, cuerpo: api.post(f"/repartidor/paradas/{pid}/resultado", json=cuerpo)

    r = await resultado(p["ajeno"], {"resultado": "entregado", "medio_pago": "efectivo"})
    revisar(r.status_code == 403, "no marca una parada de otro repartidor", r.status_code)
    r = await resultado(p["todo"], {"resultado": "entregado"})
    revisar(r.status_code == 422, "entregado exige método de pago", r.status_code)
    r = await resultado(p["todo"], {"resultado": "entregado", "medio_pago": "tarjeta"})
    revisar(r.status_code == 422, "el cliente no paga con tarjeta", r.status_code)

    r = await resultado(p["todo"], {"resultado": "entregado", "medio_pago": "efectivo"})
    d = r.json()
    revisar(
        r.status_code == 200 and d["estado"] == "entregado" and d["total"] == 11000 and not d["entrega_ajustada"],
        "todo lo pedido: total recalculado y sin ajuste",
        r.text,
    )
    revisar(all(l["cantidad_entregada"] == l["cantidad_solicitada"] for l in d["lineas"]), "guarda las cantidades entregadas")
    r = await resultado(p["todo"], {"resultado": "entregado", "medio_pago": "efectivo"})
    revisar(r.status_code == 409, "dos toques no registran dos veces", r.status_code)

    menos = await parada_de(api, p["menos"])
    linea_barata = next(l for l in menos["lineas"] if l["precio_unitario"] == 2500)
    r = await resultado(p["menos"], {"resultado": "entregado", "medio_pago": "transferencia", "lineas": [{"detalle_id": linea_barata["id"], "cantidad_entregada": 1}]})
    d = r.json()
    revisar(d["total"] == 8500 and d["entrega_ajustada"] and d["medio_pago"] == "transferencia", "menos de lo pedido baja el total", r.text)

    mas = await parada_de(api, p["mas"])
    linea_barata = next(l for l in mas["lineas"] if l["precio_unitario"] == 2500)
    r = await resultado(p["mas"], {"resultado": "entregado", "medio_pago": "efectivo", "lineas": [{"detalle_id": linea_barata["id"], "cantidad_entregada": 3}]})
    d = r.json()
    revisar(d["total"] == 13500 and d["entrega_ajustada"], "más de lo pedido sube el total", r.text)

    falla = await parada_de(api, p["falla"])
    r = await resultado(p["falla"], {"resultado": "entregado", "medio_pago": "efectivo", "lineas": [{"detalle_id": l["id"], "cantidad_entregada": 0} for l in falla["lineas"]]})
    revisar(r.status_code == 422, "cero unidades no es una entrega", r.status_code)
    r = await resultado(p["falla"], {"resultado": "entregado", "medio_pago": "efectivo", "lineas": [{"detalle_id": 99999999, "cantidad_entregada": 1}]})
    revisar(r.status_code == 422, "una línea de otro pedido se rechaza", r.status_code)

    print("Corregir")
    r = await resultado(p["menos"], {"resultado": None})
    d = r.json()
    revisar(
        r.status_code == 200 and d["estado"] == "en_despacho" and d["total"] == 11000 and d["medio_pago"] is None and not d["entrega_ajustada"],
        "corregir devuelve el pedido como estaba",
        r.text,
    )
    revisar(all(l["cantidad_entregada"] is None for l in d["lineas"]), "corregir borra las cantidades")
    r = await resultado(p["menos"], {"resultado": None})
    revisar(r.status_code == 409, "no se corrige lo que no está marcado", r.status_code)

    print("Productos que no pidió")
    entregar = lambda extras, lineas=None: resultado(
        p["menos"],
        {"resultado": "entregado", "medio_pago": "efectivo", "extras": extras, **({"lineas": lineas} if lineas else {})},
    )
    r = await entregar([{"producto_id": ctx["barato"], "cantidad": 1}])
    revisar(r.status_code == 422, "un producto que ya está en el pedido se ajusta en su línea", r.status_code)
    r = await entregar([{"producto_id": 99999999, "cantidad": 1}])
    revisar(r.status_code == 404, "un producto inexistente responde 404", r.status_code)
    r = await entregar([{"producto_id": ctx["dispensador"], "cantidad": 1}] * 2)
    revisar(r.status_code == 422, "el mismo producto dos veces se rechaza", r.status_code)
    r = await entregar([{"producto_id": ctx["dispensador"], "cantidad": 0}])
    revisar(r.status_code == 422, "agregar 0 unidades se rechaza", r.status_code)
    revisar((await parada_de(api, p["menos"]))["estado"] == "en_despacho", "los rechazos no marcan la parada")

    r = await entregar([{"producto_id": ctx["dispensador"], "cantidad": 2}])
    d = r.json()
    agregada = next((l for l in d.get("lineas", []) if l["producto_id"] == ctx["dispensador"]), None)
    revisar(
        r.status_code == 200 and d["total"] == 25000 and d["entrega_ajustada"] and len(d["lineas"]) == 3,
        "lo pedido más 2 dispensadores: el total los suma",
        r.text,
    )
    revisar(
        agregada is not None
        and agregada["cantidad_solicitada"] == 0
        and agregada["cantidad_entregada"] == 2
        and agregada["precio_unitario"] == 7000
        and agregada["producto_nombre"] == f"{PREFIJO}dispensador",
        "el agregado queda como línea con lo pedido en 0 y el precio del catálogo",
        agregada,
    )
    r = await resultado(p["menos"], {"resultado": None})
    d = r.json()
    revisar(
        r.status_code == 200 and d["total"] == 11000 and len(d["lineas"]) == 2 and not d["entrega_ajustada"],
        "corregir quita lo agregado y vuelve al total del pedido",
        r.text,
    )
    async with SessionLocal() as session:
        lineas_bd = (
            await session.execute(text("select count(*) from public.detalle_pedido where pedido_id = :id"), {"id": p["menos"]})
        ).scalar_one()
    revisar(lineas_bd == 2, "la línea agregada se borra de la BD", lineas_bd)

    menos = await parada_de(api, p["menos"])
    r = await entregar(
        [{"producto_id": ctx["dispensador"], "cantidad": 1}],
        [{"detalle_id": l["id"], "cantidad_entregada": 0} for l in menos["lineas"]],
    )
    d = r.json()
    revisar(r.status_code == 200 and d["total"] == 7000, "solo lo que no pidió también es una entrega", r.text)
    r = await resultado(p["menos"], {"resultado": None})
    revisar(r.status_code == 200 and len(r.json()["lineas"]) == 2, "y se corrige igual", r.text)

    async with SessionLocal() as session:
        pedido = await session.get(Pedido, p["todo"])
        pedido.resultado_en = datetime.utcnow() - timedelta(days=2)
        await session.commit()
    r = await resultado(p["todo"], {"resultado": None})
    revisar(r.status_code == 409, "lo de otro día no se corrige", r.status_code)
    revisar(await parada_de(api, p["todo"]) is None, "lo resuelto otro día ya no aparece en la ruta")


async def probar_no_entregado(api, ctx) -> None:
    print("No entregado")
    p = ctx["pedidos"]
    resultado = lambda pid, cuerpo: api.post(f"/repartidor/paradas/{pid}/resultado", json=cuerpo)
    anteriores = lambda: api.get("/pedidos/no-entregados-anteriores")

    como("repartidor", USUARIO_RENZO, UUID_RENZO)
    r = await resultado(p["falla"], {"resultado": "no_entregado"})
    revisar(r.status_code == 200 and r.json()["estado"] == "no_entregado", "marca no entregado", r.text)
    r = await resultado(p["falla"], {"resultado": None})
    revisar(r.status_code == 200 and r.json()["estado"] == "en_despacho", "corregir un no entregado")
    como("admin")
    revisar(not [x for x in (await anteriores()).json() if x["id"] == p["falla"]], "corregir no deja un intento fantasma")

    como("repartidor", USUARIO_RENZO, UUID_RENZO)
    await resultado(p["falla"], {"resultado": "no_entregado"})
    await resultado(p["cancelar"], {"resultado": "no_entregado"})
    como("admin")
    revisar(not [x for x in (await anteriores()).json() if x["id"] == p["falla"]], "el intento vigente no se duplica: ya está en /pedidos")
    revisar(next(x for x in (await api.get("/pedidos")).json() if x["id"] == p["falla"])["estado"] == "no_entregado", "el panel lo ve no entregado")

    hoy = hoy_chile()
    pasado = hoy - timedelta(days=1)
    r = await api.post(f"/pedidos/{p['falla']}/reagendar", json={"para": pasado.isoformat()})
    revisar(r.status_code == 422, "no se reagenda al pasado", r.status_code)
    sabado = hoy + timedelta(days=(5 - hoy.weekday()) % 7 or 7)
    r = await api.post(f"/pedidos/{p['falla']}/reagendar", json={"para": sabado.isoformat()})
    revisar(r.status_code == 422, "no se reagenda a fin de semana", r.status_code)
    lunes = hoy + timedelta(days=7 - hoy.weekday())
    r = await api.post(f"/pedidos/{p['falla']}/reagendar", json={"para": lunes.isoformat()})
    d = r.json()
    revisar(
        r.status_code == 200 and d["estado"] == "pendiente" and d["orden_entrega"] is None and d["viaje"] is None and d["repartidor_id"] is None and d["reagendado_para"] == lunes.isoformat(),
        "reagendar vuelve a Pendientes sin lugar en la ruta",
        r.text,
    )
    pendientes = [x["pedido_id"] for x in (await api.get("/rutas/pedidos-pendientes")).json()]
    revisar(p["falla"] in pendientes, "el reagendado se puede planificar de nuevo")
    fila = next((x for x in (await anteriores()).json() if x["id"] == p["falla"]), None)
    revisar(fila and fila["estado"] == "no_entregado" and fila["orden_entrega"] == 4 and fila["viaje"] == 1 and fila["resultado_en"], "el intento fallido sigue disponible para Reportes y Despachos", fila)
    r = await api.post(f"/pedidos/{p['falla']}/reagendar", json={"para": lunes.isoformat()})
    revisar(r.status_code == 409, "solo se reagenda lo no entregado", r.status_code)

    r = await api.post(f"/pedidos/{p['cancelar']}/cancelar")
    revisar(r.status_code == 200 and r.json()["estado"] == "cancelado", "cancela un no entregado", r.text)
    revisar(bool([x for x in (await anteriores()).json() if x["id"] == p["cancelar"]]), "el intento del cancelado se conserva")
    r = await api.post(f"/pedidos/{p['cancelar']}/cancelar")
    revisar(r.status_code == 409, "no se cancela dos veces", r.status_code)
    historial = (await api.get(f"/pedidos/{p['cancelar']}/historial")).json()
    revisar(any(h["estado_nuevo"] == "no_entregado" for h in historial) and historial[-1]["estado_nuevo"] == "cancelado", "el historial muestra el camino", historial)


async def probar_entrega_panel(api, ctx) -> None:
    print("Entrega desde el panel")
    p = ctx["pedidos"]
    como("admin")
    detalle = (await api.get(f"/pedidos/{p['menos']}")).json()
    lineas = [{"detalle_id": l["id"], "cantidad_entregada": l["cantidad_solicitada"]} for l in detalle["lineas"]]
    lineas[0]["cantidad_entregada"] -= 1
    r = await api.post(f"/pedidos/{p['menos']}/entrega", json={"lineas": lineas})
    d = r.json()
    revisar(r.status_code == 200 and d["entrega_ajustada"] and d["resultado_en"] and d["medio_pago"] is None, "la entrega del panel también guarda hora y ajuste", r.text)


async def probar_combustible(api, ctx) -> None:
    print("Combustible")
    como("repartidor", USUARIO_RENZO, UUID_RENZO)
    enviar = lambda cuerpo: api.post("/repartidor/combustible", json=cuerpo)
    revisar((await enviar({"cargo": True, "monto": 20000})).status_code == 422, "cargar exige método de pago")
    revisar((await enviar({"cargo": True, "medio_pago": "tarjeta"})).status_code == 422, "cargar exige monto")
    revisar((await enviar({"cargo": True, "monto": 20000, "medio_pago": "transferencia"})).status_code == 422, "el combustible no se paga por transferencia")
    revisar((await enviar({"cargo": False, "monto": 20000})).status_code == 422, "sin carga no hay monto")
    r = await enviar({"cargo": True, "monto": 20000, "medio_pago": "tarjeta"})
    carga = r.json()
    revisar(r.status_code == 201 and carga["monto"] == 20000, "registra la carga", r.text)
    revisar((await enviar({"cargo": False})).status_code == 201, "informa que no cargó")
    revisar(len((await api.get("/repartidor/combustible")).json()) == 2, "ve lo suyo de hoy")

    como("repartidor", USUARIO_OTRO, UUID_OTRO)
    revisar((await api.get("/repartidor/combustible")).json() == [], "no ve lo de otro repartidor")
    revisar((await api.delete(f"/repartidor/combustible/{carga['id']}")).status_code == 404, "no borra lo de otro repartidor")

    como("admin")
    hoy = hoy_chile().isoformat()
    filas = (await api.get(f"/combustible?desde={hoy}&hasta={hoy}")).json()
    revisar(any(f["id"] == carga["id"] for f in filas), "el admin ve el combustible del período")
    revisar((await api.get(f"/combustible?desde={hoy}&hasta=2000-01-01")).status_code == 422, "un período al revés se rechaza")
    grupos = (await api.get("/rutas/combustible")).json()
    revisar(any(g["repartidor"] == f"{PREFIJO}Renzo" and len(g["registros"]) == 2 for g in grupos), "el admin ve lo de hoy por repartidor", grupos)

    como("repartidor", USUARIO_RENZO, UUID_RENZO)
    revisar((await api.delete(f"/repartidor/combustible/{carga['id']}")).status_code == 204, "quita su propia carga")


async def main() -> None:
    app.dependency_overrides[get_current_user] = lambda: dict(usuario_actual)
    await limpiar()
    try:
        ctx = await preparar()
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://test") as api:
            await probar_roles(api, ctx)
            await probar_asignacion(api, ctx)
            await probar_entregas(api, ctx)
            await probar_no_entregado(api, ctx)
            await probar_entrega_panel(api, ctx)
            await probar_combustible(api, ctx)
    finally:
        await limpiar()
        del app.dependency_overrides[get_current_user]
        await engine.dispose()

    print(f"\n{pasadas} ok, {len(fallos)} con falla")
    if fallos:
        for fallo in fallos:
            print(f"  - {fallo}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
