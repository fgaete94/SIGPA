"""
Importa todos los modelos para que queden registrados en Base.metadata
al hacer create_all() o al generar migraciones con Alembic.
"""

from app.models.cliente import Cliente
from app.models.cliente_producto_habitual import ClienteProductoHabitual
from app.models.comuna import Comuna
from app.models.detalle_pedido import DetallePedido
from app.models.enums import DiaSemana, EstadoPedido
from app.models.pedido import Pedido
from app.models.producto import Producto
from app.models.sector import Sector
from app.models.tipo_cliente import TipoCliente

__all__ = [
    "Cliente",
    "ClienteProductoHabitual",
    "Comuna",
    "DetallePedido",
    "DiaSemana",
    "EstadoPedido",
    "Pedido",
    "Producto",
    "Sector",
    "TipoCliente",
]
