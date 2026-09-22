from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DiaSemana, EstadoPedido


class PedidoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_id: int
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None
    estado: EstadoPedido
    direccion_despacho: str | None = None
    total: float
    creado_en: datetime
    actualizado_en: datetime
    latitud: float | None = None
    longitud: float | None = None


class DetallePedidoLineaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cantidad: int
    precio_unitario: float
    producto_nombre: str


class PedidoDetalleOut(PedidoOut):
    lineas: list[DetallePedidoLineaOut]


class PedidoUpdate(BaseModel):
    estado: EstadoPedido | None = None
    direccion_despacho: str | None = None
    latitud: float | None = None
    longitud: float | None = None


class LineaPedidoCreate(BaseModel):
    producto_id: int
    cantidad: int = Field(gt=0)


class PedidoCreate(BaseModel):
    cliente_id: int
    direccion_despacho: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    estado: EstadoPedido = EstadoPedido.PENDIENTE
    lineas: list[LineaPedidoCreate] = Field(min_length=1)


    """
    PedidoDespachoOut: Respuesta para el listado de pedidos de despacho (historia #67).

    Es independiente de PedidoOut: no lo extiende ni lo modifica, para no
    afectar los endpoints existentes de pedidos (POST, PATCH, GET /pedidos/{id}).
    Incluye comuna y día de reparto del cliente, que PedidoOut no expone.
    """

class PedidoDespachoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_id: int
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None
    comuna_nombre: str | None = None
    dia_reparto: DiaSemana | None = None
    estado: EstadoPedido
    direccion_despacho: str | None = None
    total: float
    creado_en: datetime
    actualizado_en: datetime
    latitud: float | None = None
    longitud: float | None = None
