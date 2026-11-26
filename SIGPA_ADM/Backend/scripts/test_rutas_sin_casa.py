"""
Script temporal para validar RUTA_TERMINA_EN_CASA (app/services/ruta_service.py):
con true el último viaje liviano termina en la casa del despachador; con false
(varios camiones) todos vuelven a la planta y a n8n se le envía umbral 1.

No toca la BD ni llama a n8n: prueba las funciones de la regla.

Uso: python -m scripts.test_rutas_sin_casa
"""

import sys

from app.core.config import settings
from app.services import ruta_service as rs

fallos = 0


def check(condicion: bool, descripcion: str) -> None:
    global fallos
    print(f"{'ok   ' if condicion else 'FALLO'} {descripcion}")
    if not condicion:
        fallos += 1


def viaje_declarado(viaje: int, peso: int, termina: str) -> dict:
    return {"viaje": viaje, "peso_kg": peso, "termina_en": termina}


def respuesta(termina_ultimo: str) -> dict:
    # Dos viajes: 1 (pedido 1, 900 kg) y 2 (pedido 2, 300 kg).
    return {
        "ruta": [
            {"pedido_id": 1, "viaje": 1, "orden_entrega": 1, "latitud": -33.0, "longitud": -71.5},
            {"pedido_id": 2, "viaje": 2, "orden_entrega": 1, "latitud": -33.1, "longitud": -71.6},
        ],
        "viajes": [viaje_declarado(1, 900, "planta"), viaje_declarado(2, 300, termina_ultimo)],
        "sin_resolver": [],
    }


def valida(resp: dict) -> bool:
    try:
        rs._validar_respuesta(resp, [1, 2], {1: 900, 2: 300})
        return True
    except Exception:
        return False


def main() -> None:
    original = (settings.RUTA_TERMINA_EN_CASA, settings.RUTA_UMBRAL_CASA_KG)
    try:
        settings.RUTA_UMBRAL_CASA_KG = 800
        check(type(settings).model_fields["RUTA_TERMINA_EN_CASA"].default is True, "por defecto termina en la casa (como antes)")

        print("-- Con casa (un repartidor)")
        settings.RUTA_TERMINA_EN_CASA = True
        check(rs._umbral_casa() == 800, "umbral a n8n = RUTA_UMBRAL_CASA_KG")
        check(rs._termina_en(2, 2, 300) == "casa", "último viaje liviano termina en casa")
        check(rs._termina_en(2, 2, 800) == "planta", "el umbral es estricto")
        check(valida(respuesta("casa")), "acepta la respuesta de n8n con el último en casa")
        check(not valida(respuesta("planta")), "rechaza el último liviano en planta")

        print("-- Sin casa (varios camiones)")
        settings.RUTA_TERMINA_EN_CASA = False
        check(rs._umbral_casa() == 1, "umbral a n8n = 1 (n8n exige >= 1 y ningún viaje pesa menos)")
        check(rs._termina_en(2, 2, 300) == "planta", "último viaje liviano vuelve a la planta")
        check(rs._resumir_viajes({1: [1], 2: [2]}, {1: 900, 2: 300})[-1]["termina_en"] == "planta", "el resumen (Ruta en curso) dice planta")
        check(valida(respuesta("planta")), "acepta la respuesta de n8n con todo en planta")
        check(not valida(respuesta("casa")), "rechaza una respuesta que manda a la casa")
        vista = rs._vista_previa([], {}, {}, {}, [])
        check(vista["termina_en_casa"] is False, "la vista previa informa termina_en_casa=false al panel")
    finally:
        settings.RUTA_TERMINA_EN_CASA, settings.RUTA_UMBRAL_CASA_KG = original

    print(f"\n{'Todo ok' if not fallos else f'{fallos} fallo(s)'}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
