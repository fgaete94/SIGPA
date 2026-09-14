from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TipoCliente(Base):
    __tablename__ = "tipo_cliente"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    clientes: Mapped[list["Cliente"]] = relationship("Cliente", back_populates="tipo_cliente")
