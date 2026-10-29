"""
Script temporal para validar la historia #78 (criterio 3): prueba de carga
básica con varios clientes escribiendo al bot al mismo tiempo.

Cada cliente simulado hace un pedido completo por el webhook real
(POST /webhook -> webhook_queue -> procesar_mensaje), con un producto y una
cantidad distintos según el cliente. Todos los mensajes de todos los
clientes se envían en paralelo, y algunos se envían dos veces con el mismo
wamid, como cuando Meta reintenta una entrega. Al final se verifica que:

- el webhook responde de inmediato a cada POST
- cada cliente queda con exactamente un pedido, con su producto, su cantidad,
  su dirección y su total (sin mezcla de datos entre clientes)
- cada mensaje del bot llega al teléfono que corresponde y con sus datos
- las entregas repetidas no se procesan dos veces
- no hay errores ni derivaciones a la ejecutiva

El LLM y WhatsApp están SIMULADOS: no se llama a OpenAI ni se envían
mensajes reales. El LLM falso solo lee el texto del mensaje (nunca el
teléfono) y tarda un tiempo aleatorio, para que las conversaciones se
intercalen. La BD sí es real.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: clientes en el rango de teléfonos 56932302xxx. Al empezar
y al terminar se borran esos clientes, sus pedidos y detalles, sus mensajes
de mensaje_whatsapp, sus filas de conversacion_bot y las filas de auditoria
que generó el bot para ellos. No toca ningún otro cliente ni pedido.

Uso: python -m scripts.test_carga_conversaciones [cantidad_de_clientes]
(por defecto 20, máximo 999)
"""

import asyncio
import random
import re
import sys
import time
import uuid

import httpx
from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.order_flow as order_flow
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.services import webhook_queue
from app.services.conversacion_bot_service import marcar_activa
from app.services.draft_store import clear_draft, get_draft

# Estas pruebas reemplazan send_whatsapp_message por un fake: sin plantilla
# configurada, el aviso a la ejecutiva también pasa por ese fake (#152).
settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""

RANGO_TELEFONOS_SQL = r"^(56)?932302\d{3}$"
USUARIO_BOT = order_flow.USUARIO_AUDITORIA_BOT

CLIENTES_POR_DEFECTO = 20

# Probabilidad de que un mensaje se envíe dos veces con el mismo wamid.
PROBABILIDAD_DUPLICADO = 0.25

# Tiempo de respuesta simulado del LLM, en segundos.
LATENCIA_LLM = (0.05, 0.4)

# Un POST al webhook debe responder antes de esto (Meta reintenta si tarda).
MAX_RESPUESTA_WEBHOOK = 2.0

PRODUCTOS = [
    (12, "nuevo", "Bidón 12L Nuevo"),
    (12, "recarga", "Bidón 12L Recarga"),
    (20, "nuevo", "Bidón 20L Nuevo"),
    (20, "recarga", "Bidón 20L Recarga"),
]

RESPUESTA_LLM_BASE = {
    "intencion": "pedido",
    "productos": [],
    "aclaracion_pendiente": None,
    "usa_direccion_habitual": False,
    "direccion_texto": None,
    "esperando_ubicacion": False,
    "ubicacion_rechazada": False,
    "notas": None,
    "pedido_completo": False,
    "producto_consultado": None,
    "nombre_cliente": None,
    "correccion_propuesta": None,
    "respuesta_sugerida": "",
}

PATRON_PEDIDO = re.compile(r"quiero (\d+) bidones de (12|20) litros (nuevo|recarga)")


# --------------------------------------------------------------------------
# Clientes simulados
# --------------------------------------------------------------------------


def _clientes(cantidad: int) -> list[dict]:
    clientes = []
    for i in range(1, cantidad + 1):
        capacidad, variante, producto = PRODUCTOS[i % len(PRODUCTOS)]
        clientes.append(
            {
                "telefono": f"56932302{i:03d}",
                "nombre": f"Carga {i:03d}",
                "direccion": f"Calle Carga {i:03d} #{100 + i}, Viña del Mar",
                "cantidad": i % 9 + 1,
                "producto": producto,
                "mensajes": [
                    f"quiero {i % 9 + 1} bidones de {capacidad} litros {variante}",
                    "sí",
                    "no",
                    "sí",
                ],
            }
        )
    return clientes


# --------------------------------------------------------------------------
# LLM y WhatsApp simulados
# --------------------------------------------------------------------------


async def _llm_falso(phone: str, message: str, es_cliente_nuevo: bool, context: dict | None) -> dict:
    """Interpreta solo el texto del mensaje y la pregunta pendiente. Nunca
    usa el teléfono: si los datos se mezclaran entre clientes, sería por el
    backend y no por el LLM falso."""
    await asyncio.sleep(random.uniform(*LATENCIA_LLM))
    resultado = dict(RESPUESTA_LLM_BASE)
    pregunta = (context or {}).get("pregunta_pendiente")
    texto = (message or "").lower()

    coincidencia = PATRON_PEDIDO.search(texto)
    if coincidencia:
        cantidad, capacidad, variante = coincidencia.groups()
        nombre = f"Bidón {capacidad}L {variante.capitalize()}"
        resultado["productos"] = [{"nombre_producto": nombre, "cantidad": int(cantidad), "operacion": "agregar"}]
        resultado["pedido_completo"] = True
    elif pregunta == "confirmar_direccion" and texto.startswith("s"):
        resultado["usa_direccion_habitual"] = True
    return resultado


