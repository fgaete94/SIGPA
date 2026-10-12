from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import DiaSemana, EstadoPedido

class TipoClienteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str

class SectorOut(BaseModel):
    id: int
    nombre: str
    comuna_nombre: str

class ClienteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    apellido_paterno: str | None = None
    apellido_materno: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    sector_id: int | None = None
    tipo_cliente_id: int | None = None
    tipo_cliente_nombre: str | None = None
    dia_reparto: DiaSemana | None = None
    activo: bool
    opt_out_whatsapp: bool
    latitud: float | None = None
    longitud: float | None = None

class PedidoHistorialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    estado: EstadoPedido
    total: float
    creado_en: datetime


class ClienteDetalleOut(ClienteOut):
    pedidos: list[PedidoHistorialOut]

class ClienteCreate(BaseModel):
    nombre: str
    apellido_paterno: str | None = None
    apellido_materno: str | None = None
    telefono: str
    direccion: str | None = None
    sector_id: int | None = None
    tipo_cliente_id: int | None = None
    dia_reparto: DiaSemana | None = None
    activo: bool = True
    opt_out_whatsapp: bool = False
    latitud: float | None = None
    longitud: float | None = None


class ClienteUpdate(BaseModel):
    nombre: str | None = None
    apellido_paterno: str | None = None
    apellido_materno: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    sector_id: int | None = None
    tipo_cliente_id: int | None = None
    dia_reparto: DiaSemana | None = None
    activo: bool | None = None
    opt_out_whatsapp: bool | None = None
    latitud: float | None = None
    longitud: float | None = None
