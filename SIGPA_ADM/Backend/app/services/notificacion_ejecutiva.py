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
from datetime import datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Cliente
from app.models.mensaje_whatsapp import MensajeWhatsApp
from app.services.conversacion_bot_service import MARGEN_RELOJ_BD
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


async def ejecutiva_avisada_desde(telefono: str, desde: datetime | None) -> bool:
    """True si desde `desde` (UTC naive, inicio de la conversación) ya se le
    envió a la ejecutiva un aviso sobre este teléfono: plantilla o texto
    libre registrado como enviado en mensaje_whatsapp que menciona el número
    del cliente. Evita avisarle dos veces por el mismo cliente en la misma
    conversación aunque el proceso se haya reiniciado. Si la consulta falla
    devuelve False (más vale un aviso repetido que uno perdido).

    `desde` viene del reloj de la app y creado_en del de la BD: se cuenta
    desde MARGEN_RELOJ_BD antes, igual que es_primer_mensaje_conversacion
    (un aviso por este cliente minutos antes de abrir la conversación ya lo
    puso en conocimiento de la ejecutiva)."""
    if desde is None or not settings.EJECUTIVA_PHONE:
        return False
    try:
        async with SessionLocal() as session:
            encontrado = await session.scalar(
                select(MensajeWhatsApp.id)
                .where(
                    MensajeWhatsApp.telefono == settings.EJECUTIVA_PHONE,
                    MensajeWhatsApp.direccion == "saliente",
                    MensajeWhatsApp.estado == "enviado",
                    MensajeWhatsApp.creado_en >= desde - MARGEN_RELOJ_BD,
                    MensajeWhatsApp.contenido.contains(formatear_telefono(telefono)),
                    # El aviso de pedido grande tiene su propia marca (uno por
                    # pedido) y no cuenta como derivación de la conversación.
                    ~MensajeWhatsApp.contenido.startswith(PREFIJO_PEDIDO_GRANDE),
                )
                .limit(1)
            )
    except Exception:
        logger.exception("[notificacion_ejecutiva] Falló revisar avisos previos de %s", telefono)
        return False
    return encontrado is not None


# --------------------------------------------------------------------------
# Aviso de pedido grande (ver order_flow.umbrales_superados). Uno por pedido:
# todo lo que se registra en mensaje_whatsapp empieza con "Pedido grande #N:"
# (la marca propia), que ejecutiva_avisada_desde ignora, así que ni bloquea
# ni lo bloquea el aviso único por conversación de la derivación.
# --------------------------------------------------------------------------

PREFIJO_PEDIDO_GRANDE = "Pedido grande #"
MAX_CARACTERES_DETALLE = 200

# Copia para el historial de la plantilla de pedido grande ({{1}} nombre,
# {{2}} teléfono, {{3}} detalle, {{4}} motivo). Meta arma el mensaje con su
# copia aprobada.
_CUERPO_PLANTILLA_PEDIDO_GRANDE = (
    "Hola, {} ({}) confirmó un pedido grande por el WhatsApp de pedidos. Detalle: {}. Motivo: {}. "
    "Por favor revísalo y coordina la entrega."
)

# Avisos de pedido grande ya enviados en este proceso (ids de pedido). La
# fuente durable es mensaje_whatsapp (ver aviso_pedido_grande_enviado).
_pedidos_grandes_avisados: set[int] = set()


def _una_linea(texto: str | None) -> str:
    return " ".join((texto or "").split())


def _recortar(texto: str, maximo: int = MAX_CARACTERES_DETALLE) -> str:
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip(" ,") + "…"


def parametros_pedido_grande(nombre: str, telefono: str, detalle: str, motivo: str) -> list[str]:
    """Las 4 variables de la plantilla, cada una en una sola línea (Meta
    rechaza saltos de línea en las variables) y el detalle recortado."""
    return [
        _una_linea(nombre) or "Cliente",
        formatear_telefono(telefono),
        _recortar(_una_linea(detalle)),
        _una_linea(motivo),
    ]