class WhatsAppFalso:
    def __init__(self):
        self.enviados: list[tuple[str, str]] = []

    async def enviar(self, to: str, message: str) -> None:
        self.enviados.append((to, message))

    def a(self, telefono: str) -> list[str]:
        return [mensaje for destino, mensaje in self.enviados if destino == telefono]


# --------------------------------------------------------------------------
# BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar(clientes: list[dict]) -> None:
    ids_clientes = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    ids_pedidos = f"select id from pedido where cliente_id in ({ids_clientes})"
    await _ejecutar(
        f"delete from auditoria where usuario = :usuario and ("
        f"(entidad = 'pedido' and entidad_id in ({ids_pedidos})) or "
        f"(entidad = 'cliente' and entidad_id in ({ids_clientes})))",
        usuario=USUARIO_BOT,
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from detalle_pedido where pedido_id in ({ids_pedidos})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from mensaje_whatsapp where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from conversacion_bot where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    for cliente in clientes:
        clear_draft(cliente["telefono"])


async def _preparar(clientes: list[dict]) -> None:
    for cliente in clientes:
        result = await _ejecutar(
            "insert into cliente (nombre, telefono, direccion) values (:nombre, :telefono, :direccion) returning id",
            nombre=cliente["nombre"],
            telefono=cliente["telefono"],
            direccion=cliente["direccion"],
        )
        cliente["id"] = result.scalar_one()
        await marcar_activa(cliente["telefono"])


async def _pedidos_por_cliente() -> dict[int, list[dict]]:
    result = await _ejecutar(
        "select p.id, p.cliente_id, p.direccion_despacho, p.total, pr.nombre as producto, "
        "d.cantidad_solicitada, d.precio_unitario "
        "from pedido p join detalle_pedido d on d.pedido_id = p.id join producto pr on pr.id = d.producto_id "
        "where p.cliente_id in (select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango)",
        rango=RANGO_TELEFONOS_SQL,
    )
    pedidos: dict[int, list[dict]] = {}
    for fila in result.mappings():
        pedidos.setdefault(fila["cliente_id"], []).append(dict(fila))
    return pedidos


async def _mensajes_registrados() -> dict[str, int]:
    result = await _ejecutar(
        "select telefono, count(*) as n from mensaje_whatsapp where telefono ~ :rango and direccion = 'entrante' group by telefono",
        rango=RANGO_TELEFONOS_SQL,
    )
    return {fila.telefono: fila.n for fila in result}


# --------------------------------------------------------------------------
# Envío
# --------------------------------------------------------------------------


def _payload(telefono: str, texto: str, wamid: str) -> dict:
    mensaje = {"from": telefono, "id": wamid, "type": "text", "text": {"body": texto}}
    return {"entry": [{"changes": [{"value": {"messages": [mensaje]}}]}]}


async def _conversar(http: httpx.AsyncClient, cliente: dict, tiempos: list[float], duplicados: list[int]) -> None:
    """Envía los mensajes del cliente en orden, sin esperar las respuestas
    del bot (como un cliente que escribe rápido). Algunos se envían dos veces
    con el mismo wamid."""
    for texto in cliente["mensajes"]:
        await asyncio.sleep(random.uniform(0, 0.05))
        wamid = f"wamid.carga.{uuid.uuid4().hex}"
        envios = 2 if random.random() < PROBABILIDAD_DUPLICADO else 1
        if envios == 2:
            duplicados.append(1)
        for _ in range(envios):
            inicio = time.perf_counter()
            respuesta = await http.post("/webhook", json=_payload(cliente["telefono"], texto, wamid))
            tiempos.append(time.perf_counter() - inicio)
            if respuesta.status_code != 200:
                raise RuntimeError(f"POST /webhook respondió {respuesta.status_code}")


# --------------------------------------------------------------------------
# Verificación
# --------------------------------------------------------------------------


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


def _verificar_cliente(cliente: dict, pedidos: list[dict], enviados: list[str], registrados: int) -> list[str]:
    """Devuelve la lista de problemas de este cliente (vacía si está bien)."""
    problemas = []
    if len({p["id"] for p in pedidos}) != 1:
        return [f"tiene {len({p['id'] for p in pedidos})} pedidos (se esperaba 1)"]

    linea = pedidos[0]
    if len(pedidos) != 1:
        problemas.append(f"el pedido tiene {len(pedidos)} líneas (se esperaba 1)")
    if linea["producto"] != cliente["producto"]:
        problemas.append(f"producto {linea['producto']!r} (se esperaba {cliente['producto']!r})")
    if linea["cantidad_solicitada"] != cliente["cantidad"]:
        problemas.append(f"cantidad {linea['cantidad_solicitada']} (se esperaba {cliente['cantidad']})")
    if linea["direccion_despacho"] != cliente["direccion"]:
        problemas.append(f"dirección {linea['direccion_despacho']!r} (se esperaba {cliente['direccion']!r})")
    if float(linea["total"]) != float(linea["precio_unitario"]) * cliente["cantidad"]:
        problemas.append(f"total {linea['total']} no calza con {cliente['cantidad']} x {linea['precio_unitario']}")

    resumenes = [m for m in enviados if "Resumen de tu pedido" in m]
    if len(resumenes) != 1:
        problemas.append(f"recibió {len(resumenes)} resúmenes (se esperaba 1)")
    elif cliente["direccion"] not in resumenes[0] or f"{cliente['cantidad']}x {cliente['producto']}" not in resumenes[0]:
        problemas.append("el resumen no trae su dirección o su producto")

    confirmaciones = [m for m in enviados if "confirmado" in m]
    if len(confirmaciones) != 1 or f"#{linea['id']}" not in confirmaciones[0]:
        problemas.append(f"la confirmación no corresponde a su pedido #{linea['id']}: {confirmaciones}")

    if any(m == whatsapp_route.MENSAJE_ERROR_GENERICO for m in enviados):
        problemas.append("recibió el mensaje de error genérico")
    if len(enviados) != len(cliente["mensajes"]):
        problemas.append(f"recibió {len(enviados)} mensajes del bot (se esperaban {len(cliente['mensajes'])})")
    if registrados != len(cliente["mensajes"]):
        problemas.append(f"se registraron {registrados} mensajes entrantes (se esperaban {len(cliente['mensajes'])})")
    if get_draft(cliente["telefono"]) is not None:
        problemas.append("le quedó un borrador de pedido abierto")
    return problemas


async def main() -> None:
    cantidad = int(sys.argv[1]) if len(sys.argv) > 1 else CLIENTES_POR_DEFECTO
    if not 1 <= cantidad <= 999:
        print("La cantidad de clientes debe estar entre 1 y 999")
        return
    clientes = _clientes(cantidad)

    whatsapp = WhatsAppFalso()
    originales = (order_flow.interpret_message, whatsapp_route.send_whatsapp_message, order_flow.send_whatsapp_message)
    order_flow.interpret_message = _llm_falso
    whatsapp_route.send_whatsapp_message = whatsapp.enviar
    order_flow.send_whatsapp_message = whatsapp.enviar

    v = Verificador()
    try:
        await _limpiar(clientes)
        await _preparar(clientes)
        total_mensajes = sum(len(c["mensajes"]) for c in clientes)
        print(f"{cantidad} clientes, {total_mensajes} mensajes, enviados todos en paralelo")

        tiempos: list[float] = []
        duplicados: list[int] = []
        inicio = time.perf_counter()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            await asyncio.gather(*(_conversar(http, c, tiempos, duplicados) for c in clientes))
            await webhook_queue.esperar_pendientes()
        duracion = time.perf_counter() - inicio

        print(f"Terminó en {duracion:.1f} s ({total_mensajes / duracion:.1f} mensajes por segundo)")
        print(f"Entregas repetidas enviadas: {len(duplicados)}")
        print()

        v.check(
            max(tiempos) < MAX_RESPUESTA_WEBHOOK,
            f"el webhook responde de inmediato (máximo {max(tiempos) * 1000:.0f} ms, {len(tiempos)} POST)",
        )

        pedidos = await _pedidos_por_cliente()
        registrados = await _mensajes_registrados()
        v.check(sum(len({p["id"] for p in ps}) for ps in pedidos.values()) == cantidad, f"se crearon {cantidad} pedidos en total")

        con_problemas = 0
        for cliente in clientes:
            problemas = _verificar_cliente(
                cliente,
                pedidos.get(cliente["id"], []),
                whatsapp.a(cliente["telefono"]),
                registrados.get(cliente["telefono"], 0),
            )
            if problemas:
                con_problemas += 1
                print(f"    {cliente['telefono']}: {'; '.join(problemas)}")
        v.check(con_problemas == 0, "cada cliente tiene su pedido, con su producto, cantidad, dirección y total")

        telefonos = {c["telefono"] for c in clientes}
        v.check(all(destino in telefonos for destino, _ in whatsapp.enviados), "ningún mensaje del bot fue a otro teléfono")
        v.check(
            not whatsapp.a(settings.EJECUTIVA_PHONE) if settings.EJECUTIVA_PHONE else True,
            "ninguna conversación se derivó a la ejecutiva",
        )
    finally:
        order_flow.interpret_message, whatsapp_route.send_whatsapp_message, order_flow.send_whatsapp_message = originales
        await _limpiar(clientes)

    print()
    print("=" * 70)
    print(f"Resultado: {'OK' if not v.errores else 'FALLO'} ({cantidad} clientes)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())