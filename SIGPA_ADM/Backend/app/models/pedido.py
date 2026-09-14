from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import EstadoPedido


class Pedido(Base):
    __tablename__ = "pedido"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"), nullable=False)
    estado: Mapped[EstadoPedido] = mapped_column(
        SAEnum(
            EstadoPedido,
            name="estado_pedido",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=EstadoPedido.PENDIENTE,
        server_default=EstadoPedido.PENDIENTE.value,
    )
    direccion_despacho: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    latitud: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitud: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)

    cliente: Mapped["Cliente"] = relationship("Cliente", back_populates="pedidos")
    detalles: Mapped[list["DetallePedido"]] = relationship(
        "DetallePedido", back_populates="pedido", cascade="all, delete-orphan"
    )
