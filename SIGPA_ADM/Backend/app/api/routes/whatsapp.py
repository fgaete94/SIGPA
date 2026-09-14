import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.services.order_flow import procesar_mensaje
from app.services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

MENSAJE_ERROR_GENERICO = (
    "Ocurrió un problema procesando tu mensaje, por favor intenta de nuevo o contacta a un ejecutivo."
)

MENSAJE_TIPO_NO_SOPORTADO = "Por ahora solo puedo procesar mensajes de texto o de ubicación."


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.META_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge, status_code=200)
    return PlainTextResponse(content="Forbidden", status_code=403)


@router.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()

    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        phone_number = message["from"]
        message_type = message.get("type")
    except (KeyError, IndexError):
        print(f"[WhatsApp] Payload without message data: {payload}")
        return {"status": "received"}

    if message_type == "text":
        message_text = message.get("text", {}).get("body")
        print(f"[WhatsApp] From: {phone_number} - Message: {message_text}")

        try:
            respuesta = await procesar_mensaje(
                phone=phone_number,
                message_type="text",
                message_text=message_text,
                location=None,
            )
        except Exception:
            logger.exception(
                "[WhatsApp] Falló procesar_mensaje (text) para %s", phone_number
            )
            respuesta = MENSAJE_ERROR_GENERICO

        await send_whatsapp_message(to=phone_number, message=respuesta)

    elif message_type == "location":
        location = message.get("location", {})
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        print(f"[WhatsApp] Location from {phone_number}: lat={latitude}, lon={longitude}")

        try:
            respuesta = await procesar_mensaje(
                phone=phone_number,
                message_type="location",
                message_text=None,
                location={"latitude": latitude, "longitude": longitude},
            )
        except Exception:
            logger.exception(
                "[WhatsApp] Falló procesar_mensaje (location) para %s", phone_number
            )
            respuesta = MENSAJE_ERROR_GENERICO

        await send_whatsapp_message(to=phone_number, message=respuesta)

    else:
        print(f"[WhatsApp] Tipo de mensaje no soportado ({message_type}) de {phone_number}")
        await send_whatsapp_message(to=phone_number, message=MENSAJE_TIPO_NO_SOPORTADO)

    return {"status": "received"}
