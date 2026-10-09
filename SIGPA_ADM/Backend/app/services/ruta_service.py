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
                  "latitud": float | null, "longitud": float | null}]}

Respuesta esperada (200, JSON):

    {"ruta": [{"pedido_id": int, "orden_entrega": int,
               "latitud": float, "longitud": float}],
     "sin_resolver": [{"pedido_id": int, "motivo": str}]}

Cada pedido enviado debe aparecer exactamente una vez, en "ruta" (con su
posición, 1 = primera parada) o en "sin_resolver" (no se pudo geocodificar o
ubicar). Si la respuesta no cumple el contrato no se escribe nada.

Persistencia (una sola transacción, solo si todo es válido): todos los
pedidos de la solicitud pasan a "confirmado"; los de "ruta" reciben su
orden_entrega y los de "sin_resolver" quedan con orden_entrega null; las
coordenadas se guardan en el pedido solo si llegó sin ellas; la dirección y
las coordenadas del cliente nunca se tocan. Los pedidos que no están en la
solicitud no se modifican. Los de "sin_resolver" guardan el motivo en
motivo_revision_direccion; los que entran a la ruta lo dejan en null.

GET /rutas/pedidos-pendientes lista los "pendiente" y además los
"confirmado" con orden_entrega null o motivo_revision_direccion no null,
para que un sin_resolver con coordenadas corregidas se pueda replanificar.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.auditoria import Auditoria
from app.models.cliente import Cliente
from app.models.enums import EstadoPedido
from app.models.pedido import Pedido
from app.services.auditoria_service import construir_snapshot

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


async def listar_pendientes() -> list[dict]:
    """Pedidos que se pueden incluir en la próxima ruta: los "pendiente" y
    los "confirmado" que quedaron fuera de una ruta anterior (sin_resolver:
    orden_entrega null) o que siguen con la dirección por revisar. Así, tras
    corregir sus coordenadas, vuelven a aparecer y se pueden replanificar."""
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
        }
        for pedido in pedidos
    ]


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


async def _llamar_n8n(paradas: list[dict]) -> dict:
    """POST síncrono al webhook de n8n. Cualquier falla (timeout, error HTTP,
    respuesta que no es JSON) es un error claro para el panel; no se escribe
    nada en la base."""
    headers = {HEADER_SECRETO_N8N: settings.N8N_ROUTE_WEBHOOK_SECRET}
    try:
        async with _cliente_http() as cliente:
            respuesta = await cliente.post(
                settings.N8N_ROUTE_WEBHOOK_URL, json={"paradas": paradas}, headers=headers
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


def _validar_respuesta(respuesta, enviados: list[int]) -> tuple[list[dict], list[dict]]:
    """Valida la respuesta de n8n contra el contrato (ver docstring del
    módulo). Devuelve (ruta, sin_resolver) o lanza 502 con los problemas."""
    problemas: list[str] = []
    if not isinstance(respuesta, dict) or not isinstance(respuesta.get("ruta"), list):
        raise _error(status.HTTP_502_BAD_GATEWAY, "Respuesta inválida del servicio de rutas.", problemas=["falta la lista 'ruta'"])
    ruta = respuesta["ruta"]
    sin_resolver = respuesta.get("sin_resolver", [])
    if not isinstance(sin_resolver, list):
        problemas.append("'sin_resolver' no es una lista")
        sin_resolver = []

    vistos: list[int] = []
    ordenes: list[int] = []
    for item in ruta:
        if not isinstance(item, dict) or not _es_entero(item.get("pedido_id")):
            problemas.append(f"parada de ruta sin pedido_id entero: {item!r}")
            continue
        pid = item["pedido_id"]
        vistos.append(pid)
        orden = item.get("orden_entrega")
        if not _es_entero(orden) or orden < 1:
            problemas.append(f"pedido {pid}: orden_entrega no es un entero positivo ({orden!r})")
        else:
            ordenes.append(orden)
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
    ordenes_repetidos = sorted({o for o in ordenes if ordenes.count(o) > 1})
    if ajenos:
        problemas.append(f"pedidos que no se enviaron: {ajenos}")
    if repetidos:
        problemas.append(f"pedidos repetidos (o en ruta y sin_resolver a la vez): {repetidos}")
    if faltantes:
        problemas.append(f"pedidos enviados que no vinieron en la respuesta: {faltantes}")
    if ordenes_repetidos:
        problemas.append(f"orden_entrega repetidos: {ordenes_repetidos}")
    elif len(ordenes) == len(ruta) and sorted(ordenes) != list(range(1, len(ruta) + 1)):
        problemas.append(f"orden_entrega no es consecutivo 1..{len(ruta)}: {sorted(ordenes)}")
    if problemas:
        logger.warning("[rutas] Respuesta de n8n inválida: %s", problemas)
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "El servicio de rutas devolvió una ruta inválida. No se guardó nada; los pedidos siguen pendientes.",
            problemas=problemas,
        )
    return ruta, sin_resolver


