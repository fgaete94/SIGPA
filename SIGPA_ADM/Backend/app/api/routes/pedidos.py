from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.cliente import Cliente
from app.models.auditoria import Auditoria
from app.models.detalle_pedido import DetallePedido
from app.models.enums import DiaSemana, EstadoPedido
from app.models.pedido import Pedido
from app.models.producto import Producto
from app.schemas.pedido import (
    CambiarEstadoRequest,
    CorregirCoordenadasRequest,
    DetallePedidoLineaOut,
    HistorialEstadoOut,
    PedidoCreate,
    PedidoDetalleOut,
    PedidoOut,
    PedidoUpdate,
    RegistrarEntregaRequest,
)
from app.services.auditoria_service import construir_snapshot, registrar_auditoria

router = APIRouter(tags=["pedidos"], dependencies=[Depends(get_current_user)])

TRANSICIONES_DESPACHO = {
    EstadoPedido.CONFIRMADO: {EstadoPedido.EN_DESPACHO},
    EstadoPedido.EN_DESPACHO: {EstadoPedido.CONFIRMADO},
}

def _nombre_completo_cliente(cliente: Cliente) -> str:
    apellidos = " ".join(
        parte for parte in (cliente.apellido_paterno, cliente.apellido_materno) if parte
    )
    return f"{cliente.nombre} {apellidos}" if apellidos else cliente.nombre

def _pedido_a_out(pedido: Pedido) -> PedidoOut:
    return PedidoOut(
        id=pedido.id,
        cliente_id=pedido.cliente_id,
        cliente_nombre=_nombre_completo_cliente(pedido.cliente) if pedido.cliente else None,
        cliente_telefono=pedido.cliente.telefono if pedido.cliente else None,
        comuna_nombre=(
            pedido.cliente.comuna.nombre
            if pedido.cliente and pedido.cliente.comuna
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
        orden_entrega=pedido.orden_entrega,
        motivo_revision_direccion=pedido.motivo_revision_direccion,
    )


def _pedido_a_detalle_out(pedido: Pedido) -> PedidoDetalleOut:
    lineas = [
        DetallePedidoLineaOut(
            cantidad_solicitada=detalle.cantidad_solicitada,
            cantidad_entregada=detalle.cantidad_entregada,
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
            .options(selectinload(Pedido.cliente).selectinload(Cliente.comuna))
            .where(
                Pedido.estado.in_(
                    [EstadoPedido.CONFIRMADO, EstadoPedido.EN_DESPACHO, EstadoPedido.ENTREGADO]
                )
            )
        )

        if comuna_id is not None:
            query = query.where(Cliente.comuna_id == comuna_id)

        if dia_reparto is not None:
            query = query.where(Cliente.dia_reparto == dia_reparto)

        # Agrupado visualmente por día de reparto y comuna, y dentro de cada
        # grupo, los pedidos más recientes primero
        query = query.order_by(Cliente.dia_reparto, Cliente.comuna_id, Pedido.creado_en.desc())

        result = await session.execute(query)
        pedidos = result.scalars().all()

        return [_pedido_a_out(pedido) for pedido in pedidos]

    
@router.get("", response_model=list[PedidoOut])
async def listar_pedidos(
    estado: EstadoPedido | None = Query(None),
    fecha_desde: date | None = Query(None),
    fecha_hasta: date | None = Query(None),
    revision_direccion: bool | None = Query(None),
):
    async with SessionLocal() as session:
        query = select(Pedido).options(
            selectinload(Pedido.cliente).selectinload(Cliente.comuna)
            )

        if estado is not None:
            query = query.where(Pedido.estado == estado)
        if fecha_desde is not None:
            query = query.where(Pedido.creado_en >= datetime.combine(fecha_desde, time.min))
        if fecha_hasta is not None:
            query = query.where(Pedido.creado_en <= datetime.combine(fecha_hasta, time.max))
        # Historia #107: true = solo pedidos con dirección por revisar,
        # false = solo los que no tienen revisión pendiente
        if revision_direccion is True:
            query = query.where(Pedido.motivo_revision_direccion.is_not(None))
        elif revision_direccion is False:
            query = query.where(Pedido.motivo_revision_direccion.is_(None))

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
                selectinload(Pedido.cliente).selectinload(Cliente.comuna),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
        )
        pedido = result.scalar_one_or_none()

        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        return _pedido_a_detalle_out(pedido)


@router.post("", response_model=PedidoDetalleOut, status_code=status.HTTP_201_CREATED)
async def crear_pedido(datos: PedidoCreate, current_user: dict = Depends(get_current_user)):
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
            total += linea.cantidad_solicitada * precio_unitario
            detalles.append(
                DetallePedido(
                    producto_id=linea.producto_id,
                    cantidad_solicitada=linea.cantidad_solicitada,
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
                selectinload(Pedido.cliente).selectinload(Cliente.comuna),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
        )
        pedido_completo = result.scalar_one()

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="pedido",
            entidad_id=pedido_completo.id,
            accion="crear",
            antes=None,
            despues=construir_snapshot(pedido_completo),
        )

        return _pedido_a_detalle_out(pedido_completo)


@router.patch("/{id}", response_model=PedidoOut)
async def actualizar_pedido(
    id: int, datos: PedidoUpdate, current_user: dict = Depends(get_current_user)
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido).where(Pedido.id == id)
            .options(selectinload(Pedido.cliente).selectinload(Cliente.comuna))
        )
        pedido = result.scalar_one_or_none()

        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        snapshot_antes = construir_snapshot(pedido)

        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(pedido, campo, valor)

        pedido.actualizado_en = datetime.utcnow()

        await session.commit()

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="pedido",
            entidad_id=pedido.id,
            accion="actualizar",
            antes=snapshot_antes,
            despues=construir_snapshot(pedido),
        )

        return _pedido_a_out(pedido)


