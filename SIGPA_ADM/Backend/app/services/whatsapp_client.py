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

    try:
        response_body = response.json()
    except ValueError:
        response_body = response.text

    # TEMPORAL: log completo para debugging, quitar cuando se resuelva el problema.
    logger.info(
        "WhatsApp API response (status %s): %s",
        response.status_code,
        response_body,
    )

    if response.status_code != 200:
        return None

    return response_body
