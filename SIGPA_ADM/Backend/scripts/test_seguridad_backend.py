"""
Script temporal para validar el endurecimiento del backend:
- /docs, /redoc y /openapi.json solo existen con ENABLE_DOCS=true.
- POST /webhook verifica la firma X-Hub-Signature-256 de Meta cuando
  META_APP_SECRET está definido, y sin él sigue aceptando todo.

No toca la BD ni envía mensajes: los POST usan un payload sin mensajes
(un aviso de estado), que el webhook responde sin procesar nada.

Uso: python -m scripts.test_seguridad_backend
"""

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys

import httpx

from app.api.routes.whatsapp import firma_meta_valida
from app.core.config import settings
from app.main import app

SECRETO = "secreto-de-prueba"
CUERPO = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"statuses": []}}]}]}).encode()

fallos = 0


def check(condicion: bool, descripcion: str) -> None:
    global fallos
    print(f"{'ok   ' if condicion else 'FALLO'} {descripcion}")
    if not condicion:
        fallos += 1


def firmar(cuerpo: bytes, secreto: str = SECRETO) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def docs_con(valor: str | None) -> str:
    """Importa la app en otro proceso con ENABLE_DOCS=valor y devuelve su openapi_url."""
    env = {k: v for k, v in os.environ.items() if k != "ENABLE_DOCS"}
    if valor is not None:
        env["ENABLE_DOCS"] = valor
    resultado = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print(app.openapi_url, app.docs_url, app.redoc_url)"],
        env=env, capture_output=True, text=True, check=True,
    )
    return resultado.stdout.strip().splitlines()[-1]


async def main() -> None:
    print("-- Documentación de la API")
    check(docs_con(None) == "None None None", "sin ENABLE_DOCS no se publican /docs, /redoc ni /openapi.json")
    check(docs_con("false") == "None None None", "con ENABLE_DOCS=false tampoco")
    check(docs_con("true") == "/openapi.json /docs /redoc", "con ENABLE_DOCS=true sí (para trabajar en local)")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        if not settings.ENABLE_DOCS:
            for ruta in ("/docs", "/redoc", "/openapi.json"):
                check((await http.get(ruta)).status_code == 404, f"GET {ruta} responde 404")
        check((await http.get("/health")).status_code == 200, "GET /health sigue respondiendo")

        print("-- Firma del webhook")
        check(firma_meta_valida(CUERPO, firmar(CUERPO), SECRETO), "la firma correcta es válida")
        check(not firma_meta_valida(CUERPO, firmar(CUERPO, "otro"), SECRETO), "firmada con otro secreto no es válida")
        check(not firma_meta_valida(CUERPO + b" ", firmar(CUERPO), SECRETO), "cuerpo modificado no es válido")
        check(not firma_meta_valida(CUERPO, firmar(CUERPO).removeprefix("sha256="), SECRETO), "sin el prefijo sha256= no es válida")
        check(not firma_meta_valida(CUERPO, None, SECRETO), "sin firma no es válida")

        original = settings.META_APP_SECRET
        try:
            settings.META_APP_SECRET = SECRETO
            headers = {"Content-Type": "application/json"}
            r = await http.post("/webhook", content=CUERPO, headers=headers)
            check(r.status_code == 403, "con secreto: POST sin firma -> 403")
            r = await http.post("/webhook", content=CUERPO, headers={**headers, "X-Hub-Signature-256": firmar(CUERPO, "otro")})
            check(r.status_code == 403, "con secreto: POST con firma falsa -> 403")
            r = await http.post("/webhook", content=CUERPO, headers={**headers, "X-Hub-Signature-256": firmar(CUERPO)})
            check(r.status_code == 200 and r.json() == {"status": "received"}, "con secreto: POST firmado como Meta -> 200")
            malo = b"{no es json"
            r = await http.post("/webhook", content=malo, headers={**headers, "X-Hub-Signature-256": firmar(malo)})
            check(r.status_code == 400, "firmado pero con JSON inválido -> 400 (no 500)")

            settings.META_APP_SECRET = ""
            r = await http.post("/webhook", content=CUERPO, headers=headers)
            check(r.status_code == 200, "sin secreto configurado: se acepta como hasta ahora")
        finally:
            settings.META_APP_SECRET = original

        r = await http.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "incorrecto", "hub.challenge": "x"})
        check(r.status_code == 403, "GET /webhook con verify_token incorrecto sigue en 403")

    print(f"\n{'Todo ok' if not fallos else f'{fallos} fallo(s)'}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    asyncio.run(main())