@router.post("/{id}/entrega", response_model=PedidoDetalleOut)
async def registrar_entrega(id: int, datos: RegistrarEntregaRequest):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido)
            .where(Pedido.id == id)
            .options(
                selectinload(Pedido.cliente).selectinload(Cliente.comuna),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
        )
        pedido = result.scalar_one_or_none()

        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        detalles_por_id = {detalle.id: detalle for detalle in pedido.detalles}

        faltantes = [
            linea.detalle_id for linea in datos.lineas if linea.detalle_id not in detalles_por_id
        ]
        if faltantes:
            raise HTTPException(
                status_code=404,
                detail=f"Detalle(s) de pedido no encontrado(s) en este pedido: {faltantes}",
            )

        for linea in datos.lineas:
            detalles_por_id[linea.detalle_id].cantidad_entregada = linea.cantidad_entregada

        pedido.total = sum(
            (detalle.cantidad_entregada or 0) * float(detalle.precio_unitario)
            for detalle in pedido.detalles
        )
        pedido.estado = EstadoPedido.ENTREGADO
        pedido.actualizado_en = datetime.utcnow()

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se pudo registrar la entrega por un conflicto de datos",
            )

        result = await session.execute(
            select(Pedido)
            .where(Pedido.id == pedido.id)
            .options(
                selectinload(Pedido.cliente).selectinload(Cliente.comuna),
                selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
            )
        )
        pedido_actualizado = result.scalar_one()

        return _pedido_a_detalle_out(pedido_actualizado)


@router.post("/{id}/estado", response_model=PedidoOut)
async def cambiar_estado_pedido(
    id: int, datos: CambiarEstadoRequest, current_user: dict = Depends(get_current_user)
):
    async with SessionLocal() as session:
        # with_for_update bloquea la fila mientras se valida y cambia el estado,
        # para que dos clics simultáneos no se pisen
        result = await session.execute(
            select(Pedido)
            .where(Pedido.id == id)
            .options(
                selectinload(Pedido.cliente).selectinload(Cliente.comuna)
            )
            .with_for_update(of=Pedido)
        )
        pedido = result.scalar_one_or_none()

        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        # Si el estado actual no está en la tabla (pendiente, entregado, cancelado)
        # o el destino no está permitido, se rechaza con 409
        permitidos = TRANSICIONES_DESPACHO.get(pedido.estado, set())
        if datos.estado not in permitidos:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"No se puede cambiar un pedido de '{pedido.estado.value}' "
                    f"a '{datos.estado.value}' desde aquí"
                ),
            )

        snapshot_antes = construir_snapshot(pedido)

        pedido.estado = datos.estado
        pedido.actualizado_en = datetime.utcnow()

        await session.commit()

        # accion="cambiar_estado" la distingue del PATCH ("actualizar") para armar el historial
        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="pedido",
            entidad_id=pedido.id,
            accion="cambiar_estado",
            antes=snapshot_antes,
            despues=construir_snapshot(pedido),
        )

        return _pedido_a_out(pedido)

@router.post("/{id}/coordenadas", response_model=PedidoOut)
async def corregir_coordenadas_pedido(
    id: int, datos: CorregirCoordenadasRequest, current_user: dict = Depends(get_current_user)
):
    """
    Historia #113: corrige a mano las coordenadas de un pedido con dirección
    por revisar. Limpia la marca de revisión, y como el pedido ya trae
    latitud y longitud, n8n no lo vuelve a geocodificar en la próxima ruta.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            select(Pedido)
            .where(Pedido.id == id)
            .options(
                selectinload(Pedido.cliente).selectinload(Cliente.comuna)
            )
            .with_for_update(of=Pedido)
        )
        pedido = result.scalar_one_or_none()

        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        if pedido.motivo_revision_direccion is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El pedido no tiene una dirección pendiente de revisión",
            )

        snapshot_antes = construir_snapshot(pedido)

        pedido.latitud = datos.latitud
        pedido.longitud = datos.longitud
        pedido.motivo_revision_direccion = None
        pedido.actualizado_en = datetime.utcnow()

        await session.commit()

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="pedido",
            entidad_id=pedido.id,
            accion="corregir_coordenadas",
            antes=snapshot_antes,
            despues=construir_snapshot(pedido),
        )

        return _pedido_a_out(pedido)

@router.get("/{id}/historial", response_model=list[HistorialEstadoOut])
async def historial_estados_pedido(id: int):
    """
    Historia #70: historial de cambios de estado de un pedido, del más antiguo
    al más reciente. Se arma desde la tabla auditoria comparando el estado de
    la foto "antes" con la foto "despues" de cada registro.
    """
    async with SessionLocal() as session:
        pedido = await session.get(Pedido, id)
        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        result = await session.execute(
            select(Auditoria)
            .where(Auditoria.entidad == "pedido", Auditoria.entidad_id == id)
            .order_by(Auditoria.creado_en, Auditoria.id)
        )
        registros = result.scalars().all()

    historial = []
    for registro in registros:
        estado_anterior = (registro.antes or {}).get("estado")
        estado_nuevo = (registro.despues or {}).get("estado")

        # Se omiten los registros donde el estado no cambió
        # (por ejemplo, un PATCH que solo modificó la dirección)
        if estado_nuevo is None or estado_anterior == estado_nuevo:
            continue

        historial.append(
            HistorialEstadoOut(
                estado_anterior=estado_anterior,
                estado_nuevo=estado_nuevo,
                usuario=registro.usuario,
                accion=registro.accion,
                fecha=registro.creado_en,
            )
        )

    return historial