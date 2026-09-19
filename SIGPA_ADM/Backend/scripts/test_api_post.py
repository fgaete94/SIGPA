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

    separador(
        "POST /productos con nombre duplicado - "
        '{"nombre": "Bidón 12L Nuevo", "precio_unitario": 6000}'
    )
    response = httpx.post(
        f"{API_BASE_URL}/productos",
        headers=headers,
        json={"nombre": "Bidón 12L Nuevo", "precio_unitario": 6000},
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    separador(
        "POST /productos nuevo - "
        '{"nombre": "Producto Test API", "precio_unitario": 1000, "stock": 5}'
    )
    response = httpx.post(
        f"{API_BASE_URL}/productos",
        headers=headers,
        json={"nombre": "Producto Test API", "precio_unitario": 1000, "stock": 5},
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    separador(
        "POST /clientes con telefono duplicado - "
        '{"nombre": "Cliente Test API", "telefono": "56900000000"} (se espera 409)'
    )
    response = httpx.post(
        f"{API_BASE_URL}/clientes",
        headers=headers,
        json={"nombre": "Cliente Test API", "telefono": "56900000000"},
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
    if response.status_code == 409:
        print("OK: se recibió 409 como se esperaba.")
    else:
        print(f"ERROR: se esperaba 409, se obtuvo {response.status_code}.")

    separador(
        "POST /clientes nuevo - "
        '{"nombre": "Cliente Test API", "telefono": "56933334444"}'
    )
    response = httpx.post(
        f"{API_BASE_URL}/clientes",
        headers=headers,
        json={"nombre": "Cliente Test API", "telefono": "56933334444"},
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    cliente_id = None
    if response.status_code == 201:
        cliente_id = response.json().get("id")
        print(f"Cliente creado con id: {cliente_id}")
    else:
        print("ERROR: no se pudo crear el cliente, se omiten las pruebas de pedidos que dependen de este id.")

    separador("GET /productos/1 (para conocer precio_unitario esperado)")
    response = httpx.get(f"{API_BASE_URL}/productos/1", headers=headers, timeout=30.0)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    precio_unitario_producto_1 = None
    if response.status_code == 200:
        precio_unitario_producto_1 = response.json().get("precio_unitario")

    if cliente_id is not None:
        separador(
            f"POST /pedidos exitoso - cliente_id={cliente_id}, "
            'lineas=[{"producto_id": 1, "cantidad": 2}]'
        )
        response = httpx.post(
            f"{API_BASE_URL}/pedidos",
            headers=headers,
            json={
                "cliente_id": cliente_id,
                "lineas": [{"producto_id": 1, "cantidad": 2}],
            },
            timeout=30.0,
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")

        if response.status_code == 201 and precio_unitario_producto_1 is not None:
            total_esperado = 2 * precio_unitario_producto_1
            total_obtenido = response.json().get("total")
            if float(total_obtenido) == float(total_esperado):
                print(f"OK: total {total_obtenido} coincide con el esperado ({total_esperado}).")
            else:
                print(
                    f"ERROR: total {total_obtenido} NO coincide con el esperado ({total_esperado})."
                )
    else:
        separador("POST /pedidos exitoso - OMITIDO (no hay cliente_id disponible)")

    separador(
        "POST /pedidos con producto_id inexistente - "
        'lineas=[{"producto_id": 9999, "cantidad": 1}] (se espera 404)'
    )
    response = httpx.post(
        f"{API_BASE_URL}/pedidos",
        headers=headers,
        json={
            "cliente_id": cliente_id if cliente_id is not None else 1,
            "lineas": [{"producto_id": 9999, "cantidad": 1}],
        },
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
    if response.status_code == 404:
        print("OK: se recibió 404 como se esperaba.")
    else:
        print(f"ERROR: se esperaba 404, se obtuvo {response.status_code}.")


if __name__ == "__main__":
    main()
