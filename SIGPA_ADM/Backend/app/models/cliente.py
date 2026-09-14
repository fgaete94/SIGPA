from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import DiaSemana


class Cliente(Base):
    __tablename__ = "cliente"

    id: Mapped[int] = mapped_column(primary_key=True)
    cod_legado: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    telefono: Mapped[str | None] = mapped_column(String(30), nullable=True)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sector.id"), nullable=True)
    tipo_cliente_id: Mapped[int | None] = mapped_column(ForeignKey("tipo_cliente.id"), nullable=True)
    dia_reparto: Mapped[DiaSemana | None] = mapped_column(
        SAEnum(
            DiaSemana,
            name="dia_semana",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=True,
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    latitud: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitud: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)

    sector: Mapped["Sector"] = relationship("Sector", back_populates="clientes")
    tipo_cliente: Mapped["TipoCliente"] = relationship("TipoCliente", back_populates="clientes")
    pedidos: Mapped[list["Pedido"]] = relationship("Pedido", back_populates="cliente")
    productos_habituales: Mapped[list["ClienteProductoHabitual"]] = relationship(
        "ClienteProductoHabitual", back_populates="cliente", cascade="all, delete-orphan"
    )
