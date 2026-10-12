from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.security import get_current_user
from app.services.ruta_service import listar_pendientes, planificar_ruta, ruta_actual

router = APIRouter(tags=["rutas"], dependencies=[Depends(get_current_user)])


class PedidoPendienteOut(BaseModel):
    pedido_id: int
    cliente_id: int
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None
    direccion_texto: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    creado_en: datetime
    estado: str
    motivo_revision_direccion: str | None = None


class PlanificarRutaRequest(BaseModel):
    pedido_ids: list[int] = Field(min_length=1)


class ParadaRutaOut(BaseModel):
    pedido_id: int
    orden_entrega: int
    cliente_id: int
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None
    direccion_texto: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    estado: str


class AdvertenciaRutaOut(BaseModel):
    tipo: str
    mensaje: str
    orden_entrega: int
    pedido_ids: list[int]


class RutaActualOut(BaseModel):
    ruta: list[ParadaRutaOut]
    total: int
    advertencias: list[AdvertenciaRutaOut]


@router.get("/pedidos-pendientes", response_model=list[PedidoPendienteOut])
async def pedidos_pendientes():
    """Pedidos planificables: todos los "pendiente" más los "confirmado" sin
    orden_entrega o con la dirección por revisar (ver listar_pendientes). La
    ejecutiva decide cuáles incluir en la ruta. No hay filtro por fecha
    porque pedido no tiene fecha_entrega."""
    return await listar_pendientes()


@router.get("/actual", response_model=RutaActualOut)
async def actual():
    """Ruta guardada: pedidos "confirmado" con orden_entrega, por parada.
    Sin filtro por fecha porque pedido no tiene fecha_entrega ni id de ruta;
    los orden_entrega repetidos (rutas mezcladas) vienen en "advertencias"."""
    return await ruta_actual()


@router.post("/planificar")
async def planificar(datos: PlanificarRutaRequest, current_user: dict = Depends(get_current_user)):
    """Genera la ruta con los pedidos indicados (ver ruta_service): llama a
    n8n y, si todo es válido, los confirma con su orden de entrega."""
    return await planificar_ruta(datos.pedido_ids, current_user.get("email"))
