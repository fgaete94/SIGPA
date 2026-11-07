from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Repartidor(Base):
    __tablename__ = "repartidor"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Usuario de Supabase Auth: el "sub" del token identifica al repartidor.
    auth_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class IntentoNoEntregado(Base):
    """Cada vez que una parada se marcó como no entregada, con su lugar en la
    ruta de ese día: reagendar el pedido borra ese lugar del pedido."""

    __tablename__ = "intento_no_entregado"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(
        ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False
    )
    repartidor_id: Mapped[int | None] = mapped_column(ForeignKey("repartidor.id"), nullable=True)
    viaje: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    orden_entrega: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )

    pedido: Mapped["Pedido"] = relationship("Pedido")


class CargaCombustible(Base):
    __tablename__ = "carga_combustible"

    id: Mapped[int] = mapped_column(primary_key=True)
    repartidor_id: Mapped[int] = mapped_column(ForeignKey("repartidor.id"), nullable=False)
    cargo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    monto: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    medio_pago: Mapped[str | None] = mapped_column(String(20), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
