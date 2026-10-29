"""Texto de la notificación a la ejecutiva cuando el bot le deriva un cliente
(mensaje espontáneo sin conversación activa, ver whatsapp.py, o teléfono en
más de un cliente, ver order_flow.py).

El remitente se clasifica por su teléfono normalizado (cliente_lookup) como
cliente existente, nuevo o teléfono duplicado, y la notificación dice quién
es. Se envía con la plantilla aprobada por Meta (#152) si está configurada
(enviar_plantilla_ejecutiva); si no, o si Meta la rechaza, quien llama envía
el texto libre (texto_notificacion_ejecutiva).
"""

import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Cliente
from app.services.cliente_lookup import buscar_clientes_por_telefono, normalizar_telefono
from app.services.horario_atencion import MARCA_FUERA_DE_HORARIO
from app.services.whatsapp_client import send_whatsapp_template

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

# Cuerpo de la plantilla aviso_ejecutiva_cliente (Utility, es_CL) tal como
# se aprobó en Meta: {{1}} quién escribió, {{2}} el teléfono y {{3}} si fue
# en horario. Meta arma el mensaje con su copia; esta solo se usa para el
# historial de mensaje_whatsapp, así que si cambia en Meta hay que cambiarla aquí.
_CUERPO_PLANTILLA = (
    "Hola, {} escribió al WhatsApp de pedidos de Agua DM desde el número {}. Recibido {}. " + _CIERRE
)
_QUIEN_ESCRIBIO = {
    CLIENTE_EXISTENTE: "el cliente {nombres}",
    CLIENTE_NUEVO: "un cliente nuevo",
    TELEFONO_DUPLICADO: "un teléfono asociado a varios clientes ({nombres})",
}
_QUIEN_ESCRIBIO_GENERICO = "un cliente"


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


def parametros_plantilla_ejecutiva(
    tipo_cliente: str | None,
    nombres: list[str],
    telefono: str,
    fuera_de_horario: bool,
) -> list[str]:
    """Las 3 variables de la plantilla, con el mismo criterio que
    texto_notificacion_ejecutiva."""
    quien = _QUIEN_ESCRIBIO.get(tipo_cliente, _QUIEN_ESCRIBIO_GENERICO).format(nombres=", ".join(nombres))
    horario = "fuera de horario" if fuera_de_horario else "en horario de atención"
    return [quien.strip(), formatear_telefono(telefono), horario]


async def enviar_plantilla_ejecutiva(
    tipo_cliente: str | None,
    nombres: list[str],
    telefono: str,
    fuera_de_horario: bool,
) -> bool:
    """Avisa a la ejecutiva con la plantilla WHATSAPP_TEMPLATE_EJECUTIVA_NAME.
    Devuelve False sin enviar nada si no está configurada, y False si Meta
    rechazó el envío (ver logs): en ambos casos quien llama envía el texto
    libre, que llega si la ejecutiva escribió al bot en las últimas 24 h."""
    nombre_plantilla = settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME.strip()
    if not nombre_plantilla:
        return False
    parametros = parametros_plantilla_ejecutiva(tipo_cliente, nombres, telefono, fuera_de_horario)
    respuesta = await send_whatsapp_template(
        to=settings.EJECUTIVA_PHONE,
        template_name=nombre_plantilla,
        language_code=settings.WHATSAPP_TEMPLATE_EJECUTIVA_LANG.strip(),
        body_params=parametros,
        texto_registro=_CUERPO_PLANTILLA.format(*parametros),
    )
    if respuesta is None:
        logger.warning("[notificacion_ejecutiva] Meta rechazó la plantilla %s: se envía texto libre", nombre_plantilla)
        return False
    return True
