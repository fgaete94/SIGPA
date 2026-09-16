"""
Script temporal para probar interpret_message con el flujo de promociones
(dispensador + bidones), incluyendo el turno de aclaración de capacidad y
una consulta de precio directa sobre una promo.
No es parte del código final: solo para validar manualmente el comportamiento.

Uso: python -m scripts.test_promociones
"""

import asyncio
import json

from app.services.agent_service import interpret_message


def _contexto_desde_resultado(resultado: dict) -> dict:
    return {
        "intencion": resultado.get("intencion"),
        "productos": resultado.get("productos", []),
        "aclaracion_pendiente": resultado.get("aclaracion_pendiente"),
        "usa_direccion_habitual": resultado.get("usa_direccion_habitual"),
        "direccion_texto": resultado.get("direccion_texto"),
        "notas": resultado.get("notas"),
        "ubicacion_recibida": False,
        "nombre_cliente": resultado.get("nombre_cliente"),
    }


async def main() -> None:
    print("=" * 70)
    print("Caso 1/3: Pedido de promo (dispensador USB + 2 bidones), sin contexto previo")
    print(f"Mensaje: Quiero la promo del dispensador USB con 2 bidones")
    print("-" * 70)

    resultado_1 = await interpret_message(
        phone="56933333333",
        message="Quiero la promo del dispensador USB con 2 bidones",
        es_cliente_nuevo=False,
        context=None,
    )
    print(json.dumps(resultado_1, ensure_ascii=False, indent=2))
    print()

    print("=" * 70)
    print("Caso 2/3: Respuesta a la pregunta de capacidad, con contexto del turno anterior")
    print(f"Mensaje: 20 litros")
    print("-" * 70)

    contexto_2 = _contexto_desde_resultado(resultado_1)
    print(f"Contexto enviado: {json.dumps(contexto_2, ensure_ascii=False)}")
    print("-" * 70)

    resultado_2 = await interpret_message(
        phone="56933333333",
        message="20 litros",
        es_cliente_nuevo=False,
        context=contexto_2,
    )
    print(json.dumps(resultado_2, ensure_ascii=False, indent=2))
    print()

    print("=" * 70)
    print("Caso 3/3: Consulta de precio de la promo del dispensador básico con 1 bidón")
    print(f"Mensaje: ¿Cuánto cuesta la promo del dispensador básico con 1 bidón?")
    print("-" * 70)

    resultado_3 = await interpret_message(
        phone="56944444444",
        message="¿Cuánto cuesta la promo del dispensador básico con 1 bidón?",
        es_cliente_nuevo=False,
        context=None,
    )
    print(json.dumps(resultado_3, ensure_ascii=False, indent=2))
    print()

    print("=" * 70)
    print("Fin de las pruebas.")


if __name__ == "__main__":
    asyncio.run(main())
