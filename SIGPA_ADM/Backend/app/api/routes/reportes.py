from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.detalle_pedido import DetallePedido
from app.models.enums import EstadoPedido
from app.models.pedido import Pedido
from app.models.producto import Producto

router = APIRouter(tags=["reportes"], dependencies=[Depends(get_current_user)])

ZONA_HORARIA = "America/Santiago"


class ProductoVendidoOut(BaseModel):
    producto_id: int
    producto_nombre: str
    unidades: int
    monto: float
    pedidos: int


def consulta_productos(desde: date | None, hasta: date | None, contar: str):
    """Ranking de productos (aparte para poder revisar el SQL que genera)."""
    # En un pedido entregado cuenta lo entregado (#99); en los demás, lo pedido.
    unidades = func.coalesce(DetallePedido.cantidad_entregada, DetallePedido.cantidad_solicitada)
    monto = unidades * DetallePedido.precio_unitario
    # creado_en se guarda en UTC sin zona: el día se toma en hora de Chile, igual que el panel.
    dia = cast(func.timezone(ZONA_HORARIA, func.timezone("UTC", Pedido.creado_en)), Date)
    query = (
        select(
            Producto.id,
            Producto.nombre,
            func.sum(unidades).label("unidades"),
            func.sum(monto).label("monto"),
            func.count(func.distinct(Pedido.id)).label("pedidos"),
        )
        .select_from(DetallePedido)
        .join(Pedido, Pedido.id == DetallePedido.pedido_id)
        .join(Producto, Producto.id == DetallePedido.producto_id)
        .group_by(Producto.id, Producto.nombre)
        .having(func.sum(unidades) > 0)
        .order_by(func.sum(unidades).desc(), func.sum(monto).desc(), Producto.nombre)
    )
    if contar == "entregados":
        query = query.where(Pedido.estado == EstadoPedido.ENTREGADO)
    else:
        query = query.where(Pedido.estado != EstadoPedido.CANCELADO)
    if desde:
        query = query.where(dia >= desde)
    if hasta:
        query = query.where(dia <= hasta)
    return query


@router.get("/productos", response_model=list[ProductoVendidoOut])
async def productos_mas_vendidos(
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    contar: Literal["entregados", "pedidos"] = Query("entregados"),
):
    """
    Historia #72: ranking de productos de mayor a menor cantidad de unidades en
    un rango de fechas (día del pedido en hora de Chile, ambos extremos
    incluidos; sin fechas, todo el historial).

    - contar=entregados (por defecto): lo vendido, solo pedidos entregados.
    - contar=pedidos: lo pedido, todos los pedidos menos los cancelados (sirve
      para planificar inventario aunque todavía no se registren las entregas).
    """
    if desde and hasta and desde > hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha 'desde' no puede ser posterior a 'hasta'",
        )
    async with SessionLocal() as session:
        filas = (await session.execute(consulta_productos(desde, hasta, contar))).all()
    return [
        ProductoVendidoOut(
            producto_id=fila.id,
            producto_nombre=fila.nombre,
            unidades=int(fila.unidades),
            monto=float(fila.monto),
            pedidos=fila.pedidos,
        )
        for fila in filas
    ]
