"""Generación de la ruta de reparto (EP-04).

Flujo: la ejecutiva valida en el panel los pedidos "pendiente" y pide la ruta
con los que quedan (POST /rutas/planificar). El backend arma las paradas
desde la BD, llama de forma síncrona al webhook de n8n (que geocodifica con
OpenRouteService y optimiza el orden; las coordenadas del depósito viven en
n8n), valida la respuesta y la persiste. n8n NO escribe en la base de datos.

Contrato con n8n
----------------
Request (POST a N8N_ROUTE_WEBHOOK_URL, header X-Route-Secret con
N8N_ROUTE_WEBHOOK_SECRET):

    {"paradas": [{"pedido_id": int, "direccion_texto": str | null,
                  "latitud": float | null, "longitud": float | null,
                  "peso_kg": int}],
     "reglas": {"capacidad_kg": int, "umbral_casa_kg": int, "max_viajes": int}}

peso_kg (entero >= 1) es el peso del pedido: SUM(cantidad_solicitada *
producto.peso_kg), redondeado hacia arriba una sola vez al final.

Respuesta esperada (200, JSON):

    {"ruta": [{"pedido_id": int, "viaje": int, "orden_entrega": int,
               "latitud": float, "longitud": float}],
     "viajes": [{"viaje": int, "peso_kg": int, "termina_en": "planta" | "casa",
                 "total_paradas": int}],
     "sin_resolver": [{"pedido_id": int, "motivo": str}]}

Cada pedido enviado debe aparecer exactamente una vez, en "ruta" o en
"sin_resolver" (no se pudo geocodificar o ubicar). n8n decide el reparto en
viajes y su orden; el backend valida y persiste:

- "viajes" es obligatorio, con números consecutivos 1..V (V <= max_viajes),
  todos con al menos una parada, y cada parada referencia un viaje existente.
- orden_entrega es consecutivo 1..N DENTRO de cada viaje (1 = primera parada).
- El peso de cada viaje se recalcula con los pesos del backend (no se confía
  en el de n8n): debe ser <= capacidad_kg y coincidir con el que informa n8n.
- Todos los viajes parten de la planta. Los pesados terminan en la planta; el
  último viaje (el más liviano) termina en la casa del despachador
  (termina_en = "casa") solo si V >= 2 y su peso es ESTRICTAMENTE menor que
  umbral_casa_kg; si no, en la planta. Las coordenadas de la casa viven solo
  en n8n: el backend no las conoce. termina_en no se guarda, se deriva.

Si la respuesta no cumple el contrato no se escribe nada.

Persistencia (una sola transacción, solo si todo es válido): todos los
pedidos de la solicitud pasan a "confirmado"; los de "ruta" reciben su viaje
y su orden_entrega y los de "sin_resolver" quedan con ambos en null; las
coordenadas se guardan en el pedido solo si llegó sin ellas; la dirección y
las coordenadas del cliente nunca se tocan. Los pedidos que no están en la
solicitud no se modifican. Los de "sin_resolver" guardan el motivo en
motivo_revision_direccion; los que entran a la ruta lo dejan en null.

Peso: antes de llamar a n8n se valida (422, sin escribir nada) que todos los
pedidos tengan peso calculable (con ítems y todos sus productos con peso_kg
no nulo ni negativo; 0 es válido, como en los dispensadores), que ninguno supere la capacidad de un viaje y que el total quepa en
RUTA_MAX_VIAJES viajes.

GET /rutas/pedidos-pendientes lista los "pendiente" y además los
"confirmado" con orden_entrega null o motivo_revision_direccion no null,
para que un sin_resolver con coordenadas corregidas se pueda replanificar.

Vista previa (historia #87): POST /rutas/planificar con "vista_previa": true
hace la validación y la llamada a n8n pero no escribe nada; devuelve la ruta
propuesta con la "version" (actualizado_en) de cada pedido. La ejecutiva la
reordena en el panel y la guarda con POST /rutas/confirmar, que aplica la
misma persistencia en el orden recibido (cada parada trae su viaje; si no lo
trae es el 1) y rechaza (409) los pedidos que cambiaron desde la vista previa.
Los pesos se vuelven a validar con las mismas reglas (422).

GET /rutas/actual devuelve la ruta guardada (los "confirmado" y "en_despacho"
con orden_entrega, por viaje y orden) para que el panel la vuelva a ver tras
recargar. Sin id de ruta ni fecha de entrega en pedido, no distingue rutas:
avisa los (viaje, orden_entrega) repetidos (ver ruta_actual).
"""

import asyncio
import logging
import math
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.auditoria import Auditoria
from app.models.cliente import Cliente
from app.models.detalle_pedido import DetallePedido
from app.models.enums import EstadoPedido
from app.models.pedido import Pedido
from app.models.producto import Producto
from app.services.auditoria_service import construir_snapshot
from app.services.coordenadas_cliente import copiar_coordenadas_al_cliente

logger = logging.getLogger(__name__)

# Header del secreto compartido con n8n (mismo patrón que X-Cron-Secret).
HEADER_SECRETO_N8N = "X-Route-Secret"

# Estados desde los que se puede (re)generar una ruta.
ESTADOS_PLANIFICABLES = (EstadoPedido.PENDIENTE, EstadoPedido.CONFIRMADO)

