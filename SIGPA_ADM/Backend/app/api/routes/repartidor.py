"""Vista del repartidor en el celular y lo que la administración necesita para
asignarle la ruta y ver el combustible. El repartidor solo llega a /repartidor/*:
el resto de la API exige el rol admin (ver app/core/security.py)."""

from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.security import require_admin, require_repartidor
from app.models.cliente import Cliente
from app.models.detalle_pedido import DetallePedido
from app.models.enums import EstadoPedido
from app.models.pedido import Pedido
from app.models.repartidor import CargaCombustible, IntentoNoEntregado, Repartidor
from app.schemas.pedido import DetallePedidoLineaOut, LineaEntregaRequest
from app.services.auditoria_service import construir_snapshot, registrar_auditoria
from app.services.entrega import ZONA_HORARIA, cerrar_entrega, dia_chile, hoy_chile, total_pedido

router_repartidor = APIRouter(prefix="/repartidor", tags=["repartidor"])
router_admin = APIRouter(tags=["repartidor"], dependencies=[Depends(require_admin)])

ESTADOS_POR_ENTREGAR = (EstadoPedido.CONFIRMADO, EstadoPedido.EN_DESPACHO)
ESTADOS_RESUELTOS = (EstadoPedido.ENTREGADO, EstadoPedido.NO_ENTREGADO)


class RepartidorOut(BaseModel):
    id: int
    nombre: str


class AsignarRutaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # null quita la asignación
    repartidor_id: int | None
    pedido_ids: list[int]


class ParadaOut(BaseModel):
    id: int
    viaje: int | None
    orden_entrega: int | None
    estado: EstadoPedido
    cliente_nombre: str | None
    cliente_telefono: str | None
    direccion: str | None
    latitud: float | None
    longitud: float | None
    total: float
    medio_pago: str | None
    resultado_en: datetime | None
    entrega_ajustada: bool
    lineas: list[DetallePedidoLineaOut]


class RepartidorNombreOut(BaseModel):
    nombre: str


class RutaRepartidorOut(BaseModel):
    repartidor: RepartidorNombreOut
    paradas: list[ParadaOut]
    viajes: list[dict] = []


class ResultadoParadaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # null deshace lo marcado hoy (botón "Corregir")
    resultado: Literal["entregado", "no_entregado"] | None
    medio_pago: Literal["efectivo", "transferencia"] | None = None
    # Sin líneas se entiende que entregó todo lo pedido.
    lineas: list[LineaEntregaRequest] | None = None


class CargaCombustibleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cargo: bool
    monto: int | None = Field(default=None, gt=0, le=9_999_999)
    medio_pago: Literal["efectivo", "tarjeta"] | None = None


class CargaCombustibleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    repartidor_id: int
    cargo: bool
    monto: float | None
    medio_pago: str | None
    creado_en: datetime


class CombustibleRepartidorOut(BaseModel):
    repartidor: str
    registros: list[CargaCombustibleOut]


def _dia_chile_python(momento_utc: datetime) -> date:
    return momento_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(ZONA_HORARIA)).date()


def _nombre_cliente(cliente: Cliente | None) -> str | None:
    if cliente is None:
        return None
    return " ".join(p for p in (cliente.nombre, cliente.apellido_paterno, cliente.apellido_materno) if p)


def _parada_out(pedido: Pedido) -> ParadaOut:
    cliente = pedido.cliente
    return ParadaOut(
        id=pedido.id,
        viaje=pedido.viaje,
        orden_entrega=pedido.orden_entrega,
        estado=pedido.estado,
        cliente_nombre=_nombre_cliente(cliente),
        cliente_telefono=cliente.telefono if cliente else None,
        direccion=pedido.direccion_despacho or (cliente.direccion if cliente else None),
        latitud=pedido.latitud,
        longitud=pedido.longitud,
        total=pedido.total,
        medio_pago=pedido.medio_pago,
        resultado_en=pedido.resultado_en,
        entrega_ajustada=pedido.entrega_ajustada,
        lineas=[
            DetallePedidoLineaOut(
                id=d.id,
                producto_id=d.producto_id,
                cantidad_solicitada=d.cantidad_solicitada,
                cantidad_entregada=d.cantidad_entregada,
                precio_unitario=d.precio_unitario,
                producto_nombre=d.producto.nombre,
            )
            for d in sorted(pedido.detalles, key=lambda d: d.id)
        ],
    )


