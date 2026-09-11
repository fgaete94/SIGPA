"""Almacenamiento de borradores de pedido en memoria.

TEMPORAL: estos borradores viven solo en un dict de proceso y se pierden
al reiniciar el servidor. Cuando se agregue persistencia real (Redis o
una tabla en Supabase), esta implementación se reemplaza pero la interfaz
pública (get_draft, save_draft, clear_draft) se mantiene igual para no
tener que tocar el código que la consume.
"""

_drafts: dict[str, dict] = {}


def get_draft(phone: str) -> dict | None:
    return _drafts.get(phone)


def save_draft(phone: str, data: dict) -> None:
    _drafts[phone] = data


def clear_draft(phone: str) -> None:
    _drafts.pop(phone, None)
