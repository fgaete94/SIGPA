"""
Script temporal para simular, a través de procesar_mensaje, el camino
alternativo de un cliente NUEVO que rechaza compartir su ubicación de
WhatsApp (ver "ubicacion_rechazada" en el SYSTEM_PROMPT de
app/services/agent_service.py), las salvaguardas de "un saludo no anula el
pedido" / cancelación explícita, y las correcciones de fondo del bug en
producción del 2026-10-01 (teléfono 56957721243): "esperando_ubicacion" sin
productos, cancelación que no actuaba sobre un estado "fantasma", y
repetición del mismo mensaje fijo de ubicación turno tras turno.
No es parte del código final: solo para validar manualmente el comportamiento.

Cada caso usa un teléfono nuevo y distinto (ninguno debe existir todavía como
Cliente en la BD, ya que procesar_mensaje determina es_cliente_nuevo
consultando por telefono == PHONE). Como ningún caso llega a confirmar el
pedido (_confirmar_pedido nunca se llama aquí), no se crea ningún Cliente en
la BD al correr este script, así que puede repetirse sin "ensuciar" los
teléfonos usados.

Cada caso define su propia secuencia de "turnos" (mensajes de texto
sucesivos del cliente) y una función "verificar" que recibe el draft final y
la lista de respuestas del bot para decidir OK/FALLO.

Uso: python -m scripts.test_ubicacion_rechazada
"""

import asyncio

from app.services.draft_store import descartar_draft, get_draft
from app.services.order_flow import _es_cliente_nuevo, procesar_mensaje

MENSAJE_INICIAL = "Quiero 1 Dispensador USB, soy Pedro"
PRODUCTOS_ESPERADOS = [{"nombre_producto": "Dispensador USB", "cantidad": 1}]


def _avanzo(draft: dict | None) -> bool:
    """True si el draft ya salió del estado 'esperando_ubicacion' (es decir,
    el pedido avanzó sin necesidad de recibir coordenadas)."""
    return draft is not None and draft.get("estado") != "esperando_ubicacion"


def _retomo_pedido_sin_perderlo(draft: dict | None) -> bool:
    """True si, tras un saludo/interjección ambigua, el draft sigue esperando
    ubicación Y conservó los productos del pedido en curso (no se vació ni se
    marcó como rechazo)."""
    return (
        draft is not None
        and draft.get("estado") == "esperando_ubicacion"
        and draft.get("productos") == PRODUCTOS_ESPERADOS
        and not draft.get("ubicacion_rechazada")
        and draft.get("direccion_texto") is None
    )