async def _repartidor_actual(session: AsyncSession, current_user: dict) -> Repartidor:
    """El repartidor de la cuenta con sesión. El rol solo no basta: sin su fila no tiene ruta."""
    try:
        auth_user_id = UUID(str(current_user.get("user_id")))
    except ValueError:
        auth_user_id = None
    repartidor = None
    if auth_user_id is not None:
        result = await session.execute(
            select(Repartidor).where(Repartidor.auth_user_id == auth_user_id, Repartidor.activo.is_(True))
        )
        repartidor = result.scalar_one_or_none()
    if repartidor is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta no está registrada como repartidor. Avísale a la administración.",
        )
    return repartidor


# ---------- Administración ----------


@router_admin.get("/repartidores", response_model=list[RepartidorOut])
async def listar_repartidores():
    async with SessionLocal() as session:
        result = await session.execute(
            select(Repartidor).where(Repartidor.activo.is_(True)).order_by(Repartidor.nombre)
        )
        return [RepartidorOut(id=r.id, nombre=r.nombre) for r in result.scalars()]


@router_admin.post("/rutas/asignar")
async def asignar_ruta(datos: AsignarRutaRequest, current_user: dict = Depends(require_admin)):
    """Asigna las paradas por entregar a un repartidor, que las verá en su celular."""
    if not datos.pedido_ids:
        return {"asignados": 0}
    async with SessionLocal() as session:
        if datos.repartidor_id is not None:
            repartidor = await session.get(Repartidor, datos.repartidor_id)
            if repartidor is None or not repartidor.activo:
                raise HTTPException(status_code=404, detail="Repartidor no encontrado")

        result = await session.execute(
            select(Pedido).where(Pedido.id.in_(datos.pedido_ids)).with_for_update()
        )
        pedidos = result.scalars().all()
        faltantes = sorted(set(datos.pedido_ids) - {p.id for p in pedidos})
        if faltantes:
            raise HTTPException(status_code=404, detail=f"Pedido(s) no encontrado(s): {faltantes}")
        fuera = sorted(p.id for p in pedidos if p.estado not in ESTADOS_POR_ENTREGAR)
        if fuera:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Solo se asignan pedidos confirmados o en despacho. No se pudo con: {fuera}",
            )

        cambios = []
        for pedido in pedidos:
            antes = construir_snapshot(pedido)
            pedido.repartidor_id = datos.repartidor_id
            pedido.actualizado_en = datetime.utcnow()
            cambios.append((pedido, antes))
        await session.commit()

        for pedido, antes in cambios:
            await registrar_auditoria(
                usuario=current_user.get("email"),
                entidad="pedido",
                entidad_id=pedido.id,
                accion="asignar_repartidor",
                antes=antes,
                despues=construir_snapshot(pedido),
            )
    return {"asignados": len(cambios)}


@router_admin.get("/combustible", response_model=list[CargaCombustibleOut])
async def combustible_del_periodo(desde: date = Query(...), hasta: date = Query(...)):
    if desde > hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha 'desde' no puede ser posterior a 'hasta'",
        )
    dia = dia_chile(CargaCombustible.creado_en)
    async with SessionLocal() as session:
        result = await session.execute(
            select(CargaCombustible)
            .where(dia >= desde, dia <= hasta)
            .order_by(CargaCombustible.creado_en, CargaCombustible.id)
        )
        return result.scalars().all()