def texto_pedido_grande(pedido_id: int, parametros: list[str]) -> str:
    """Texto libre (y registro) del aviso, con la marca "Pedido grande #N:"."""
    nombre, telefono, detalle, motivo = parametros
    return (
        f"{PREFIJO_PEDIDO_GRANDE}{pedido_id}: {nombre} ({telefono}) confirmó un pedido grande por el "
        f"WhatsApp de pedidos. Detalle: {detalle}. Motivo: {motivo}. Por favor revísalo y coordina la entrega."
    )


async def aviso_pedido_grande_enviado(pedido_id: int) -> bool:
    """True si ya se avisó este pedido (marca en mensaje_whatsapp o en este
    proceso). Si la consulta falla devuelve lo que sabe el proceso."""
    if pedido_id in _pedidos_grandes_avisados:
        return True
    if not settings.EJECUTIVA_PHONE:
        return False
    try:
        async with SessionLocal() as session:
            encontrado = await session.scalar(
                select(MensajeWhatsApp.id)
                .where(
                    MensajeWhatsApp.telefono == settings.EJECUTIVA_PHONE,
                    MensajeWhatsApp.direccion == "saliente",
                    MensajeWhatsApp.estado == "enviado",
                    MensajeWhatsApp.contenido.startswith(f"{PREFIJO_PEDIDO_GRANDE}{pedido_id}:"),
                )
                .limit(1)
            )
    except Exception:
        logger.exception("[notificacion_ejecutiva] Falló revisar el aviso del pedido grande #%s", pedido_id)
        return False
    return encontrado is not None


async def avisar_pedido_grande(
    pedido_id: int, nombre: str, telefono: str, detalle: str, motivo: str, send_text
) -> bool:
    """Avisa a la ejecutiva de un pedido grande, una sola vez por pedido: con
    la plantilla WHATSAPP_TEMPLATE_PEDIDO_GRANDE_NAME si está configurada y,
    si no o si Meta la rechaza, con texto libre (send_text, el
    send_whatsapp_message de quien llama). True si se envió (o ya se había
    enviado). Nunca lanza: un error se loguea y devuelve False."""
    try:
        if await aviso_pedido_grande_enviado(pedido_id):
            logger.info("[notificacion_ejecutiva] Pedido grande #%s ya avisado: no se reenvía", pedido_id)
            return True
        parametros = parametros_pedido_grande(nombre, telefono, detalle, motivo)
        texto = texto_pedido_grande(pedido_id, parametros)
        nombre_plantilla = settings.WHATSAPP_TEMPLATE_PEDIDO_GRANDE_NAME.strip()
        enviado = False
        if nombre_plantilla:
            respuesta = await send_whatsapp_template(
                to=settings.EJECUTIVA_PHONE,
                template_name=nombre_plantilla,
                language_code=settings.WHATSAPP_TEMPLATE_PEDIDO_GRANDE_LANG.strip(),
                body_params=parametros,
                texto_registro=f"{PREFIJO_PEDIDO_GRANDE}{pedido_id}: " + _CUERPO_PLANTILLA_PEDIDO_GRANDE.format(*parametros),
            )
            enviado = respuesta is not None
            if not enviado:
                logger.warning(
                    "[notificacion_ejecutiva] Meta rechazó la plantilla %s del pedido grande #%s: se envía texto libre",
                    nombre_plantilla,
                    pedido_id,
                )
        if not enviado:
            enviado = await send_text(to=settings.EJECUTIVA_PHONE, message=texto) is not None
        if enviado:
            _pedidos_grandes_avisados.add(pedido_id)
        else:
            logger.error("[notificacion_ejecutiva] No se pudo avisar el pedido grande #%s", pedido_id)
        return enviado
    except Exception:
        logger.exception("[notificacion_ejecutiva] Falló avisar el pedido grande #%s", pedido_id)
        return False
