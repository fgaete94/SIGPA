"""Reglas compartidas al cerrar una entrega, desde el panel (POST /pedidos/{id}/entrega)
o desde el celular del repartidor (POST /repartidor/paradas/{id}/resultado)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func

from app.models.enums import EstadoPedido
from app.models.pedido import Pedido

ZONA_HORARIA = "America/Santiago"


def hoy_chile() -> date:
    return datetime.now(ZoneInfo(ZONA_HORARIA)).date()


def dia_chile(columna):
    """Día en hora de Chile de una columna guardada en UTC sin zona."""
    return cast(func.timezone(ZONA_HORARIA, func.timezone("UTC", columna)), Date)


def total_pedido(pedido: Pedido) -> float:
    """El total por lo que se pidió, antes de registrar lo entregado."""
    return sum(d.cantidad_solicitada * float(d.precio_unitario) for d in pedido.detalles)


def cerrar_entrega(pedido: Pedido) -> None:
    """Deja el pedido entregado, con el total de lo entregado: eso es lo que suma en Ventas.
    Una línea sin cantidad registrada cuenta como 0."""
    pedido.total = sum(
        (detalle.cantidad_entregada or 0) * float(detalle.precio_unitario)
        for detalle in pedido.detalles
    )
    pedido.entrega_ajustada = any(
        (detalle.cantidad_entregada or 0) != detalle.cantidad_solicitada
        for detalle in pedido.detalles
    )
    ahora = datetime.utcnow()
    pedido.estado = EstadoPedido.ENTREGADO
    pedido.resultado_en = ahora
    pedido.actualizado_en = ahora
