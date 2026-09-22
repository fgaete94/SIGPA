from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models.sector import Sector
from app.models.comuna import Comuna

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.cliente import Cliente
from app.models.detalle_pedido import DetallePedido
from app.models.enums import DiaSemana, EstadoPedido
from app.models.pedido import Pedido
from app.models.producto import Producto
from app.schemas.pedido import (
    DetallePedidoLineaOut,
    PedidoCreate,
    PedidoDetalleOut,
    PedidoOut,
    PedidoUpdate,
)

router = APIRouter(tags=["pedidos"], dependencies=[Depends(get_current_user)])


def _pedido_a_out(pedido: Pedido) -> PedidoOut:
    return PedidoOut(
        id=pedido.id,
        cliente_id=pedido.cliente_id,
        cliente_nombre=pedido.cliente.nombre if pedido.cliente else None,
        cliente_telefono=pedido.cliente.telefono if pedido.cliente else None,
        comuna_nombre=(
            pedido.cliente.sector.comuna.nombre
            if pedido.cliente and pedido.cliente.sector
            else None
        ),
        dia_reparto=pedido.cliente.dia_reparto if pedido.cliente else None,
        estado=pedido.estado,
        direccion_despacho=pedido.direccion_despacho,
        total=pedido.total,
        creado_en=pedido.creado_en,
        actualizado_en=pedido.actualizado_en,
        latitud=pedido.latitud,
        longitud=pedido.longitud,
    )


def _pedido_a_detalle_out(pedido: Pedido) -> PedidoDetalleOut:
    lineas = [
        DetallePedidoLineaOut(
            cantidad=detalle.cantidad,
            precio_unitario=detalle.precio_unitario,
            producto_nombre=detalle.producto.nombre,
        )
        for detalle in pedido.detalles
    ]
    return PedidoDetalleOut(**_pedido_a_out(pedido).model_dump(), lineas=lineas)


@router.get("/despacho", response_model=list[PedidoOut])
async def listar_pedidos_despacho(
    comuna_id: int | None = Query(None),
    dia_reparto: DiaSemana | None = Query(None),
):
    """
    Historia #67: listado de pedidos confirmados para organizar el despacho,
    agrupados por comuna y ordenados por día de reparto configurado.

    Solo incluye pedidos en estado confirmado, en_despacho o entregado
    (los "pendiente" de confirmación y los "cancelado" no forman parte
    de este listado, según la historia de usuario).

    Filtros opcionales por query params:
    - comuna_id: filtra a los pedidos de clientes en esa comuna
    - dia_reparto: filtra a los pedidos de clientes con ese día de reparto.
    """
    async with SessionLocal() as session:
        query = (
            select(Pedido)
            .join(Cliente, Pedido.cliente_id == Cliente.id)
            # LEFT JOIN porque sector_id puede ser nulo en Cliente
            .join(Sector, Cliente.sector_id == Sector.id, isouter=True)
            .options(
                selectinload(Pedido.cliente)
                .selectinload(Cliente.sector)
                .selectinload(Sector.comuna)
            )
            .where(
                Pedido.estado.in_(
                    [EstadoPedido.CONFIRMADO, EstadoPedido.EN_DESPACHO, EstadoPedido.ENTREGADO]
                )
            )
        )

        if comuna_id is not None:
            query = query.where(Sector.comuna_id == comuna_id)

        if dia_reparto is not None:
            query = query.where(Cliente.dia_reparto == dia_reparto)

        # Agrupado visualmente por día de reparto y comuna, y dentro de cada
        # grupo, los pedidos más recientes primero
        query = query.order_by(Cliente.dia_reparto, Sector.comuna_id, Pedido.creado_en.desc())

        result = await session.execute(query)
        pedidos = result.scalars().all()

        return [_pedido_a_out(pedido) for pedido in pedidos]

    
@router.get("", response_model=list[PedidoOut])
async def listar_pedidos(
    estado: EstadoPedido | None = Query(None),
    fecha_desde: date | None = Query(None),
    fecha_hasta: date | None = Query(None),
):
    async with SessionLocal() as session:
        query = select(Pedido).options(
            selectinload(Pedido.cliente).selectinload(Cliente.sector).selectinload(Sector.comuna)
            )

        if estado is not None:
            query = query.where(Pedido.estado == estado)
        if fecha_desde is not None:
            query = query.where(Pedido.creado_en >= datetime.combine(fecha_desde, time.min))
        if fecha_hasta is not None:
            query = query.where(Pedido.creado_en <= datetime.combine(fecha_hasta, time.max))

        result = await session.execute(query.order_by(Pedido.creado_en.desc()))
        pedidos = result.scalars().all()

        return [_pedido_a_out(pedido) for pedido in pedidos]


@router.get("/{id}", response_model=PedidoDetalleOut)
async def obtener_pedido(id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido)
            .where(Pedido.id == id)
            .options(
                selectinload(Pedido.cliente).selectinload(Cliente.sector).selectinload(Sector.comuna),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
        )
        pedido = result.scalar_one_or_none()

        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        return _pedido_a_detalle_out(pedido)


@router.post("", response_model=PedidoDetalleOut, status_code=status.HTTP_201_CREATED)
async def crear_pedido(datos: PedidoCreate):
    async with SessionLocal() as session:
        cliente = await session.get(Cliente, datos.cliente_id)
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        producto_ids = [linea.producto_id for linea in datos.lineas]
        result = await session.execute(
            select(Producto).where(Producto.id.in_(producto_ids))
            )
        productos_por_id = {producto.id: producto for producto in result.scalars().all()}

        faltantes = [pid for pid in producto_ids if pid not in productos_por_id]
        if faltantes:
            raise HTTPException(
                status_code=404,
                detail=f"Producto(s) no encontrado(s): {faltantes}",
            )

        detalles = []
        total = 0.0
        for linea in datos.lineas:
            precio_unitario = float(productos_por_id[linea.producto_id].precio_unitario)
            total += linea.cantidad * precio_unitario
            detalles.append(
                DetallePedido(
                    producto_id=linea.producto_id,
                    cantidad=linea.cantidad,
                    precio_unitario=precio_unitario,
                )
            )

        pedido = Pedido(
            cliente_id=datos.cliente_id,
            estado=datos.estado,
            direccion_despacho=datos.direccion_despacho,
            total=total,
            latitud=datos.latitud,
            longitud=datos.longitud,
            detalles=detalles,
        )

        session.add(pedido)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se pudo crear el pedido por un conflicto de datos",
            )

        result = await session.execute(
            select(Pedido)
            .where(Pedido.id == pedido.id)
            .options(
                selectinload(Pedido.cliente).selectinload(Cliente.sector).selectinload(Sector.comuna),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
        )
        pedido_completo = result.scalar_one()

        return _pedido_a_detalle_out(pedido_completo)


@router.patch("/{id}", response_model=PedidoOut)
async def actualizar_pedido(id: int, datos: PedidoUpdate):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido).where(Pedido.id == id)
            .options(selectinload(Pedido.cliente).selectinload(Cliente.sector).selectinload(Sector.comuna))
        )
        pedido = result.scalar_one_or_none()

        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(pedido, campo, valor)

        pedido.actualizado_en = datetime.utcnow()

        await session.commit()

        return _pedido_a_out(pedido)


