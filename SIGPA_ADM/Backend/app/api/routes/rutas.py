from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.security import get_current_user
from app.services.ruta_service import confirmar_ruta, listar_pendientes, planificar_ruta, ruta_actual

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
    # Peso del pedido en kg (SUM(cantidad * peso_kg), redondeado hacia arriba);
    # null si no se puede calcular (sin ítems o con productos sin peso).
    peso_kg: int | None = None


class PlanificarRutaRequest(BaseModel):
    pedido_ids: list[int] = Field(min_length=1)
    # Historia #87: solo calcula y devuelve la ruta propuesta, sin guardar nada.
    vista_previa: bool = False


class ParadaConfirmadaIn(BaseModel):
    pedido_id: int
    # Viaje de la parada (el de la vista previa); sin él se asume el viaje 1.
    viaje: int = Field(default=1, ge=1)
    latitud: float = Field(ge=-90, le=90)
    longitud: float = Field(ge=-180, le=180)
    # actualizado_en del pedido tal como vino en la vista previa
    version: datetime


class SinResolverConfirmadoIn(BaseModel):
    pedido_id: int
    motivo: str = Field(min_length=1, max_length=300)
    version: datetime


class ConfirmarRutaRequest(BaseModel):
    # En el orden final de visita (la primera es la parada 1). Puede ir vacía si n8n no
    # ubicó ninguna dirección: igual se confirman los sin_resolver para revisarlos.
    paradas: list[ParadaConfirmadaIn] = []
    sin_resolver: list[SinResolverConfirmadoIn] = []


class ParadaRutaOut(BaseModel):
    pedido_id: int
    viaje: int | None = None
    orden_entrega: int
    cliente_id: int
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None
    direccion_texto: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    estado: str


class ViajeOut(BaseModel):
    viaje: int
    peso_kg: int | None = None
    # Derivado, no se guarda: "casa" solo el último viaje, si hay al menos 2 y pesa menos que el umbral.
    termina_en: str
    total_paradas: int


class AdvertenciaRutaOut(BaseModel):
    tipo: str
    mensaje: str
    viaje: int | None = None
    orden_entrega: int
    pedido_ids: list[int]


class RutaActualOut(BaseModel):
    ruta: list[ParadaRutaOut]
    viajes: list[ViajeOut]
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
    """Ruta guardada: pedidos "confirmado" y "en_despacho" con orden_entrega,
    por viaje y parada, con el resumen "viajes" (peso y termina_en derivado).
    Sin filtro por fecha porque pedido no tiene fecha_entrega ni id de ruta;
    los (viaje, orden_entrega) repetidos (rutas mezcladas) vienen en "advertencias"."""
    return await ruta_actual()


@router.post("/planificar")
async def planificar(datos: PlanificarRutaRequest, current_user: dict = Depends(get_current_user)):
    """Genera la ruta con los pedidos indicados (ver ruta_service): llama a
    n8n y, si todo es válido, los confirma con su orden de entrega."""
    return await planificar_ruta(datos.pedido_ids, current_user.get("email"), datos.vista_previa)


@router.post("/confirmar")
async def confirmar(datos: ConfirmarRutaRequest, current_user: dict = Depends(get_current_user)):
    """Historia #87: guarda la ruta revisada en la vista previa, en el orden
    recibido. Mismas reglas que planificar; 409 si algún pedido cambió desde
    que se calculó (hay que volver a calcular)."""
    return await confirmar_ruta(
        [p.model_dump() for p in datos.paradas],
        [s.model_dump() for s in datos.sin_resolver],
        current_user.get("email"),
    )