# Acción registrada en auditoria por cada pedido de la ruta.
ACCION_AUDITORIA = "planificar_ruta"

# Evita dos planificaciones simultáneas (dos clics seguidos no deben llamar
# dos veces a n8n). Como webhook_queue y draft_store, vale para un solo
# proceso: el Dockerfile levanta uvicorn sin --workers. Si se escala a varios
# procesos, debe pasar a un lock compartido (ej. advisory lock de Postgres).
_planificacion_en_curso = asyncio.Lock()


def _cliente_http() -> httpx.AsyncClient:
    """Cliente HTTP para llamar a n8n (se reemplaza en las pruebas)."""
    return httpx.AsyncClient(timeout=settings.N8N_ROUTE_TIMEOUT_SECONDS)


def _nombre_cliente(cliente: Cliente | None) -> str | None:
    if cliente is None:
        return None
    apellidos = " ".join(p for p in (cliente.apellido_paterno, cliente.apellido_materno) if p)
    return f"{cliente.nombre} {apellidos}" if apellidos else cliente.nombre


def _direccion(pedido: Pedido) -> str | None:
    return pedido.direccion_despacho or (pedido.cliente.direccion if pedido.cliente else None)


def _coordenada(valor) -> float | None:
    return float(valor) if valor is not None else None


def _tiene_coordenadas(pedido: Pedido) -> bool:
    """Un par a medias (solo latitud o solo longitud) cuenta como sin
    coordenadas: se geocodifica y se sobrescribe el par completo."""
    return pedido.latitud is not None and pedido.longitud is not None


def _como_utc(valor: datetime) -> datetime:
    """creado_en se guarda sin zona (timestamp de Postgres con now() del
    servidor, que corre en UTC): se marca como UTC para devolver ISO 8601
    con zona."""
    return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


def _redondear_kg(total) -> int:
    """Peso en kg entero: se redondea hacia arriba UNA sola vez, sobre la suma
    del pedido (no por línea), con mínimo 1."""
    return max(1, math.ceil(total))


async def calcular_pesos(session, pedido_ids: list[int]) -> tuple[dict[int, int], dict[int, list[str]]]:
    """Peso de cada pedido en una sola consulta: SUM(cantidad_solicitada *
    producto.peso_kg) por pedido. Devuelve (pesos, productos_sin_peso):
    pesos solo trae los pedidos con peso calculable; un pedido sin ítems, o con
    algún producto con peso_kg NULL o negativo, no está en pesos (y, si tiene
    productos sin peso, productos_sin_peso trae sus nombres). peso_kg = 0 es
    válido (aporta 0 kg); el mínimo de 1 kg se aplica al total del pedido."""
    if not pedido_ids:
        return {}, {}
    producto_sin_peso = or_(Producto.peso_kg.is_(None), Producto.peso_kg < 0)
    result = await session.execute(
        select(
            DetallePedido.pedido_id,
            func.sum(DetallePedido.cantidad_solicitada * Producto.peso_kg),
            func.array_agg(Producto.nombre.distinct()).filter(producto_sin_peso),
        )
        .join(Producto, Producto.id == DetallePedido.producto_id)
        .where(DetallePedido.pedido_id.in_(pedido_ids))
        .group_by(DetallePedido.pedido_id)
    )
    pesos: dict[int, int] = {}
    sin_peso: dict[int, list[str]] = {}
    for pedido_id, total, nombres in result:
        if nombres:
            sin_peso[pedido_id] = sorted(nombres)
        else:
            pesos[pedido_id] = _redondear_kg(total)
    return pesos, sin_peso


def _verificar_pesos(pedido_ids: list[int], pesos: dict[int, int], sin_peso: dict[int, list[str]]) -> None:
    """422 (antes de llamar a n8n) si algún pedido no tiene peso calculable, si
    alguno solo ya supera la capacidad de un viaje o si el total no cabe en
    RUTA_MAX_VIAJES viajes."""
    sin_calculo = [pid for pid in pedido_ids if pid not in pesos]
    if sin_calculo:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No se puede calcular el peso de algunos pedidos (sin ítems o con productos sin peso). Corrígelos o sácalos de la ruta.",
            sin_peso=sin_calculo,
            productos_sin_peso=[
                {"pedido_id": pid, "productos": sin_peso[pid]} for pid in sin_calculo if pid in sin_peso
            ],
        )
    capacidad = settings.RUTA_CAPACIDAD_KG
    sobre_capacidad = [{"pedido_id": pid, "peso_kg": pesos[pid]} for pid in pedido_ids if pesos[pid] > capacidad]
    if sobre_capacidad:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Hay pedidos que pesan más que la capacidad de un viaje ({capacidad} kg).",
            capacidad_kg=capacidad,
            sobre_capacidad=sobre_capacidad,
        )
    total = sum(pesos[pid] for pid in pedido_ids)
    maximo = capacidad * settings.RUTA_MAX_VIAJES
    if total > maximo:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"El peso total ({total} kg) supera el máximo de {maximo} kg ({settings.RUTA_MAX_VIAJES} viajes de {capacidad} kg). Saca pedidos de la ruta.",
            peso_total_kg=total,
            maximo_kg=maximo,
        )


