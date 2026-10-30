"""
Script temporal para validar manualmente el rename de schema en detalle_pedido
(cantidad -> cantidad_solicitada, + nuevo campo cantidad_entregada).
No es parte del código final.

Requiere que el servidor esté corriendo en API_BASE_URL (ver test_api_post.py).

Uso: python -m scripts.test_cantidad_solicitada
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.core.config import settings

API_BASE_URL = "http://localhost:8000"

# Credenciales del usuario de Supabase Auth para estos scripts: se leen del .env
# local (TEST_ADMIN_EMAIL / TEST_ADMIN_PASSWORD), nunca van en el código.
EMAIL = settings.TEST_ADMIN_EMAIL.strip()
PASSWORD = settings.TEST_ADMIN_PASSWORD.strip()

if __name__ == "__main__" and not (EMAIL and PASSWORD):
    sys.exit("Define TEST_ADMIN_EMAIL y TEST_ADMIN_PASSWORD en tu .env local (ver .env.example)")


CLIENTE_ID = 1
PRODUCTO_ID = 1


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


def _validar_linea(linea: dict) -> bool:
    ok = True
    if linea.get("cantidad_solicitada") != 2:
        print(f"ERROR: se esperaba cantidad_solicitada=2, se obtuvo {linea.get('cantidad_solicitada')!r}")
        ok = False
    else:
        print("OK: cantidad_solicitada=2")

    if linea.get("cantidad_entregada") is not None:
        print(f"ERROR: se esperaba cantidad_entregada=null, se obtuvo {linea.get('cantidad_entregada')!r}")
        ok = False
    else:
        print("OK: cantidad_entregada=null")

    return ok


def main():
    access_token = login()
    headers = {"Authorization": f"Bearer {access_token}"}

    separador(f"GET /clientes/{CLIENTE_ID} (confirmar que el cliente existe)")
    response = httpx.get(f"{API_BASE_URL}/clientes/{CLIENTE_ID}", headers=headers, timeout=30.0)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
    if response.status_code != 200:
        print(f"ERROR: no existe cliente_id={CLIENTE_ID}, ajusta CLIENTE_ID en el script.")
        return

    separador(
        f"POST /pedidos - cliente_id={CLIENTE_ID}, "
        f'lineas=[{{"producto_id": {PRODUCTO_ID}, "cantidad_solicitada": 2}}]'
    )
    response = httpx.post(
        f"{API_BASE_URL}/pedidos",
        headers=headers,
        json={
            "cliente_id": CLIENTE_ID,
            "lineas": [{"producto_id": PRODUCTO_ID, "cantidad_solicitada": 2}],
        },
        timeout=30.0,
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    if response.status_code != 201:
        print(f"ERROR: se esperaba 201, se obtuvo {response.status_code}. Se omite el resto de las pruebas.")
        return

    pedido_creado = response.json()
    pedido_id = pedido_creado.get("id")
    print(f"\nPedido creado con id: {pedido_id}")

    lineas_creadas = pedido_creado.get("lineas", [])
    if not lineas_creadas:
        print("ERROR: la respuesta del POST no trae 'lineas'.")
    else:
        print("\n--- Validando línea devuelta por el POST ---")
        _validar_linea(lineas_creadas[0])

    separador(f"GET /pedidos/{pedido_id} (confirmar persistencia)")
    response = httpx.get(f"{API_BASE_URL}/pedidos/{pedido_id}", headers=headers, timeout=30.0)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    if response.status_code != 200:
        print(f"ERROR: se esperaba 200 en el GET, se obtuvo {response.status_code}.")
        return

    pedido_obtenido = response.json()
    lineas_obtenidas = pedido_obtenido.get("lineas", [])
    if not lineas_obtenidas:
        print("ERROR: la respuesta del GET no trae 'lineas'.")
    else:
        print("\n--- Validando línea devuelta por el GET ---")
        _validar_linea(lineas_obtenidas[0])


if __name__ == "__main__":
    main()
