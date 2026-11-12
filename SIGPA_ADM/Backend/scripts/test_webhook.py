import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hashlib
import hmac
import json

import httpx

from app.core.config import settings

WEBHOOK_URL = "https://sigpa-34sy.onrender.com/webhook"

SENDER_PHONE_NUMBER = "56957721243"
MESSAGE_TEXT = "recarga"


def build_payload() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": settings.META_WABA_ID,
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": settings.META_PHONE_NUMBER_ID,
                                "phone_number_id": settings.META_PHONE_NUMBER_ID,
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Felipe"},
                                    "wa_id": SENDER_PHONE_NUMBER,
                                }
                            ],
                            "messages": [
                                {
                                    "from": SENDER_PHONE_NUMBER,
                                    "id": "wamid.test123",
                                    "timestamp": "1694000000",
                                    "text": {"body": MESSAGE_TEXT},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def main():
    payload = build_payload()
    cuerpo = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    # Con META_APP_SECRET en el .env se firma como Meta; sin él, el backend
    # en producción (que sí lo tiene) responde 403.
    if settings.META_APP_SECRET:
        firma = hmac.new(settings.META_APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={firma}"
    response = httpx.post(WEBHOOK_URL, content=cuerpo, headers=headers, timeout=30.0)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")


if __name__ == "__main__":
    main()
