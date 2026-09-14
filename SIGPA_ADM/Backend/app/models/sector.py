from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Sector(Base):
    __tablename__ = "sector"
    __table_args__ = (UniqueConstraint("comuna_id", "nombre"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    comuna_id: Mapped[int] = mapped_column(ForeignKey("comuna.id"), nullable=False)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)

    comuna: Mapped["Comuna"] = relationship("Comuna", back_populates="sectores")
    clientes: Mapped[list["Cliente"]] = relationship("Cliente", back_populates="sector")
