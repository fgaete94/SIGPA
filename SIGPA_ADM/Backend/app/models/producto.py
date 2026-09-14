from __future__ import annotations

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Producto(Base):
    __tablename__ = "producto"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    precio_unitario: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capacidad_litros: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    clientes_habituales: Mapped[list["ClienteProductoHabitual"]] = relationship(
        "ClienteProductoHabitual", back_populates="producto", cascade="all, delete-orphan"
    )
    detalles_pedido: Mapped[list["DetallePedido"]] = relationship(
        "DetallePedido", back_populates="producto"
    )
