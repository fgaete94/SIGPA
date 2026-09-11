import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_whatsapp_message(to: str, message: str) -> dict | None:
    url = f"{settings.META_API_URL}/{settings.META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.META_WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        logger.error(
            "Error sending WhatsApp message (status %s): %s",
            response.status_code,
            response.text,
        )
        return None

    return response.json()