def _termina_en(viaje: int, ultimo: int, peso_kg: int | None) -> str:
    """Dónde termina un viaje: "casa" solo el último, si hay al menos 2 viajes
    y su peso es ESTRICTAMENTE menor al umbral; en cualquier otro caso (o si
    el peso no se conoce) "planta"."""
    if viaje == ultimo and ultimo >= 2 and peso_kg is not None and peso_kg < settings.RUTA_UMBRAL_CASA_KG:
        return "casa"
    return "planta"


def _resumir_viajes(por_viaje: dict[int, list[int]], pesos: dict[int, int]) -> list[dict]:
    """Resumen por viaje con el peso recalculado con los pesos del backend
    (None si algún pedido del viaje no tiene peso calculable) y termina_en
    derivado con _termina_en."""
    ultimo = max(por_viaje, default=0)
    resumen = []
    for viaje in sorted(por_viaje):
        ids = por_viaje[viaje]
        peso = sum(pesos[pid] for pid in ids) if all(pid in pesos for pid in ids) else None
        resumen.append(
            {
                "viaje": viaje,
                "peso_kg": peso,
                "termina_en": _termina_en(viaje, ultimo, peso),
                "total_paradas": len(ids),
            }
        )
    return resumen


def _problemas_viajes(por_viaje: dict[int, list[int]], pesos: dict[int, int]) -> list[str]:
    """Reglas de reparto en viajes que valen para cualquier origen (n8n o el
    panel): numeración consecutiva 1..V con V <= RUTA_MAX_VIAJES, peso de cada
    viaje <= capacidad y el último viaje es el más liviano."""
    problemas: list[str] = []
    numeros = sorted(por_viaje)
    total = len(numeros)
    if numeros != list(range(1, total + 1)):
        problemas.append(f"los viajes no son consecutivos 1..{total}: {numeros}")
    if total > settings.RUTA_MAX_VIAJES:
        problemas.append(f"hay {total} viajes y el máximo es {settings.RUTA_MAX_VIAJES}")
    peso_de = {n: sum(pesos.get(pid, 0) for pid in ids) for n, ids in por_viaje.items()}
    for n in numeros:
        if peso_de[n] > settings.RUTA_CAPACIDAD_KG:
            problemas.append(
                f"viaje {n}: pesa {peso_de[n]} kg y la capacidad es {settings.RUTA_CAPACIDAD_KG} kg"
            )
    if total >= 2 and numeros == list(range(1, total + 1)):
        if any(peso_de[n] < peso_de[total] for n in numeros[:-1]):
            problemas.append(f"el último viaje ({total}) debe ser el más liviano")
    return problemas


async def listar_pendientes() -> list[dict]:
    """Pedidos que se pueden incluir en la próxima ruta: los "pendiente" y
    los "confirmado" que quedaron fuera de una ruta anterior (sin_resolver:
    orden_entrega null) o que siguen con la dirección por revisar. Así, tras
    corregir sus coordenadas, vuelven a aparecer y se pueden replanificar.
    "peso_kg" es el peso del pedido (ver calcular_pesos), None si no se puede
    calcular, para que el panel sume lo seleccionado."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido)
            .where(
                or_(
                    Pedido.estado == EstadoPedido.PENDIENTE,
                    and_(
                        Pedido.estado == EstadoPedido.CONFIRMADO,
                        or_(
                            Pedido.orden_entrega.is_(None),
                            Pedido.motivo_revision_direccion.is_not(None),
                        ),
                    ),
                )
            )
            .options(selectinload(Pedido.cliente))
            .order_by(Pedido.creado_en)
        )
        pedidos = result.scalars().all()
        pesos, _ = await calcular_pesos(session, [pedido.id for pedido in pedidos])
    return [
        {
            "pedido_id": pedido.id,
            "cliente_id": pedido.cliente_id,
            "cliente_nombre": _nombre_cliente(pedido.cliente),
            "cliente_telefono": pedido.cliente.telefono if pedido.cliente else None,
            "direccion_texto": _direccion(pedido),
            "latitud": _coordenada(pedido.latitud),
            "longitud": _coordenada(pedido.longitud),
            "creado_en": _como_utc(pedido.creado_en),
            "estado": pedido.estado.value,
            "motivo_revision_direccion": pedido.motivo_revision_direccion,
            "peso_kg": pesos.get(pedido.id),
        }
        for pedido in pedidos
    ]


async def ruta_actual() -> dict:
    """Ruta guardada: los "confirmado" y "en_despacho" con orden_entrega, por
    viaje y posición dentro del viaje (los sin viaje, de antes de existir los
    viajes, al final).

    No existe un id de ruta ni una fecha de entrega: cada planificación
    numera desde 1 y no toca los confirmados que quedan fuera de la
    solicitud, así que pueden convivir pedidos de rutas distintas. Esos
    (viaje, orden_entrega) repetidos se informan en "advertencias" en vez de
    ocultarse. A igual viaje y orden va primero el planificado o editado más
    reciente (actualizado_en desc) y luego el id. Los "entregado" y
    "cancelado" quedan fuera por el filtro de estado.

    "viajes" resume cada viaje con su peso (recalculado con los pesos actuales
    de los productos; None si algún pedido no tiene peso calculable) y
    termina_en. termina_en NO se guarda: se deriva con la misma regla que al
    planificar (el último viaje, si hay al menos 2 y su peso es menor al
    umbral, termina en "casa"; si no, en "planta")."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido)
            .where(
                Pedido.estado.in_((EstadoPedido.CONFIRMADO, EstadoPedido.EN_DESPACHO)),
                Pedido.orden_entrega.is_not(None),
            )
            .options(selectinload(Pedido.cliente))
            .order_by(Pedido.viaje.asc().nulls_last(), Pedido.orden_entrega, Pedido.actualizado_en.desc(), Pedido.id)
        )
        pedidos = result.scalars().all()
        pesos, _ = await calcular_pesos(session, [pedido.id for pedido in pedidos])

    por_posicion: dict[tuple[int | None, int], list[int]] = {}
    por_viaje: dict[int, list[int]] = {}
    for pedido in pedidos:
        por_posicion.setdefault((pedido.viaje, pedido.orden_entrega), []).append(pedido.id)
        if pedido.viaje is not None:
            por_viaje.setdefault(pedido.viaje, []).append(pedido.id)
    advertencias = [
        {
            "tipo": "orden_entrega_duplicado",
            "mensaje": (
                f"Hay {len(ids)} pedidos con la parada {orden}"
                + (f" en el viaje {viaje}" if viaje is not None else "")
                + ": la ruta mezcla más de una planificación."
            ),
            "viaje": viaje,
            "orden_entrega": orden,
            "pedido_ids": ids,
        }
        for (viaje, orden), ids in por_posicion.items()
        if len(ids) > 1
    ]
    return {
        "ruta": [
            {
                "pedido_id": pedido.id,
                "viaje": pedido.viaje,
                "orden_entrega": pedido.orden_entrega,
                "cliente_id": pedido.cliente_id,
                "cliente_nombre": _nombre_cliente(pedido.cliente),
                "cliente_telefono": pedido.cliente.telefono if pedido.cliente else None,
                "direccion_texto": _direccion(pedido),
                "latitud": _coordenada(pedido.latitud),
                "longitud": _coordenada(pedido.longitud),
                "estado": pedido.estado.value,
            }
            for pedido in pedidos
        ],
        "viajes": _resumir_viajes(por_viaje, pesos),
        "total": len(pedidos),
        "advertencias": advertencias,
    }


