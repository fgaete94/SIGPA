from __future__ import annotations

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ClienteProductoHabitual(Base):
    __tablename__ = "cliente_producto_habitual"

    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("cliente.id", ondelete="CASCADE"), primary_key=True
    )
    producto_id: Mapped[int] = mapped_column(
        ForeignKey("producto.id", ondelete="CASCADE"), primary_key=True
    )

    cliente: Mapped["Cliente"] = relationship("Cliente", back_populates="productos_habituales")
    producto: Mapped["Producto"] = relationship("Producto", back_populates="clientes_habituales")