async def planificar_ruta(pedido_ids: list[int], usuario: str | None) -> dict:
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
        return await _planificar(pedido_ids, usuario)


async def _planificar(pedido_ids: list[int], usuario: str | None) -> dict:
    # 1. Paradas armadas solo desde la BD (del panel solo se usan los IDs).
    async with SessionLocal() as session:
        pedidos = await _cargar_pedidos(session, pedido_ids)
    _verificar_planificables(pedido_ids, pedidos)
    paradas = [
        {
            "pedido_id": pid,
            "direccion_texto": _direccion(pedidos[pid]),
            "latitud": _coordenada(pedidos[pid].latitud) if _tiene_coordenadas(pedidos[pid]) else None,
            "longitud": _coordenada(pedidos[pid].longitud) if _tiene_coordenadas(pedidos[pid]) else None,
        }
        for pid in pedido_ids
    ]
    # Para detectar cambios hechos mientras se espera a n8n.
    leido_en = {pid: pedidos[pid].actualizado_en for pid in pedido_ids}

    # 2. n8n (sin ninguna transacción abierta mientras se espera).
    respuesta = await _llamar_n8n(paradas)
    ruta, sin_resolver = _validar_respuesta(respuesta, pedido_ids)
    motivos = {item["pedido_id"]: item["motivo"] for item in sin_resolver}
    ruta_por_id = {item["pedido_id"]: item for item in ruta}

    # 3. Persistencia en una sola transacción.
    ahora = datetime.utcnow()
    async with SessionLocal() as session:
        async with session.begin():
            pedidos = await _cargar_pedidos(session, pedido_ids, bloquear=True)
            # Pudieron cambiar mientras se esperaba a n8n (ej. un cancelado, o
            # una dirección o coordenadas corregidas): no se escribe nada.
            _verificar_planificables(pedido_ids, pedidos)
            for pid in pedido_ids:
                if pedidos[pid].actualizado_en != leido_en[pid]:
                    raise _error(
                        status.HTTP_409_CONFLICT,
                        f"El pedido {pid} cambió durante la planificación, reintenta",
                    )
            for pid in pedido_ids:
                pedido = pedidos[pid]
                antes = construir_snapshot(pedido)
                pedido.estado = EstadoPedido.CONFIRMADO
                parada = ruta_por_id.get(pid)
                pedido.orden_entrega = parada["orden_entrega"] if parada else None

                # Historia #107: si n8n no pudo ubicar la dirección, queda marcado
                # con el motivo para revisión manual. Si entró a la ruta, se
                # limpia una marca anterior.
                pedido.motivo_revision_direccion = motivos.get(pid)
                if parada and not _tiene_coordenadas(pedido):
                    # Solo se completan las coordenadas del pedido que llegó
                    # sin ellas (o con el par a medias, que se sobrescribe
                    # entero); las del cliente nunca se tocan.
                    pedido.latitud = parada["latitud"]
                    pedido.longitud = parada["longitud"]
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
            # Rutas anteriores que pueden haber quedado viejas: se informan,
            # no se tocan.
            result = await session.execute(
                select(Pedido.id, Pedido.orden_entrega)
                .where(
                    Pedido.estado == EstadoPedido.CONFIRMADO,
                    Pedido.orden_entrega.is_not(None),
                    Pedido.id.not_in(pedido_ids),
                )
                .order_by(Pedido.id)
            )
            fuera_de_solicitud = [{"pedido_id": fila.id, "orden_entrega": fila.orden_entrega} for fila in result]

    logger.info(
        "[rutas] Ruta generada por %s: %s en ruta, %s sin resolver", usuario, len(ruta), len(sin_resolver)
    )
    return {
        "ruta": [
            {
                "pedido_id": pid,
                "orden_entrega": ruta_por_id[pid]["orden_entrega"],
                "cliente_nombre": _nombre_cliente(pedidos[pid].cliente),
                "direccion_texto": _direccion(pedidos[pid]),
                "latitud": _coordenada(pedidos[pid].latitud),
                "longitud": _coordenada(pedidos[pid].longitud),
            }
            for pid in sorted(ruta_por_id, key=lambda p: ruta_por_id[p]["orden_entrega"])
        ],
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
