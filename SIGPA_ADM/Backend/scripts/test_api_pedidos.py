import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.core.config import settings

API_BASE_URL = "http://localhost:8000"

EMAIL = "admin@sigpa-test.cl"
PASSWORD = "123456"


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


def separador(titulo: str):
    print("\n" + "=" * 60)
    print(titulo)
    print("=" * 60)


def main():
    access_token = login()
    headers = {"Authorization": f"Bearer {access_token}"}

    separador("GET /pedidos (con token)")
    response = httpx.get(f"{API_BASE_URL}/pedidos", headers=headers, timeout=30.0)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    separador("GET /pedidos?estado=pendiente (con token)")
    response = httpx.get(
        f"{API_BASE_URL}/pedidos",
        headers=headers,
        params={"estado": "pendiente"},
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    separador("GET /pedidos/1 (con token)")
    response = httpx.get(f"{API_BASE_URL}/pedidos/1", headers=headers, timeout=30.0)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    separador("PATCH /pedidos/1 (con token) - body: {'estado': 'confirmado'}")
    response = httpx.patch(
        f"{API_BASE_URL}/pedidos/1",
        headers=headers,
        json={"estado": "confirmado"},
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    separador("GET /pedidos SIN token (prueba negativa, se espera 401)")
    response = httpx.get(f"{API_BASE_URL}/pedidos", timeout=30.0)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
    if response.status_code == 401:
        print("OK: se recibió 401 como se esperaba.")
    else:
        print(f"ERROR: se esperaba 401, se obtuvo {response.status_code}.")


if __name__ == "__main__":
    main()
