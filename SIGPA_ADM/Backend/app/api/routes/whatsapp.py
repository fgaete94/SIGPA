import logging
import re
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.core.config import MENSAJE_NOTIFICACION_EJECUTIVA, settings
from app.services.conversacion_bot_service import es_primer_mensaje_conversacion, esta_activa
from app.services.horario_atencion import (
    marcar_notificacion_ejecutiva,
    mensaje_derivacion_ejecutiva,
    presentacion_asistente,
)
from app.services.mensaje_whatsapp_service import registrar_mensaje_entrante
from app.services.order_flow import procesar_mensaje
from app.services.webhook_queue import encolar, reservar_wamid
from app.services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

MENSAJE_ERROR_GENERICO = (
    "Ocurrió un problema procesando tu mensaje, por favor intenta de nuevo o contacta a un ejecutivo."
)

MENSAJE_TIPO_NO_SOPORTADO = "Por ahora solo puedo procesar mensajes de texto o de ubicación."

# Saludo al inicio de la respuesta: "¡Hola, Felipe!", "¡Hola!", "Hola Felipe."
_PATRON_SALUDO_CERRADO = re.compile(r"^\s*(¡\s*hola\b[^!?\n]*!|hola\b[^!.?,\n]*[!.])\s*", re.IGNORECASE)
# Cualquier otro saludo inicial ("Hola Felipe, ¿qué ...?"), hasta la coma.
_PATRON_SALUDO_ABIERTO = re.compile(r"^\s*¡?\s*hola\b[^!.?,\n]*[,!.]?\s*", re.IGNORECASE)


def _presentar_asistente(texto: str) -> str:
    """Agrega la presentación del asistente ("Soy Lea, tu asistente
    virtual.") al primer mensaje de la conversación, sin duplicar el saludo:
    va justo después del "¡Hola...!" con que empiece el texto, o con un
    "¡Hola!" adelante si no empieza con saludo. Si el texto ya nombra al
    asistente no se toca."""
    if re.search(rf"\b{re.escape(settings.NOMBRE_ASISTENTE)}\b", texto):
        return texto
    presentacion = presentacion_asistente()
    cerrado = _PATRON_SALUDO_CERRADO.match(texto)
    if cerrado:
        return f"{cerrado.group(1)} {presentacion} {texto[cerrado.end():]}".rstrip()
    resto = _PATRON_SALUDO_ABIERTO.sub("", texto, count=1)
    resto = re.sub(r"^([¿¡\s]*)(\w)", lambda m: m.group(1) + m.group(2).upper(), resto, count=1)
    return f"¡Hola! {presentacion} {resto}".rstrip()


async def _enrutar_a_ejecutiva(phone_number: str, ahora: datetime | None = None) -> None:
    """Restricción "el agente solo procesa conversaciones que él inicia":
    cuando no hay conversación activa (esta_activa == False), en vez de
    procesar el mensaje con el bot se saluda al cliente y se avisa a la
    ejecutiva para que continúe manualmente. El saludo depende del horario
    de atención (ver horario_atencion); `ahora` es la hora actual, que se
    puede inyectar para probarlo."""
    logger.info(
        "[WhatsApp] Mensaje de %s enrutado a la ejecutiva: no hay conversación activa del bot",
        phone_number,
    )
    ahora = ahora or datetime.now().astimezone()
    await send_whatsapp_message(to=phone_number, message=mensaje_derivacion_ejecutiva(ahora))
    await send_whatsapp_message(
        to=settings.EJECUTIVA_PHONE,
        message=marcar_notificacion_ejecutiva(
            MENSAJE_NOTIFICACION_EJECUTIVA.format(telefono=phone_number), ahora
        ),
    )


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.META_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge, status_code=200)
    return PlainTextResponse(content="Forbidden", status_code=403)


async def _procesar_mensaje_entrante(message: dict, ahora: datetime | None = None) -> None:
    """Procesa un mensaje entrante ya aceptado por el webhook (corre en la
    cola del teléfono, ver webhook_queue). Ninguna excepción sale de aquí
    sin loguearse: Meta ya recibió su 200. `ahora` (hora actual) solo se
    inyecta en pruebas."""
    phone_number = message["from"]
    message_type = message.get("type")
    wamid = message.get("id")

    if message_type == "text":
        message_text = message.get("text", {}).get("body")
        print(f"[WhatsApp] From: {phone_number} - Message: {message_text}")
        contenido = message_text
        location = None
    elif message_type == "location":
        ubicacion = message.get("location", {})
        latitude = ubicacion.get("latitude")
        longitude = ubicacion.get("longitude")
        print(f"[WhatsApp] Location from {phone_number}: lat={latitude}, lon={longitude}")
        message_text = None
        contenido = f"lat={latitude}, lon={longitude}"
        location = {"latitude": latitude, "longitude": longitude}
    else:
        print(f"[WhatsApp] Tipo de mensaje no soportado ({message_type}) de {phone_number}")
        message_text = None
        contenido = None
        location = None

    es_nuevo = await registrar_mensaje_entrante(
        telefono=phone_number,
        tipo=message_type or "desconocido",
        contenido=contenido,
        meta_message_id=wamid,
    )
    if not es_nuevo:
        logger.info("[WhatsApp] Mensaje %s de %s ya procesado: se ignora", wamid, phone_number)
        return

    if message_type not in ("text", "location"):
        await send_whatsapp_message(to=phone_number, message=MENSAJE_TIPO_NO_SOPORTADO)
        return

    if not await esta_activa(phone_number):
        await _enrutar_a_ejecutiva(phone_number, ahora)
        return

    # El horario de atención no aplica aquí: en una conversación activa el
    # agente toma pedidos a cualquier hora.

    try:
        respuesta = await procesar_mensaje(
            phone=phone_number,
            message_type=message_type,
            message_text=message_text,
            location=location,
        )
    except Exception:
        logger.exception(
            "[WhatsApp] Falló procesar_mensaje (%s) para %s", message_type, phone_number
        )
        respuesta = MENSAJE_ERROR_GENERICO

    # El asistente se presenta una sola vez, en su primera respuesta de la
    # conversación (no depende de que el LLM lo escriba).
    try:
        if await es_primer_mensaje_conversacion(phone_number):
            respuesta = _presentar_asistente(respuesta)
    except Exception:
        logger.exception("[WhatsApp] Falló revisar si es el primer mensaje de %s", phone_number)

    await send_whatsapp_message(to=phone_number, message=respuesta)


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Responde 200 a Meta de inmediato: si la respuesta tarda (LLM + BD),
    Meta reintenta la entrega y el mismo mensaje se procesaría varias veces.
    El trabajo real queda en la cola FIFO del teléfono (webhook_queue), que
    respeta el orden de llegada y no procesa dos mensajes del mismo teléfono
    en paralelo. Una entrega repetida (mismo wamid) se descarta aquí mismo
    y, si el proceso se reinició entremedio, en registrar_mensaje_entrante."""
    payload = await request.json()

    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        phone_number = message["from"]
    except (KeyError, IndexError):
        print(f"[WhatsApp] Payload without message data: {payload}")
        return {"status": "received"}

    wamid = message.get("id")
    if wamid and not reservar_wamid(wamid):
        logger.info("[WhatsApp] Entrega repetida del mensaje %s de %s: se ignora", wamid, phone_number)
        return {"status": "received"}

    encolar(phone_number, lambda: _procesar_mensaje_entrante(message))
    return {"status": "received"}