def _error(estado_http: int, mensaje: str, **extra) -> HTTPException:
    return HTTPException(status_code=estado_http, detail={"mensaje": mensaje, **extra})


async def _cargar_pedidos(session, pedido_ids: list[int], bloquear: bool = False) -> dict[int, Pedido]:
    query = select(Pedido).where(Pedido.id.in_(pedido_ids)).options(selectinload(Pedido.cliente))
    if bloquear:
        query = query.with_for_update(of=Pedido)
    result = await session.execute(query)
    return {pedido.id: pedido for pedido in result.scalars().all()}


def _verificar_planificables(pedido_ids: list[int], pedidos: dict[int, Pedido]) -> None:
    """409 con los IDs problemáticos si alguno no existe o no está pendiente
    ni confirmado. Nunca se excluyen en silencio: el panel debe refrescar."""
    inexistentes = [pid for pid in pedido_ids if pid not in pedidos]
    estado_invalido = [
        {"pedido_id": pid, "estado": pedidos[pid].estado.value}
        for pid in pedido_ids
        if pid in pedidos and pedidos[pid].estado not in ESTADOS_PLANIFICABLES
    ]
    if inexistentes or estado_invalido:
        raise _error(
            status.HTTP_409_CONFLICT,
            "Algunos pedidos ya no se pueden incluir en la ruta. Actualiza la lista.",
            pedido_ids=inexistentes + [p["pedido_id"] for p in estado_invalido],
            inexistentes=inexistentes,
            estado_invalido=estado_invalido,
        )


async def _llamar_n8n(paradas: list[dict], reglas: dict) -> dict:
    """POST síncrono al webhook de n8n. Cualquier falla (timeout, error HTTP,
    respuesta que no es JSON) es un error claro para el panel; no se escribe
    nada en la base."""
    headers = {HEADER_SECRETO_N8N: settings.N8N_ROUTE_WEBHOOK_SECRET}
    try:
        async with _cliente_http() as cliente:
            respuesta = await cliente.post(
                settings.N8N_ROUTE_WEBHOOK_URL, json={"paradas": paradas, "reglas": reglas}, headers=headers
            )
    except httpx.TimeoutException:
        logger.warning("[rutas] n8n no respondió en %ss", settings.N8N_ROUTE_TIMEOUT_SECONDS)
        raise _error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "El servicio de rutas no respondió a tiempo. Los pedidos siguen pendientes; intenta de nuevo.",
        )
    except httpx.HTTPError as exc:
        logger.warning("[rutas] Falló la llamada a n8n: %r", exc)
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "No se pudo contactar al servicio de rutas. Los pedidos siguen pendientes; intenta de nuevo.",
        )
    if respuesta.status_code != 200:
        logger.warning("[rutas] n8n respondió %s: %s", respuesta.status_code, respuesta.text[:500])
        codigo_n8n = _codigo_error_n8n(respuesta)
        if respuesta.status_code in (401, 403) or codigo_n8n is not None:
            # El "detalle" de n8n solo va al log, nunca al panel.
            raise _error(
                status.HTTP_502_BAD_GATEWAY,
                "Servicio de rutas mal configurado o rechazó la solicitud",
                codigo=codigo_n8n or respuesta.status_code,
            )
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            f"El servicio de rutas respondió con error ({respuesta.status_code}). Los pedidos siguen pendientes.",
        )
    try:
        return respuesta.json()
    except ValueError:
        raise _error(status.HTTP_502_BAD_GATEWAY, "El servicio de rutas devolvió una respuesta que no es JSON.")


