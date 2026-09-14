from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.services.whatsapp_client import send_whatsapp_message

router = APIRouter(tags=["whatsapp"])


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

        if message_type == "location":
            location = message.get("location", {})
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            print(f"[WhatsApp] Location from {phone_number}: lat={latitude}, lon={longitude}")

            # TODO: conectar la lógica del agente. Cuando exista, esta ubicación debe
            # guardarse en el draft_store como la ubicación pendiente de confirmar
            # del pedido en curso para este phone_number.
        else:
            message_text = message.get("text", {}).get("body")
            print(f"[WhatsApp] From: {phone_number} - Message: {message_text}")

            # TODO: conectar la lógica del agente para procesar el mensaje entrante
            # Llamada de prueba temporal para validar el envío real vía Graph API
            await send_whatsapp_message(to=phone_number, message=f"Recibido: {message_text}")
    except (KeyError, IndexError):
        print(f"[WhatsApp] Payload without message data: {payload}")

    return {"status": "received"}
