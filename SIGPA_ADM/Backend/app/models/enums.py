import enum


class DiaSemana(str, enum.Enum):
    """Coincide exactamente con el enum dia_semana ya creado en Supabase (5 valores, lunes a viernes)."""

    LUNES = "Lunes"
    MARTES = "Martes"
    MIERCOLES = "Miercoles"
    JUEVES = "Jueves"
    VIERNES = "Viernes"


class EstadoPedido(str, enum.Enum):
    """Placeholder del ciclo de vida de un pedido. Ajustar si el negocio usa otro flujo."""

    PENDIENTE = "pendiente"
    CONFIRMADO = "confirmado"
    EN_DESPACHO = "en_despacho"
    ENTREGADO = "entregado"
    CANCELADO = "cancelado"