def _codigo_error_n8n(respuesta: httpx.Response) -> str | None:
    """Valor de "error" si n8n respondió un error 4xx/5xx con cuerpo
    {"error": "...", "detalle": "..."}; None si no trae un cuerpo útil."""
    if respuesta.status_code < 400:
        return None
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    return error.strip() if isinstance(error, str) and error.strip() else None


def _es_entero(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _es_numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def _validar_respuesta(respuesta, enviados: list[int], pesos: dict[int, int]) -> tuple[list[dict], list[dict], list[dict]]:
    """Valida la respuesta de n8n contra el contrato (ver docstring del
    módulo). Devuelve (ruta, sin_resolver, viajes) o lanza 502 con los
    problemas. "viajes" es el resumen recalculado con los pesos del backend."""
    problemas: list[str] = []
    if not isinstance(respuesta, dict) or not isinstance(respuesta.get("ruta"), list):
        raise _error(status.HTTP_502_BAD_GATEWAY, "Respuesta inválida del servicio de rutas.", problemas=["falta la lista 'ruta'"])
    if not isinstance(respuesta.get("viajes"), list):
        raise _error(status.HTTP_502_BAD_GATEWAY, "Respuesta inválida del servicio de rutas.", problemas=["falta la lista 'viajes'"])
    ruta = respuesta["ruta"]
    sin_resolver = respuesta.get("sin_resolver", [])
    if not isinstance(sin_resolver, list):
        problemas.append("'sin_resolver' no es una lista")
        sin_resolver = []

    # Viajes declarados por n8n: número -> item (sus paradas se agregan abajo).
    declarados: dict[int, dict] = {}
    for item in respuesta["viajes"]:
        numero = item.get("viaje") if isinstance(item, dict) else None
        if not _es_entero(numero) or numero < 1:
            problemas.append(f"viaje sin número entero positivo: {item!r}")
        elif numero in declarados:
            problemas.append(f"viaje {numero} repetido en 'viajes'")
        else:
            declarados[numero] = item
    por_viaje: dict[int, list[int]] = {numero: [] for numero in declarados}
    ordenes_por_viaje: dict[int, list[int]] = {numero: [] for numero in declarados}

    vistos: list[int] = []
    for item in ruta:
        if not isinstance(item, dict) or not _es_entero(item.get("pedido_id")):
            problemas.append(f"parada de ruta sin pedido_id entero: {item!r}")
            continue
        pid = item["pedido_id"]
        vistos.append(pid)
        viaje = item.get("viaje")
        orden = item.get("orden_entrega")
        orden_valido = _es_entero(orden) and orden >= 1
        if not orden_valido:
            problemas.append(f"pedido {pid}: orden_entrega no es un entero positivo ({orden!r})")
        if not _es_entero(viaje) or viaje not in declarados:
            problemas.append(f"pedido {pid}: viaje {viaje!r} no existe en 'viajes'")
        else:
            por_viaje[viaje].append(pid)
            # None marca un orden inválido: cuenta como parada pero no se compara.
            ordenes_por_viaje[viaje].append(orden if orden_valido else None)
        latitud, longitud = item.get("latitud"), item.get("longitud")
        if not _es_numero(latitud) or not -90 <= latitud <= 90:
            problemas.append(f"pedido {pid}: latitud fuera de rango ({latitud!r})")
        if not _es_numero(longitud) or not -180 <= longitud <= 180:
            problemas.append(f"pedido {pid}: longitud fuera de rango ({longitud!r})")
    for item in sin_resolver:
        if not isinstance(item, dict) or not _es_entero(item.get("pedido_id")):
            problemas.append(f"elemento de sin_resolver sin pedido_id entero: {item!r}")
            continue
        vistos.append(item["pedido_id"])
        if not isinstance(item.get("motivo"), str) or not item["motivo"].strip():
            problemas.append(f"pedido {item['pedido_id']}: sin_resolver sin motivo")

    ajenos = sorted({pid for pid in vistos if pid not in enviados})
    repetidos = sorted({pid for pid in vistos if vistos.count(pid) > 1})
    faltantes = sorted(set(enviados) - set(vistos))
    if ajenos:
        problemas.append(f"pedidos que no se enviaron: {ajenos}")
    if repetidos:
        problemas.append(f"pedidos repetidos (o en ruta y sin_resolver a la vez): {repetidos}")
    if faltantes:
        problemas.append(f"pedidos enviados que no vinieron en la respuesta: {faltantes}")

    # orden_entrega consecutivo 1..N dentro de cada viaje.
    for numero in sorted(declarados):
        ordenes = ordenes_por_viaje[numero]
        if not ordenes:
            problemas.append(f"viaje {numero} sin paradas")
            continue
        validos = [o for o in ordenes if o is not None]
        repetidos_orden = sorted({o for o in validos if validos.count(o) > 1})
        if repetidos_orden:
            problemas.append(f"viaje {numero}: orden_entrega repetidos: {repetidos_orden}")
        elif len(validos) == len(ordenes) and sorted(validos) != list(range(1, len(validos) + 1)):
            problemas.append(
                f"viaje {numero}: orden_entrega no es consecutivo 1..{len(validos)}: {sorted(validos)}"
            )

    # Numeración, capacidad y último viaje más liviano (pesos del backend).
    problemas.extend(_problemas_viajes(por_viaje, pesos))
    resumen = _resumir_viajes(por_viaje, pesos)
    ultimo = len(declarados)
    for fila in resumen:
        numero, peso = fila["viaje"], fila["peso_kg"]
        informado = declarados[numero].get("peso_kg")
        if not _es_numero(informado) or informado != peso:
            problemas.append(f"viaje {numero}: n8n informa peso_kg {informado!r} y el backend calcula {peso}")
        termina = declarados[numero].get("termina_en")
        if termina not in ("planta", "casa"):
            problemas.append(f"viaje {numero}: termina_en inválido ({termina!r})")
        elif termina == "casa" and numero != ultimo:
            problemas.append(f"viaje {numero}: solo el último viaje puede terminar en casa")
        elif termina == "casa" and ultimo < 2:
            problemas.append(f"viaje {numero}: un único viaje siempre termina en planta")
        elif termina == "casa" and peso is not None and peso >= settings.RUTA_UMBRAL_CASA_KG:
            problemas.append(
                f"viaje {numero}: termina en casa con {peso} kg y el umbral es {settings.RUTA_UMBRAL_CASA_KG} kg"
            )
        elif termina != fila["termina_en"]:
            problemas.append(
                f"viaje {numero}: pesa {peso} kg (menos que el umbral de {settings.RUTA_UMBRAL_CASA_KG} kg) y debe terminar en casa"
            )
    if problemas:
        logger.warning("[rutas] Respuesta de n8n inválida: %s", problemas)
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "El servicio de rutas devolvió una ruta inválida. No se guardó nada; los pedidos siguen pendientes.",
            problemas=problemas,
        )
    return ruta, sin_resolver, resumen


