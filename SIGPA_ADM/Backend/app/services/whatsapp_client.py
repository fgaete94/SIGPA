import logging

import httpx

from app.core.config import settings
from app.services.mensaje_whatsapp_service import registrar_mensaje

logger = logging.getLogger(__name__)


async def _post_a_meta(to: str, payload: dict, tipo: str, contenido: str) -> dict | None:
    """Envía el payload a la Cloud API de Meta y lo registra en
    mensaje_whatsapp. Devuelve el body de la respuesta si Meta respondió 200
    y None en cualquier otro caso."""
    url = f"{settings.META_API_URL}/{settings.META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.META_WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
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

    exitoso = response.status_code == 200

    if not exitoso:
        # Meta devuelve {"error": {"code", "message", "error_data": {"details"}}}:
        # ahí aparece, por ejemplo, la plantilla inexistente, los parámetros
        # que no calzan o el número no autorizado.
        error = response_body.get("error") if isinstance(response_body, dict) else None
        if isinstance(error, dict):
            logger.error(
                "WhatsApp API rechazó el envío %s a %s: código %s, %s (%s)",
                tipo,
                to,
                error.get("code"),
                error.get("message"),
                (error.get("error_data") or {}).get("details"),
            )
        else:
            logger.error(
                "WhatsApp API rechazó el envío %s a %s (status %s): %s",
                tipo,
                to,
                response.status_code,
                response_body,
            )

    meta_message_id = None
    if exitoso and isinstance(response_body, dict):
        mensajes_respuesta = response_body.get("messages") or []
        if mensajes_respuesta:
            meta_message_id = mensajes_respuesta[0].get("id")

    await registrar_mensaje(
        telefono=to,
        direccion="saliente",
        tipo=tipo,
        contenido=contenido,
        meta_message_id=meta_message_id,
        estado="enviado" if exitoso else "fallido",
    )

    if not exitoso:
        return None

    return response_body


async def send_whatsapp_message(to: str, message: str) -> dict | None:
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    return await _post_a_meta(to, payload, tipo="text", contenido=message)


async def send_whatsapp_template(
    to: str,
    template_name: str,
    language_code: str,
    body_params: list[str],
    texto_registro: str,
) -> dict | None:
    """Envía una plantilla aprobada por Meta (única forma de escribirle al
    cliente fuera de la ventana de 24 h). texto_registro es el cuerpo ya
    renderizado, solo para el historial de mensaje_whatsapp."""
    template: dict = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if body_params:
        template["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": param} for param in body_params],
            }
        ]
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template,
    }
    return await _post_a_meta(to, payload, tipo="template", contenido=texto_registro)
