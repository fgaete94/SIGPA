import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.core.config import settings
from app.core.security import verify_supabase_jwt

EMAIL = "admin@sigpa-test.cl"
PASSWORD = "123456"

INVALID_TOKEN = "esto.no.es.un.jwt.valido"


def login() -> str:
    url = f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": settings.SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    }
    body = {"email": EMAIL, "password": PASSWORD}

    response = httpx.post(url, headers=headers, json=body, timeout=30.0)
    print(f"Login status: {response.status_code}")

    data = response.json()
    if "access_token" not in data:
        raise RuntimeError(f"No se obtuvo access_token: {data}")

    return data["access_token"]


def test_valid_token():
    access_token = login()
    print("\n--- Verificando token válido ---")
    try:
        payload = verify_supabase_jwt(access_token)
        print("Token válido. Payload:")
        print(payload)
    except Exception as e:
        print(f"Error al verificar el token: {e}")


def test_invalid_token():
    print("\n--- Verificando token inválido (prueba negativa) ---")
    try:
        payload = verify_supabase_jwt(INVALID_TOKEN)
        print(f"ERROR: se esperaba una excepción, pero se obtuvo: {payload}")
    except Exception as e:
        print(f"OK, se lanzó la excepción esperada: {e}")


def main():
    test_valid_token()
    test_invalid_token()


if __name__ == "__main__":
    main()