@router_admin.get("/rutas/combustible", response_model=list[CombustibleRepartidorOut])
async def combustible_de_hoy():
    """Lo informado hoy por cada repartidor; los que no han informado no aparecen."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(CargaCombustible, Repartidor.nombre)
            .join(Repartidor, Repartidor.id == CargaCombustible.repartidor_id)
            .where(dia_chile(CargaCombustible.creado_en) == hoy_chile())
            .order_by(Repartidor.nombre, CargaCombustible.creado_en, CargaCombustible.id)
        )
        por_repartidor: dict[str, list[CargaCombustible]] = {}
        for carga, nombre in result.all():
            por_repartidor.setdefault(nombre, []).append(carga)
    return [
        CombustibleRepartidorOut(
            repartidor=nombre,
            registros=[CargaCombustibleOut.model_validate(c) for c in cargas],
        )
        for nombre, cargas in por_repartidor.items()
    ]


# ---------- Repartidor ----------


@router_repartidor.get("/ruta", response_model=RutaRepartidorOut)
async def ruta_del_repartidor(current_user: dict = Depends(require_repartidor)):
    """Sus paradas por entregar y las que ya resolvió hoy, en el orden de la ruta."""
    async with SessionLocal() as session:
        repartidor = await _repartidor_actual(session, current_user)
        result = await session.execute(
            select(Pedido)
            .where(
                Pedido.repartidor_id == repartidor.id,
                Pedido.orden_entrega.is_not(None),
                or_(
                    Pedido.estado.in_(ESTADOS_POR_ENTREGAR),
                    and_(
                        Pedido.estado.in_(ESTADOS_RESUELTOS),
                        dia_chile(Pedido.resultado_en) == hoy_chile(),
                    ),
                ),
            )
            .options(
                selectinload(Pedido.cliente),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
            .order_by(Pedido.viaje.asc().nulls_last(), Pedido.orden_entrega, Pedido.id)
        )
        paradas = [_parada_out(p) for p in result.scalars()]
    return RutaRepartidorOut(repartidor=RepartidorNombreOut(nombre=repartidor.nombre), paradas=paradas)


def _marcar_entregado(pedido: Pedido, datos: ResultadoParadaRequest) -> None:
    if datos.medio_pago is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Falta el método de pago.")
    detalles = {d.id: d for d in pedido.detalles}
    enviadas = {l.detalle_id: l.cantidad_entregada for l in datos.lineas or []}
    ajenas = sorted(set(enviadas) - set(detalles))
    if ajenas:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Detalle(s) que no son de este pedido: {ajenas}",
        )
    cantidades = {i: enviadas.get(i, d.cantidad_solicitada) for i, d in detalles.items()}
    if not any(cantidades.values()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No se entregó ninguna unidad: marca No entregado.",
        )
    for i, cantidad in cantidades.items():
        detalles[i].cantidad_entregada = cantidad
    cerrar_entrega(pedido)
    pedido.medio_pago = datos.medio_pago


def _marcar_no_entregado(session: AsyncSession, pedido: Pedido, repartidor: Repartidor) -> None:
    ahora = datetime.utcnow()
    pedido.estado = EstadoPedido.NO_ENTREGADO
    pedido.medio_pago = None
    pedido.resultado_en = ahora
    pedido.actualizado_en = ahora
    session.add(
        IntentoNoEntregado(
            pedido_id=pedido.id,
            repartidor_id=repartidor.id,
            viaje=pedido.viaje,
            orden_entrega=pedido.orden_entrega,
            total=pedido.total,
            creado_en=ahora,
        )
    )


async def _deshacer_resultado(session: AsyncSession, pedido: Pedido) -> None:
    if pedido.estado == EstadoPedido.NO_ENTREGADO:
        result = await session.execute(
            select(IntentoNoEntregado)
            .where(IntentoNoEntregado.pedido_id == pedido.id)
            .order_by(IntentoNoEntregado.id.desc())
            .limit(1)
        )
        intento = result.scalar_one_or_none()
        if intento is not None:
            await session.delete(intento)
    for detalle in pedido.detalles:
        detalle.cantidad_entregada = None
    pedido.total = total_pedido(pedido)
    pedido.estado = EstadoPedido.EN_DESPACHO
    pedido.medio_pago = None
    pedido.resultado_en = None
    pedido.entrega_ajustada = False
    pedido.actualizado_en = datetime.utcnow()


@router_repartidor.post("/paradas/{id}/resultado", response_model=ParadaOut)
async def registrar_resultado(
    id: int, datos: ResultadoParadaRequest, current_user: dict = Depends(require_repartidor)
):
    async with SessionLocal() as session:
        repartidor = await _repartidor_actual(session, current_user)
        # with_for_update: dos toques seguidos no registran dos veces.
        result = await session.execute(
            select(Pedido)
            .where(Pedido.id == id)
            .options(
                selectinload(Pedido.cliente),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
            .with_for_update(of=Pedido)
        )
        pedido = result.scalar_one_or_none()
        if pedido is None or pedido.repartidor_id != repartidor.id or pedido.orden_entrega is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Esta parada no está en tu ruta.")

        snapshot_antes = construir_snapshot(pedido)
        if datos.resultado is None:
            marcado_hoy = (
                pedido.estado in ESTADOS_RESUELTOS
                and pedido.resultado_en is not None
                and _dia_chile_python(pedido.resultado_en) == hoy_chile()
            )
            if not marcado_hoy:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Solo se puede corregir lo que marcaste hoy en esta parada.",
                )
            await _deshacer_resultado(session, pedido)
        elif pedido.estado not in ESTADOS_POR_ENTREGAR:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esta parada ya está marcada. Usa Corregir si fue un error.",
            )
        elif datos.resultado == "entregado":
            _marcar_entregado(pedido, datos)
        else:
            _marcar_no_entregado(session, pedido, repartidor)

        await session.commit()

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="pedido",
            entidad_id=pedido.id,
            accion="corregir_resultado" if datos.resultado is None else "registrar_resultado",
            antes=snapshot_antes,
            despues=construir_snapshot(pedido),
        )
        return _parada_out(pedido)


@router_repartidor.get("/combustible", response_model=list[CargaCombustibleOut])
async def mi_combustible_de_hoy(current_user: dict = Depends(require_repartidor)):
    async with SessionLocal() as session:
        repartidor = await _repartidor_actual(session, current_user)
        result = await session.execute(
            select(CargaCombustible)
            .where(
                CargaCombustible.repartidor_id == repartidor.id,
                dia_chile(CargaCombustible.creado_en) == hoy_chile(),
            )
            .order_by(CargaCombustible.creado_en, CargaCombustible.id)
        )
        return result.scalars().all()


@router_repartidor.post("/combustible", response_model=CargaCombustibleOut, status_code=status.HTTP_201_CREATED)
async def informar_combustible(datos: CargaCombustibleIn, current_user: dict = Depends(require_repartidor)):
    if datos.cargo and (datos.monto is None or datos.medio_pago is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Falta el monto o el método de pago.",
        )
    if not datos.cargo and (datos.monto is not None or datos.medio_pago is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Si no cargaste, no corresponde informar monto ni método de pago.",
        )
    async with SessionLocal() as session:
        repartidor = await _repartidor_actual(session, current_user)
        carga = CargaCombustible(
            repartidor_id=repartidor.id,
            cargo=datos.cargo,
            monto=datos.monto,
            medio_pago=datos.medio_pago,
            creado_en=datetime.utcnow(),
        )
        session.add(carga)
        await session.commit()
        return carga


@router_repartidor.delete("/combustible/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def quitar_combustible(id: int, current_user: dict = Depends(require_repartidor)):
    """Solo lo que informó él mismo hoy: los días anteriores ya son parte de los reportes."""
    async with SessionLocal() as session:
        repartidor = await _repartidor_actual(session, current_user)
        result = await session.execute(
            select(CargaCombustible).where(
                CargaCombustible.id == id,
                CargaCombustible.repartidor_id == repartidor.id,
                dia_chile(CargaCombustible.creado_en) == hoy_chile(),
            )
        )
        carga = result.scalar_one_or_none()
        if carga is None:
            raise HTTPException(status_code=404, detail="Carga no encontrada")
        await session.delete(carga)
        await session.commit()
