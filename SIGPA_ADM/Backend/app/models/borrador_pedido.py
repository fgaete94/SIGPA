from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class BorradorPedido(Base):
    """Respaldo del borrador de pedido que el bot arma por teléfono (ver
    app/services/draft_store.py)."""

    __tablename__ = "borrador_pedido"

    telefono: Mapped[str] = mapped_column(String(30), primary_key=True)
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