def _verificar_lista(pedido_ids: list[int]) -> None:
    if len(pedido_ids) > settings.RUTA_MAX_PEDIDOS:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Máximo {settings.RUTA_MAX_PEDIDOS} pedidos por planificación",
            maximo=settings.RUTA_MAX_PEDIDOS,
            recibidos=len(pedido_ids),
        )
    repetidos = sorted({pid for pid in pedido_ids if pedido_ids.count(pid) > 1})
    if repetidos:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "La lista de pedidos tiene IDs repetidos.", pedido_ids=repetidos
        )


async def planificar_ruta(pedido_ids: list[int], usuario: str | None, vista_previa: bool = False) -> dict:
    """Con vista_previa=True (historia #87) solo calcula la ruta con n8n y la
    devuelve, sin escribir nada: la ejecutiva la revisa, la reordena y la
    guarda con confirmar_ruta."""
    _verificar_lista(pedido_ids)
    if not settings.N8N_ROUTE_WEBHOOK_URL:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "El servicio de rutas no está configurado (falta N8N_ROUTE_WEBHOOK_URL).",
        )
    if not settings.N8N_ROUTE_WEBHOOK_SECRET:
        # Sin secreto n8n rechaza la llamada (403): no tiene sentido hacerla.
        logger.warning("[rutas] N8N_ROUTE_WEBHOOK_SECRET vacío: no se llama a n8n")
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de rutas no configurado")
    # Chequeo y toma del lock sin awaits entremedio: dos solicitudes
    # simultáneas no pueden pasar ambas.
    if _planificacion_en_curso.locked():
        raise _error(status.HTTP_409_CONFLICT, "Ya hay una planificación de ruta en curso. Espera a que termine.")
    async with _planificacion_en_curso:
        return await _planificar(pedido_ids, usuario, vista_previa)


