from pydantic import BaseModel, ConfigDict

from app.models.enums import DiaSemana


class ClienteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    telefono: str | None = None
    direccion: str | None = None
    sector_id: int | None = None
    tipo_cliente_id: int | None = None
    dia_reparto: DiaSemana | None = None
    activo: bool
    latitud: float | None = None
    longitud: float | None = None


class ClienteCreate(BaseModel):
    nombre: str
    telefono: str
    direccion: str | None = None
    sector_id: int | None = None
    tipo_cliente_id: int | None = None
    dia_reparto: DiaSemana | None = None
    activo: bool = True
    latitud: float | None = None
    longitud: float | None = None


class ClienteUpdate(BaseModel):
    nombre: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    sector_id: int | None = None
    tipo_cliente_id: int | None = None
    dia_reparto: DiaSemana | None = None
    activo: bool | None = None
    latitud: float | None = None
    longitud: float | None = None
