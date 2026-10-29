"""Borradores de pedido del bot: caché en memoria + respaldo en Supabase.

La interfaz que usa order_flow.py es síncrona (get_draft, save_draft,
clear_draft) y opera sobre el dict _drafts, que es la caché. El respaldo vive
en la tabla borrador_pedido (historia #150) para que un redeploy o la
suspensión del servicio no deje al cliente a medio pedido:

- cargar_draft(phone): antes de procesar un mensaje, si el teléfono no está
  en la caché lo lee de la tabla (si está vigente, según BORRADOR_TTL_HORAS).
- persistir_draft(phone): después de procesarlo, deja la tabla igual que la
  caché (upsert, o delete si ya no hay borrador). No escribe si nada cambió.

procesar_mensaje (order_flow.py) las llama dentro del lock del teléfono. Un
fallo de la BD en cualquiera de las dos se loguea y la conversación sigue con
la caché; el siguiente mensaje reintenta persistir.

Limitaciones que se mantienen: la caché, el lock por teléfono (get_lock), la
cola del webhook (webhook_queue.py) y la deduplicación viven en la memoria
del proceso, así que esto solo es correcto con un único proceso/worker. La BD
sirve para recuperarse de un reinicio, no para compartir borradores entre
varias instancias.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.borrador_pedido import BorradorPedido

logger = logging.getLogger(__name__)

# Una BD lenta no debe dejar al cliente esperando la respuesta del bot.
TIMEOUT_BD_SEGUNDOS = 10

_drafts: dict[str, dict] = {}

# Último estado conocido de la tabla por teléfono: el JSON que se escribió o
# leyó, o None si se sabe que no hay fila. Sin entrada = no se sabe (aún no
# se cargó o la carga falló). Evita escrituras cuando nada cambió.
_persistidos: dict[str, str | None] = {}

# Teléfonos cuyo borrador se descartó (clear_draft) y cuya fila en la tabla
# aún no se borró. Mientras estén aquí, cargar_draft no lee la fila: el
# descarte gana sobre el respaldo (si no, un borrador cancelado reviviría
# desde la BD, p. ej. si el delete falló por una caída).
_pendientes_borrar: set[str] = set()

_locks: dict[str, asyncio.Lock] = {}


def get_draft(phone: str) -> dict | None:
    return _drafts.get(phone)


def save_draft(phone: str, data: dict) -> None:
    _drafts[phone] = data
    _pendientes_borrar.discard(phone)


def clear_draft(phone: str) -> None:
    _drafts.pop(phone, None)
    _pendientes_borrar.add(phone)


def get_lock(phone: str) -> asyncio.Lock:
    """Lock por teléfono para serializar el procesamiento de mensajes de un
    mismo cliente (ver procesar_mensaje en order_flow.py).

    setdefault es atómico dentro de un mismo event loop (asyncio es de un
    solo hilo), así que no hace falta proteger la creación del lock con otro
    lock. Mecanismo simple, adecuado solo para un proceso/worker: si en el
    futuro se escala a múltiples procesos o instancias, esto debe migrarse a
    un lock distribuido (ej. Redis)."""
    return _locks.setdefault(phone, asyncio.Lock())


# --------------------------------------------------------------------------
# Serialización
# --------------------------------------------------------------------------
#
# Los borradores son dicts/listas de str, int, float, bool y None, pero en el
# flujo aparecen también set/frozenset (atributos de menciones) y tuple.
# jsonb no los tiene, y un str() ciego los degradaría sin aviso (un set
# volvería como lista y un Decimal como texto). Por eso el codec marca con
# {"__t": <tipo>, "v": ...} todo lo que no es JSON nativo y falla con
# TypeError ante un tipo desconocido, en vez de guardar algo que no vuelve
# igual. Se cumple deserializar(serializar(d)) == d, con las mismas formas
# (dict vs. lista en aclaracion_pendiente, int vs. float).

_ETIQUETA = "__t"


def serializar(valor: Any) -> Any:
    """Convierte un borrador a una estructura apta para jsonb."""
    if valor is None or isinstance(valor, (bool, int, str)):
        return valor
    if isinstance(valor, float):
        if valor != valor or valor in (float("inf"), float("-inf")):
            raise TypeError(f"float no representable en JSON: {valor!r}")
        return valor
    if isinstance(valor, dict):
        if all(isinstance(k, str) for k in valor) and _ETIQUETA not in valor:
            return {k: serializar(v) for k, v in valor.items()}
        # Claves no string (o que chocan con la etiqueta): pares clave/valor.
        return {_ETIQUETA: "dict", "v": [[serializar(k), serializar(v)] for k, v in valor.items()]}
    if isinstance(valor, list):
        return [serializar(v) for v in valor]
    if isinstance(valor, tuple):
        return {_ETIQUETA: "tuple", "v": [serializar(v) for v in valor]}
    if isinstance(valor, frozenset):
        return {_ETIQUETA: "frozenset", "v": _ordenar([serializar(v) for v in valor])}
    if isinstance(valor, set):
        return {_ETIQUETA: "set", "v": _ordenar([serializar(v) for v in valor])}
    if isinstance(valor, Decimal):
        return {_ETIQUETA: "decimal", "v": str(valor)}
    if isinstance(valor, datetime):
        return {_ETIQUETA: "datetime", "v": valor.isoformat()}
    if isinstance(valor, date):
        return {_ETIQUETA: "date", "v": valor.isoformat()}
    raise TypeError(f"Tipo no serializable en el borrador: {type(valor).__name__}")


def _ordenar(elementos: list) -> list:
    # Orden estable para que el mismo conjunto dé siempre el mismo JSON (el
    # snapshot que evita escrituras redundantes se compara como texto).
    return sorted(elementos, key=lambda e: json.dumps(e, sort_keys=True))


def deserializar(valor: Any) -> Any:
    """Inversa de serializar."""
    if isinstance(valor, list):
        return [deserializar(v) for v in valor]
    if isinstance(valor, dict):
        if _ETIQUETA not in valor:
            return {k: deserializar(v) for k, v in valor.items()}
        tipo, contenido = valor[_ETIQUETA], valor["v"]
        if tipo == "dict":
            return {deserializar(k): deserializar(v) for k, v in contenido}
        if tipo == "tuple":
            return tuple(deserializar(v) for v in contenido)
        if tipo == "frozenset":
            return frozenset(deserializar(v) for v in contenido)
        if tipo == "set":
            return {deserializar(v) for v in contenido}
        if tipo == "decimal":
            return Decimal(contenido)
        if tipo == "datetime":
            return datetime.fromisoformat(contenido)
        if tipo == "date":
            return date.fromisoformat(contenido)
        raise ValueError(f"Etiqueta desconocida en el borrador: {tipo!r}")
    return valor


def _a_texto(draft: dict) -> str:
    return json.dumps(serializar(draft), sort_keys=True, allow_nan=False)


# --------------------------------------------------------------------------
# Respaldo en la BD
# --------------------------------------------------------------------------


async def cargar_draft(phone: str) -> None:
    """Si el teléfono no tiene borrador en la caché, lo lee de la tabla y lo
    deja en la caché cuando está vigente (el vencido se borra). Ante un fallo
    de la BD loguea y sigue con la caché: no se tumba la conversación."""
    if phone in _drafts or phone in _pendientes_borrar:
        return
    try:
        await asyncio.wait_for(_cargar(phone), TIMEOUT_BD_SEGUNDOS)
    except Exception:
        logger.exception("[draft_store] No se pudo cargar el borrador de phone=%s; sigue en memoria", phone)


async def _cargar(phone: str) -> None:
    ttl = timedelta(hours=settings.BORRADOR_TTL_HORAS)
    async with SessionLocal() as session:
        fila = (
            await session.execute(
                select(
                    BorradorPedido.datos,
                    (func.now() - BorradorPedido.actualizado_en > ttl).label("vencido"),
                ).where(BorradorPedido.telefono == phone)
            )
        ).first()
        if fila is None:
            _persistidos[phone] = None
            return
        if fila.vencido:
            await session.execute(
                delete(BorradorPedido).where(
                    BorradorPedido.telefono == phone,
                    func.now() - BorradorPedido.actualizado_en > ttl,
                )
            )
            await session.commit()
            _persistidos[phone] = None
            return

    try:
        draft = deserializar(fila.datos)
        texto = _a_texto(draft)
    except Exception:
        # Fila ilegible: se ignora y el siguiente persistir la sobrescribe.
        logger.exception("[draft_store] Borrador ilegible en la BD para phone=%s; se ignora", phone)
        return
    _drafts[phone] = draft
    _persistidos[phone] = texto


async def descartar_draft(phone: str) -> None:
    """Descarta el borrador del teléfono en la caché y en la tabla: lo usa
    marcar_activa al empezar una conversación nueva (el recordatorio), para
    que no se retome un pedido viejo. Nunca lanza: un fallo de la BD se loguea
    y no debe impedir quien la llama. Si el delete falla, el descarte queda
    anotado como pendiente (ver clear_draft) y se completa con el siguiente
    persistir_draft del teléfono."""
    clear_draft(phone)
    await persistir_draft(phone)


async def persistir_draft(phone: str) -> None:
    """Deja la tabla igual que la caché: upsert si hay borrador, delete si no.
    No escribe si el borrador es el mismo que ya se persistió. Ante un fallo
    loguea y sigue: la caché conserva el borrador y el próximo mensaje
    reintenta."""
    try:
        await asyncio.wait_for(_persistir(phone), TIMEOUT_BD_SEGUNDOS)
    except Exception:
        logger.exception("[draft_store] No se pudo persistir el borrador de phone=%s; sigue en memoria", phone)


async def _persistir(phone: str) -> None:
    draft = _drafts.get(phone)
    if draft is None:
        if phone not in _pendientes_borrar and phone in _persistidos and _persistidos[phone] is None:
            return
        async with SessionLocal() as session:
            await session.execute(delete(BorradorPedido).where(BorradorPedido.telefono == phone))
            await session.commit()
        _persistidos[phone] = None
        _pendientes_borrar.discard(phone)
        return

    texto = _a_texto(draft)
    if _persistidos.get(phone) == texto:
        return
    datos = json.loads(texto)
    stmt = pg_insert(BorradorPedido).values(telefono=phone, datos=datos)
    stmt = stmt.on_conflict_do_update(
        index_elements=[BorradorPedido.telefono],
        set_={"datos": datos, "actualizado_en": func.now()},
    )
    async with SessionLocal() as session:
        await session.execute(stmt)
        await session.commit()
    _persistidos[phone] = texto