async def _planificar(pedido_ids: list[int], usuario: str | None, vista_previa: bool = False) -> dict:
    # 1. Paradas armadas solo desde la BD (del panel solo se usan los IDs).
    async with SessionLocal() as session:
        pedidos = await _cargar_pedidos(session, pedido_ids)
        pesos, sin_peso = await calcular_pesos(session, pedido_ids)
    _verificar_planificables(pedido_ids, pedidos)
    _verificar_pesos(pedido_ids, pesos, sin_peso)
    paradas = [
        {
            "pedido_id": pid,
            "direccion_texto": _direccion(pedidos[pid]),
            "latitud": _coordenada(pedidos[pid].latitud) if _tiene_coordenadas(pedidos[pid]) else None,
            "longitud": _coordenada(pedidos[pid].longitud) if _tiene_coordenadas(pedidos[pid]) else None,
            "peso_kg": pesos[pid],
        }
        for pid in pedido_ids
    ]
    reglas = {
        "capacidad_kg": settings.RUTA_CAPACIDAD_KG,
        "umbral_casa_kg": settings.RUTA_UMBRAL_CASA_KG,
        "max_viajes": settings.RUTA_MAX_VIAJES,
    }
    # Para detectar cambios hechos mientras se espera a n8n.
    leido_en = {pid: pedidos[pid].actualizado_en for pid in pedido_ids}

    # 2. n8n (sin ninguna transacción abierta mientras se espera).
    respuesta = await _llamar_n8n(paradas, reglas)
    ruta, sin_resolver, viajes = _validar_respuesta(respuesta, pedido_ids, pesos)
    motivos = {item["pedido_id"]: item["motivo"] for item in sin_resolver}
    ruta_por_id = {item["pedido_id"]: item for item in ruta}

    if vista_previa:
        logger.info(
            "[rutas] Vista previa para %s: %s en ruta, %s sin resolver", usuario, len(ruta), len(sin_resolver)
        )
        return _vista_previa(pedido_ids, pedidos, ruta_por_id, motivos, viajes)

    # 3. Persistencia en una sola transacción.
    pedidos, fuera_de_solicitud = await _guardar_ruta(
        pedido_ids, ruta_por_id, motivos, usuario, leido_en,
        "El pedido {pid} cambió durante la planificación, reintenta",
    )
    logger.info(
        "[rutas] Ruta generada por %s: %s en ruta, %s sin resolver", usuario, len(ruta), len(sin_resolver)
    )
    return _resultado(pedido_ids, pedidos, ruta_por_id, motivos, fuera_de_solicitud, viajes)


def _vista_previa(pedido_ids, pedidos, ruta_por_id, motivos, viajes) -> dict:
    """Ruta propuesta sin guardar. "version" (actualizado_en del pedido) se
    devuelve tal cual en POST /rutas/confirmar para detectar pedidos que
    cambiaron mientras la ejecutiva revisaba la ruta. Las coordenadas son las
    de n8n, para no volver a geocodificar al confirmar."""
    return {
        "vista_previa": True,
        "ruta": [
            {
                "pedido_id": pid,
                "viaje": ruta_por_id[pid]["viaje"],
                "orden_entrega": ruta_por_id[pid]["orden_entrega"],
                "cliente_nombre": _nombre_cliente(pedidos[pid].cliente),
                "direccion_texto": _direccion(pedidos[pid]),
                "latitud": ruta_por_id[pid]["latitud"],
                "longitud": ruta_por_id[pid]["longitud"],
                "version": pedidos[pid].actualizado_en.isoformat(),
            }
            for pid in sorted(ruta_por_id, key=_posicion(ruta_por_id))
        ],
        "viajes": viajes,
        "sin_resolver": [
            {
                "pedido_id": pid,
                "motivo": motivos[pid],
                "cliente_nombre": _nombre_cliente(pedidos[pid].cliente),
                "direccion_texto": _direccion(pedidos[pid]),
                "version": pedidos[pid].actualizado_en.isoformat(),
            }
            for pid in pedido_ids
            if pid in motivos
        ],
    }


async def confirmar_ruta(paradas: list[dict], sin_resolver: list[dict], usuario: str | None) -> dict:
    """Historia #87: guarda la ruta revisada en el panel, en el orden recibido
    (1 = primera parada), con las mismas reglas que planificar: todos quedan
    "confirmado", los de sin_resolver con su motivo y sin orden. Los pedidos
    que la ejecutiva sacó de la ruta no se envían y no se tocan. Si un pedido
    cambió desde la vista previa (version distinta) no se guarda nada (409)."""
    pedido_ids = [p["pedido_id"] for p in paradas] + [s["pedido_id"] for s in sin_resolver]
    if not pedido_ids:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "No hay pedidos para confirmar.")
    _verificar_lista(pedido_ids)
    # Cada parada trae su viaje (1 si no lo trae); la posición es el orden en
    # que llegan dentro de su viaje.
    ruta_por_id = {}
    por_viaje: dict[int, list[int]] = {}
    for p in paradas:
        viaje = p.get("viaje") or 1
        por_viaje.setdefault(viaje, []).append(p["pedido_id"])
        ruta_por_id[p["pedido_id"]] = {
            "viaje": viaje,
            "orden_entrega": len(por_viaje[viaje]),
            "latitud": p["latitud"],
            "longitud": p["longitud"],
        }
    async with SessionLocal() as session:
        pesos, sin_peso = await calcular_pesos(session, pedido_ids)
    _verificar_pesos(pedido_ids, pesos, sin_peso)
    problemas = _problemas_viajes(por_viaje, pesos)
    if problemas:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Los viajes de la ruta no cumplen las reglas de peso. No se guardó nada.",
            problemas=problemas,
        )
    viajes = _resumir_viajes(por_viaje, pesos)
    motivos = {s["pedido_id"]: s["motivo"] for s in sin_resolver}
    versiones = {item["pedido_id"]: item["version"] for item in [*paradas, *sin_resolver]}
    if _planificacion_en_curso.locked():
        raise _error(status.HTTP_409_CONFLICT, "Ya hay una planificación de ruta en curso. Espera a que termine.")
    async with _planificacion_en_curso:
        pedidos, fuera_de_solicitud = await _guardar_ruta(
            pedido_ids, ruta_por_id, motivos, usuario, versiones,
            "El pedido {pid} cambió desde que se calculó la ruta. Vuelve a calcularla.",
        )
    logger.info(
        "[rutas] Ruta confirmada por %s desde la vista previa: %s en ruta, %s sin resolver",
        usuario, len(paradas), len(sin_resolver),
    )
    return _resultado(pedido_ids, pedidos, ruta_por_id, motivos, fuera_de_solicitud, viajes)