CASOS = [
    {
        "descripcion": '"hola?" no es un rechazo ni una cancelación: debe retomar el pedido y seguir pidiendo ubicación',
        "phone": "56930000001",
        "turnos": [MENSAJE_INICIAL, "hola?"],
        "verificar": lambda draft, respuestas: _retomo_pedido_sin_perderlo(draft),
        "esperado": "sigue esperando ubicación, conserva productos, ubicacion_rechazada=false",
    },
    {
        "descripcion": '"ok" no es un rechazo ni una cancelación: debe retomar el pedido y seguir pidiendo ubicación',
        "phone": "56930000002",
        "turnos": [MENSAJE_INICIAL, "ok"],
        "verificar": lambda draft, respuestas: _retomo_pedido_sin_perderlo(draft),
        "esperado": "sigue esperando ubicación, conserva productos, ubicacion_rechazada=false",
    },
    {
        "descripcion": "Rechazo explícito + dirección en el mismo mensaje: debe avanzar",
        "phone": "56930000003",
        "turnos": [MENSAJE_INICIAL, "no tengo esa opción, mi dirección es Av. Siempre Viva 742"],
        "verificar": lambda draft, respuestas: (
            _avanzo(draft)
            and draft.get("ubicacion_rechazada") is True
            and draft.get("direccion_texto") == "Av. Siempre Viva 742"
        ),
        "esperado": "avanza con direccion_texto='Av. Siempre Viva 742', ubicacion_rechazada=true",
    },
    {
        "descripcion": "Rechazo implícito ('prefiero escribirla') + dirección: debe avanzar",
        "phone": "56930000004",
        "turnos": [MENSAJE_INICIAL, "prefiero escribirla: Calle Falsa 123"],
        "verificar": lambda draft, respuestas: (
            _avanzo(draft)
            and draft.get("ubicacion_rechazada") is True
            and draft.get("direccion_texto") == "Calle Falsa 123"
        ),
        "esperado": "avanza con direccion_texto='Calle Falsa 123', ubicacion_rechazada=true",
    },
    {
        "descripcion": "Rechazo sin dirección alternativa: debe pedir la dirección en texto, no la ubicación",
        "phone": "56930000005",
        "turnos": [MENSAJE_INICIAL, "no puedo"],
        "verificar": lambda draft, respuestas: (
            draft is not None
            and draft.get("ubicacion_rechazada") is True
            and draft.get("estado") != "esperando_ubicacion"
            and draft.get("direccion_texto") is None
            and "ubicación de whatsapp" not in respuestas[-1].lower()
            and "📎" not in respuestas[-1]
        ),
        "esperado": "ubicacion_rechazada=true, direccion_texto sigue null, respuesta pide dirección (no ubicación)",
    },
    {
        "descripcion": '"sigues ahí?" no es un rechazo ni una cancelación: debe retomar el pedido y seguir pidiendo ubicación',
        "phone": "56930000006",
        "turnos": [MENSAJE_INICIAL, "sigues ahí?"],
        "verificar": lambda draft, respuestas: _retomo_pedido_sin_perderlo(draft),
        "esperado": "sigue esperando ubicación, conserva productos, ubicacion_rechazada=false",
    },
    {
        "descripcion": '"cancela" SÍ debe vaciar el pedido en curso por completo',
        "phone": "56930000007",
        "turnos": [MENSAJE_INICIAL, "cancela"],
        "verificar": lambda draft, respuestas: (
            draft is None and "cancel" in respuestas[-1].lower()
        ),
        "esperado": "el draft queda en None (pedido cancelado por completo)",
    },
    {
        "descripcion": (
            '"hola" como PRIMER mensaje (sin pedido previo): no debe dejar '
            "esperando_ubicacion=True para un pedido vacío"
        ),
        "phone": "56930000008",
        "turnos": ["hola"],
        "verificar": lambda draft, respuestas: (
            draft is None or draft.get("estado") != "esperando_ubicacion"
        ),
        "esperado": "el draft (si existe) no queda en estado 'esperando_ubicacion' sin productos",
    },
    {
        "descripcion": '"hola" y luego "cancelar": el draft debe quedar completamente limpio',
        "phone": "56930000009",
        "turnos": ["hola", "cancelar"],
        "verificar": lambda draft, respuestas: draft is None,
        "esperado": "el draft queda en None tras cancelar",
    },
    {
        "descripcion": (
            "Pedido en curso en 'esperando_ubicacion' y luego 'cancelar' "
            "(reproduce el bug de producción): debe vaciar todo"
        ),
        "phone": "56930000010",
        "turnos": [MENSAJE_INICIAL, "cancelar"],
        "verificar": lambda draft, respuestas: (
            draft is None and "cancel" in respuestas[-1].lower()
        ),
        "esperado": "el draft queda en None (pedido y esperando_ubicacion vaciados por completo)",
    },
]


async def _descartar_borradores() -> None:
    """Descarta el borrador (caché y fila de borrador_pedido) de cada teléfono
    de CASOS: no tienen cliente, así que nadie más los limpia."""
    for caso in CASOS:
        await descartar_draft(caso["phone"])


async def main() -> None:
    resultados = []
    await _descartar_borradores()

    try:
        for i, caso in enumerate(CASOS, start=1):
            phone = caso["phone"]
            print("#" * 70)
            print(f"Caso {i}/{len(CASOS)}: {caso['descripcion']}")
            print(f"Phone: {phone}")
            print("#" * 70)

            if not await _es_cliente_nuevo(phone):
                print(
                    f"[ADVERTENCIA] {phone} ya existe como Cliente en la BD; "
                    "este caso necesita un teléfono nuevo para probar el flujo "
                    "de cliente nuevo. Cambia el número en CASOS y vuelve a correr."
                )
                resultados.append((caso["descripcion"], False))
                print()
                continue

            respuestas = []
            for j, mensaje in enumerate(caso["turnos"], start=1):
                print(f"--- Turno {j}/{len(caso['turnos'])} ---")
                print(f"Cliente: {mensaje}")
                respuesta = await procesar_mensaje(
                    phone=phone,
                    message_type="text",
                    message_text=mensaje,
                    location=None,
                )
                print(f"Bot: {respuesta}")
                respuestas.append(respuesta)

            draft = get_draft(phone)
            print(f"Draft resultante: {draft}")
            print(f"Esperado: {caso['esperado']}")

            try:
                ok = bool(caso["verificar"](draft, respuestas))
            except Exception as exc:
                print(f"[ERROR] Falló la verificación del caso: {exc!r}")
                ok = False

            print(f"Resultado: {'OK' if ok else 'FALLO'}")
            resultados.append((caso["descripcion"], ok))
            print()
    finally:
        await _descartar_borradores()

    print("=" * 70)
    print("Resumen final:")
    for descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
