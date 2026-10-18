from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DiaSemana, EstadoPedido


class PedidoOut(BaseModel):
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
    orden_entrega: int | None = None
    motivo_revision_direccion: str | None = None


class DetallePedidoLineaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Historia #99: id del detalle, que POST /pedidos/{id}/entrega recibe como detalle_id
    id: int
    cantidad_solicitada: int
    cantidad_entregada: int | None = None
    precio_unitario: float
    producto_nombre: str


class PedidoDetalleOut(PedidoOut):
    lineas: list[DetallePedidoLineaOut]


class PedidoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direccion_despacho: str | None = None
    latitud: float | None = None
    longitud: float | None = None


class LineaPedidoCreate(BaseModel):
    producto_id: int
    cantidad_solicitada: int = Field(gt=0)


class PedidoCreate(BaseModel):
    cliente_id: int
    direccion_despacho: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    estado: EstadoPedido = EstadoPedido.PENDIENTE
    lineas: list[LineaPedidoCreate] = Field(min_length=1)


class LineaEntregaRequest(BaseModel):
    detalle_id: int
    cantidad_entregada: int = Field(ge=0)


class RegistrarEntregaRequest(BaseModel):
    lineas: list[LineaEntregaRequest] = Field(min_length=1)


class CambiarEstadoRequest(BaseModel):
    estado: EstadoPedido

class CorregirCoordenadasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Historia #113: rangos válidos de coordenadas geográficas
    latitud: float = Field(ge=-90, le=90)
    longitud: float = Field(ge=-180, le=180)

# Historia #70: un cambio de estado en GET /pedidos/{id}/historial
class HistorialEstadoOut(BaseModel):
    estado_anterior: EstadoPedido | None  # None cuando el pedido se creó con ese estado
    estado_nuevo: EstadoPedido
    usuario: str
    accion: str  # crear, cambiar_estado, actualizar, planificar_ruta
    fecha: datetime