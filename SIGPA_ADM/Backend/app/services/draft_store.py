"""Almacenamiento de borradores de pedido en memoria.

TEMPORAL: estos borradores viven solo en un dict de proceso y se pierden
al reiniciar el servidor. Cuando se agregue persistencia real (Redis o
una tabla en Supabase), esta implementación se reemplaza pero la interfaz
pública (get_draft, save_draft, clear_draft) se mantiene igual para no
tener que tocar el código que la consume.
"""

import asyncio

_drafts: dict[str, dict] = {}

_locks: dict[str, asyncio.Lock] = {}


def get_draft(phone: str) -> dict | None:
    return _drafts.get(phone)


def save_draft(phone: str, data: dict) -> None:
    _drafts[phone] = data


def clear_draft(phone: str) -> None:
    _drafts.pop(phone, None)


def get_lock(phone: str) -> asyncio.Lock:
    """Lock por teléfono para serializar el procesamiento de mensajes de un
    mismo cliente (ver procesar_mensaje en order_flow.py).

    setdefault es atómico dentro de un mismo event loop (asyncio es de un
    solo hilo), así que no hace falta proteger la creación del lock con otro
    lock. Mecanismo simple, adecuado solo para un proceso/worker: si en el
    futuro se escala a múltiples procesos o instancias, esto debe migrarse a
    un lock distribuido (ej. Redis)."""
    return _locks.setdefault(phone, asyncio.Lock())