async def _guardar_ruta(pedido_ids, ruta_por_id, motivos, usuario, versiones, mensaje_cambio):
    """Persistencia en una sola transacción (ver docstring del módulo).
    versiones: actualizado_en esperado de cada pedido; si alguno cambió no se
    escribe nada. Devuelve (pedidos, confirmados con orden fuera de la solicitud)."""
    ahora = datetime.utcnow()
    async with SessionLocal() as session:
        async with session.begin():
            pedidos = await _cargar_pedidos(session, pedido_ids, bloquear=True)
            # Pudieron cambiar mientras se esperaba (ej. un cancelado, o una
            # dirección o coordenadas corregidas): no se escribe nada.
            _verificar_planificables(pedido_ids, pedidos)
            for pid in pedido_ids:
                if pedidos[pid].actualizado_en != versiones[pid]:
                    raise _error(status.HTTP_409_CONFLICT, mensaje_cambio.format(pid=pid))
            for pid in pedido_ids:
                pedido = pedidos[pid]
                antes = construir_snapshot(pedido)
                cliente_antes = construir_snapshot(pedido.cliente) if pedido.cliente else None
                pedido.estado = EstadoPedido.CONFIRMADO
                parada = ruta_por_id.get(pid)
                pedido.orden_entrega = parada["orden_entrega"] if parada else None
                pedido.viaje = parada["viaje"] if parada else None

                # Historia #107: si n8n no pudo ubicar la dirección, queda marcado
                # con el motivo para revisión manual. Si entró a la ruta, se
                # limpia una marca anterior.
                pedido.motivo_revision_direccion = motivos.get(pid)
                if parada and not _tiene_coordenadas(pedido):
                    # Solo se completan las coordenadas del pedido que llegó
                    # sin ellas (o con el par a medias, que se sobrescribe
                    # entero).
                    pedido.latitud = parada["latitud"]
                    pedido.longitud = parada["longitud"]

                # Historia #159: si el pedido entró a la ruta y va a la
                # dirección del cliente, sus coordenadas completan al cliente
                # que no tenga. No reemplazan las que ya tenga, que pueden
                # venir de una corrección manual.
                cliente_actualizado = bool(parada) and copiar_coordenadas_al_cliente(
                    pedido, sobrescribir=False
                )

                pedido.actualizado_en = ahora
                await session.flush()
                session.add(
                    Auditoria(
                        usuario=usuario or "desconocido",
                        entidad="pedido",
                        entidad_id=pid,
                        accion=ACCION_AUDITORIA,
                        antes=antes,
                        despues=construir_snapshot(pedido),
                    )
                )
                if cliente_actualizado:
                    session.add(
                        Auditoria(
                            usuario=usuario or "desconocido",
                            entidad="cliente",
                            entidad_id=pedido.cliente.id,
                            accion=ACCION_AUDITORIA,
                            antes=cliente_antes,
                            despues=construir_snapshot(pedido.cliente),
                        )
                    )
            # Rutas anteriores que pueden haber quedado viejas: se informan,
            # no se tocan.
            result = await session.execute(
                select(Pedido.id, Pedido.viaje, Pedido.orden_entrega)
                .where(
                    Pedido.estado == EstadoPedido.CONFIRMADO,
                    Pedido.orden_entrega.is_not(None),
                    Pedido.id.not_in(pedido_ids),
                )
                .order_by(Pedido.id)
            )
            fuera_de_solicitud = [
                {"pedido_id": fila.id, "viaje": fila.viaje, "orden_entrega": fila.orden_entrega} for fila in result
            ]
    return pedidos, fuera_de_solicitud


def _posicion(ruta_por_id):
    """Clave de orden de las paradas: viaje y luego posición dentro del viaje."""
    return lambda pid: (ruta_por_id[pid]["viaje"], ruta_por_id[pid]["orden_entrega"])


def _resultado(pedido_ids, pedidos, ruta_por_id, motivos, fuera_de_solicitud, viajes) -> dict:
    return {
        "ruta": [
            {
                "pedido_id": pid,
                "viaje": ruta_por_id[pid]["viaje"],
                "orden_entrega": ruta_por_id[pid]["orden_entrega"],
                "cliente_nombre": _nombre_cliente(pedidos[pid].cliente),
                "direccion_texto": _direccion(pedidos[pid]),
                "latitud": _coordenada(pedidos[pid].latitud),
                "longitud": _coordenada(pedidos[pid].longitud),
            }
            for pid in sorted(ruta_por_id, key=_posicion(ruta_por_id))
        ],
        "viajes": viajes,
        "sin_resolver": [
            {
                "pedido_id": pid,
                "motivo": motivos[pid],
                "cliente_nombre": _nombre_cliente(pedidos[pid].cliente),
                "direccion_texto": _direccion(pedidos[pid]),
            }
            for pid in pedido_ids
            if pid in motivos
        ],
        "pedidos_confirmados": len(pedido_ids),
        "confirmados_con_orden_fuera_de_solicitud": fuera_de_solicitud,
    }
