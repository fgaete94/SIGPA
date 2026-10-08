"""Texto de la notificación a la ejecutiva cuando el bot le deriva un cliente
(mensaje espontáneo sin conversación activa, ver whatsapp.py, o teléfono en
más de un cliente, ver order_flow.py).

El remitente se clasifica por su teléfono normalizado (cliente_lookup) como
cliente existente, nuevo o teléfono duplicado, y la notificación dice quién
es. Aquí solo se arma el texto: el envío lo hace quien llama.
"""

import logging

from app.core.database import SessionLocal
from app.models import Cliente
from app.services.cliente_lookup import buscar_clientes_por_telefono, normalizar_telefono
from app.services.horario_atencion import MARCA_FUERA_DE_HORARIO

logger = logging.getLogger(__name__)

CLIENTE_EXISTENTE = "existente"
CLIENTE_NUEVO = "nuevo"
TELEFONO_DUPLICADO = "duplicado"

_CIERRE = "Por favor revisa y continúa la conversación."

_TEXTOS = {
    CLIENTE_EXISTENTE: "Hola, el cliente {nombres} ({telefono}) escribió al WhatsApp de pedidos. " + _CIERRE,
    CLIENTE_NUEVO: "Hola, un cliente nuevo ({telefono}) escribió al WhatsApp de pedidos. " + _CIERRE,
    TELEFONO_DUPLICADO: (
        "Hola, el teléfono {telefono} está asociado a más de un cliente ({nombres}) "
        "y escribió al WhatsApp de pedidos. " + _CIERRE
    ),
}

# Si no se pudo clasificar al remitente (ej. falla de la BD).
_TEXTO_GENERICO = "Hola, un cliente ({telefono}) escribió al WhatsApp de pedidos. " + _CIERRE


def formatear_telefono(telefono: str | None) -> str:
    """"+" y los dígitos con código de país (ej. +56957721243), venga como
    venga el teléfono."""
    normalizado = normalizar_telefono(telefono)
    return f"+{normalizado}" if normalizado else (telefono or "")


def nombre_completo(cliente: Cliente) -> str:
    """Nombre y apellidos que tenga el cliente, sin espacios dobles."""
    partes = (cliente.nombre, cliente.apellido_paterno, cliente.apellido_materno)
    return " ".join(" ".join(parte for parte in partes if parte).split())


def clasificar_clientes(clientes: list[Cliente]) -> tuple[str, list[str]]:
    """(tipo de remitente, nombres completos) según los clientes que
    coinciden con su teléfono."""
    nombres = [nombre_completo(cliente) for cliente in clientes]
    if not clientes:
        return CLIENTE_NUEVO, nombres
    if len(clientes) == 1:
        return CLIENTE_EXISTENTE, nombres
    return TELEFONO_DUPLICADO, nombres


async def clasificar_remitente(telefono: str) -> tuple[str | None, list[str]]:
    """Busca al remitente por teléfono y lo clasifica. Si la búsqueda falla
    devuelve (None, []) para que se envíe el texto genérico: la notificación
    no se bloquea por un error de BD."""
    try:
        async with SessionLocal() as session:
            clientes = await buscar_clientes_por_telefono(session, telefono)
    except Exception:
        logger.exception("[notificacion_ejecutiva] Falló buscar al cliente del teléfono %s", telefono)
        return None, []
    return clasificar_clientes(clientes)


def texto_notificacion_ejecutiva(
    tipo_cliente: str | None,
    nombres: list[str],
    telefono: str,
    fuera_de_horario: bool,
) -> str:
    """Texto que recibe la ejecutiva. tipo_cliente None (no se pudo
    clasificar) usa el texto genérico. Fuera de horario se agrega la marca
    "(fuera de horario)" al final."""
    plantilla = _TEXTOS.get(tipo_cliente, _TEXTO_GENERICO)
    texto = plantilla.format(telefono=formatear_telefono(telefono), nombres=", ".join(nombres))
    if fuera_de_horario:
        texto = f"{texto} {MARCA_FUERA_DE_HORARIO}"
    return texto
