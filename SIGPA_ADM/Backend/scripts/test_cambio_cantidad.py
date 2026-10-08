"""
Script temporal que valida la corrección del bug de la prueba real por
WhatsApp del 2026-10-07: con el resumen "3x Bidón 12L Recarga + 1x
Dispensador USB", el cliente escribió "mejor que sean 2 bidones, no 3", el
LLM devolvió "productos" vacío y el bot repitió el mismo resumen sin aviso.

1. Un cambio de cantidad simple se resuelve en código
   (_cambio_cantidad_simple), sin depender de que el LLM devuelva "fijar".
2. Un cambio explícito que no cambia nada responde MENSAJE_CAMBIO_NO_APLICADO
   en vez de repetir el resumen.

Sin BD ni OpenAI: el resultado del LLM se simula, y _catalogo y
construir_resumen_pedido se reemplazan por versiones en memoria. Los drafts
viven en memoria (draft_store), así que no se escribe nada persistente.
No es parte del código final: solo para validar manualmente el comportamiento.

Uso: python -m scripts.test_cambio_cantidad
"""

import asyncio

from app.services import order_flow
from app.services.agent_service import CATALOGO_NOMBRES
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import (
    ESTADO_ESPERANDO_CONFIRMACION,
    ESTADO_ESPERANDO_MODIFICACION,
    MENSAJE_CAMBIO_NO_APLICADO,
    _aplicar_resultado_llm,
    _cambio_cantidad_simple,
    _fusionar_productos,
    _normalizar_texto,
    _resumen_productos_corto,
)

PHONE = "56900000000"

PRECIOS = {
    "Bidón 12L Nuevo": 4000,
    "Bidón 12L Recarga": 2000,
    "Bidón 20L Nuevo": 6000,
    "Bidón 20L Recarga": 3000,
    "Dispensador Básico": 5000,
    "Dispensador USB": 7000,
    "Promo Dispensador Básico + 1 Bidón": 8000,
    "Promo Dispensador Básico + 2 Bidones": 10000,
    "Promo Dispensador USB + 1 Bidón": 10000,
    "Promo Dispensador USB + 2 Bidones": 12000,
}

CLIENTE = {"id": 1, "nombre": "Cliente Prueba", "direccion": "Santa Maria 793", "latitud": None, "longitud": None}

BIDONES = {"nombre_producto": "Bidón 12L Recarga", "cantidad": 3}
DISPENSADOR = {"nombre_producto": "Dispensador USB", "cantidad": 1}
PEDIDO = [BIDONES, DISPENSADOR]
PEDIDO_AMBIGUO = [
    {"nombre_producto": "Bidón 12L Recarga", "cantidad": 2},
    {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 1},
]


# --------------------------------------------------------------------------
# Reemplazos en memoria de lo que va a la BD
# --------------------------------------------------------------------------


async def _catalogo_fake() -> list[dict]:
    return [{"nombre": nombre, "precio": PRECIOS[nombre]} for nombre in CATALOGO_NOMBRES]


async def _resumen_fake(productos: list[dict]) -> dict:
    lineas = [
        {
            "nombre": item["nombre_producto"],
            "cantidad": item["cantidad"],
            "precio_unitario": float(PRECIOS[item["nombre_producto"]]),
            "subtotal": float(PRECIOS[item["nombre_producto"]] * item["cantidad"]),
        }
        for item in productos
    ]
    return {"lineas": lineas, "total": sum(linea["subtotal"] for linea in lineas)}


order_flow._catalogo = _catalogo_fake
order_flow.construir_resumen_pedido = _resumen_fake


def _draft(productos: list[dict], paso: str) -> dict:
    """Draft de un cliente existente con dirección confirmada, en el paso
    dado ("confirmacion": resumen mostrado; "algo_mas": preguntando)."""
    return {
        "intencion": "pedido",
        "productos": [dict(item) for item in productos],
        "aclaracion_pendiente": None,
        "notas": None,
        "nombre_cliente": None,
        "usa_direccion_habitual": True,
        "direccion_texto": None,
        "ubicacion": None,
        "ubicacion_rechazada": False,
        "algo_mas_respondido": paso == "confirmacion",
        "pendientes_modelo": [],
        "no_encontrados": [],
        "unidades_pedidas": {},
        "paso": paso,
        "estado": ESTADO_ESPERANDO_CONFIRMACION if paso == "confirmacion" else "armando",
    }


async def _turno(draft_previo: dict, mensaje: str, productos_llm: list[dict] | None = None) -> str:
    resultado = {
        "intencion": "pedido",
        "productos": productos_llm or [],
        "respuesta_sugerida": "¡Listo!",
    }
    texto = await _aplicar_resultado_llm(PHONE, resultado, draft_previo, CLIENTE, mensaje)
    draft = get_draft(PHONE) or {}
    print(f"  Cliente: {mensaje}")
    print(f"  Bot [{draft.get('paso')}/{draft.get('estado')}]: {texto}")
    return texto


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


def _sin_orden(lineas: list[dict]) -> list[tuple]:
    return sorted((item["nombre_producto"], item["cantidad"]) for item in lineas)


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_1(v: Verificador) -> None:
    mensaje = "mejor que sean 2 bidones, no 3"
    v.check(
        _cambio_cantidad_simple(PEDIDO, _normalizar_texto(mensaje)) == (0, 2),
        "_cambio_cantidad_simple: línea de bidones -> 2 (el 'no 3' no cuenta)",
    )
    esperado = [{"nombre_producto": "Bidón 12L Recarga", "cantidad": 2}, DISPENSADOR]

    # La conversación real: el LLM devolvió "productos" vacío.
    await _turno(_draft(PEDIDO, "confirmacion"), mensaje)
    draft = get_draft(PHONE) or {}
    v.check(draft.get("productos") == esperado, "LLM vacío: 2x Bidón 12L Recarga, dispensador intacto")
    clear_draft(PHONE)

    # Si el LLM sí devuelve el "fijar", no se aplica dos veces.
    fijar = [{"nombre_producto": "Bidón 12L Recarga", "cantidad": 2, "operacion": "fijar"}]
    await _turno(_draft(PEDIDO, "confirmacion"), mensaje, fijar)
    v.check((get_draft(PHONE) or {}).get("productos") == esperado, "LLM con 'fijar' 2: sigue en 2 (no se duplica)")
    clear_draft(PHONE)

    # Ni se suma lo que el LLM extraiga mal como "agregar".
    agregar = [{"nombre_producto": "Bidón 12L Recarga", "cantidad": 2, "operacion": "agregar"}]
    await _turno(_draft(PEDIDO, "confirmacion"), mensaje, agregar)
    v.check((get_draft(PHONE) or {}).get("productos") == esperado, "LLM con 'agregar' 2: sigue en 2 (no suma 5)")
    clear_draft(PHONE)


async def caso_2(v: Verificador) -> None:
    v.check(
        _cambio_cantidad_simple(PEDIDO, _normalizar_texto("que sean dos bidones")) == (0, 2),
        "'que sean dos bidones' -> línea de bidones a 2",
    )
    v.check(
        _cambio_cantidad_simple(PEDIDO, _normalizar_texto("mejor 2 bidones por favor")) == (0, 2),
        "'por favor' no se confunde con un reemplazo ('por')",
    )


async def caso_3(v: Verificador) -> None:
    mensaje = "mejor 2 dispensadores"
    v.check(
        _cambio_cantidad_simple(PEDIDO, _normalizar_texto(mensaje)) == (1, 2),
        "'mejor 2 dispensadores' -> línea del dispensador a 2",
    )
    await _turno(_draft(PEDIDO, "confirmacion"), mensaje)
    v.check(
        (get_draft(PHONE) or {}).get("productos") == [BIDONES, {"nombre_producto": "Dispensador USB", "cantidad": 2}],
        "2x Dispensador USB, bidones intactos",
    )
    clear_draft(PHONE)


async def caso_4(v: Verificador) -> None:
    v.check(
        _cambio_cantidad_simple(PEDIDO_AMBIGUO, _normalizar_texto("mejor que sean 3 bidones")) is None,
        "dos líneas de bidones: no se resuelve en código (ambiguo)",
    )
    # Otros mensajes que no son "fijar una cantidad" tampoco se resuelven.
    for mensaje in ("quita un bidón", "mejor agrega 2 bidones más", "mejor 2 bidones de 20", "mejor 2"):
        v.check(
            _cambio_cantidad_simple(PEDIDO, _normalizar_texto(mensaje)) is None,
            f"{mensaje!r}: no se resuelve en código",
        )


async def caso_5(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "cambia eso")
    draft = get_draft(PHONE) or {}
    v.check(
        texto == MENSAJE_CAMBIO_NO_APLICADO.format(pedido=_resumen_productos_corto(PEDIDO)),
        "responde MENSAJE_CAMBIO_NO_APLICADO con el pedido anotado",
    )
    v.check("Resumen de tu pedido" not in texto, "no repite el resumen")
    v.check(draft.get("estado") == ESTADO_ESPERANDO_MODIFICACION, "estado esperando_modificacion")
    v.check(draft.get("productos") == PEDIDO, "el pedido en curso se conserva")
    clear_draft(PHONE)

    # En "¿algo más?" responde igual pero no cambia el estado.
    texto = await _turno(_draft(PEDIDO, "algo_mas"), "cambia eso")
    draft = get_draft(PHONE) or {}
    v.check(texto.startswith("No pude aplicar el cambio"), "en '¿algo más?' responde el mismo aviso")
    v.check(draft.get("estado") != ESTADO_ESPERANDO_MODIFICACION, "en '¿algo más?' no pasa a esperando_modificacion")
    clear_draft(PHONE)

    # Mensajes que calzan con _PATRON_MODIFICACION pero no piden un cambio.
    texto = await _turno(_draft(PEDIDO, "algo_mas"), "no quiero nada más")
    v.check(not texto.startswith("No pude aplicar"), "'no quiero nada más' en '¿algo más?' no da el aviso")
    clear_draft(PHONE)
    previo = {**_draft(PEDIDO, "algo_mas"), "usa_direccion_habitual": None, "paso": "confirmar_direccion"}
    texto = await _turno(previo, "si, sin problema")
    v.check(not texto.startswith("No pude aplicar"), "'si, sin problema' al confirmar dirección no da el aviso")
    clear_draft(PHONE)


async def caso_6(v: Verificador) -> None:
    mensaje = "si, un dispensador usb"
    extraidos = [{"nombre_producto": "Dispensador USB", "cantidad": 1, "operacion": "agregar"}]
    previos = [{"nombre_producto": "Bidón 12L Recarga", "cantidad": 3}]
    productos, _ = _fusionar_productos(previos, extraidos, mensaje)
    v.check(_sin_orden(productos) == _sin_orden(PEDIDO), "_fusionar_productos suma el dispensador a los bidones")
    v.check(_cambio_cantidad_simple(previos, _normalizar_texto(mensaje)) is None, "no es un cambio de cantidad")

    await _turno(_draft(previos, "algo_mas"), mensaje, extraidos)
    v.check(
        _sin_orden((get_draft(PHONE) or {}).get("productos") or []) == _sin_orden(PEDIDO),
        "en '¿algo más?': 3x Bidón 12L Recarga + 1x Dispensador USB",
    )
    clear_draft(PHONE)


async def caso_7(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "mejor que sean 2 bidones, no 3")
    draft = get_draft(PHONE) or {}
    v.check(
        draft.get("productos") == [{"nombre_producto": "Bidón 12L Recarga", "cantidad": 2}, DISPENSADOR],
        "2x Bidón 12L Recarga, dispensador intacto",
    )
    v.check("Resumen de tu pedido" in texto, "vuelve directo al resumen (no pregunta '¿algo más?')")
    v.check(draft.get("estado") == ESTADO_ESPERANDO_CONFIRMACION, "espera confirmación")
    clear_draft(PHONE)


CASOS = [
    ("1", "[3x Bidón 12L Recarga, 1x Dispensador USB] + 'mejor que sean 2 bidones, no 3'", caso_1),
    ("2", "Mismo pedido + 'que sean dos bidones'", caso_2),
    ("3", "Mismo pedido + 'mejor 2 dispensadores'", caso_3),
    ("4", "[2x 12L Recarga, 1x 20L Nuevo] + 'mejor que sean 3 bidones': ambiguo, no se resuelve en código", caso_4),
    ("5", "'cambia eso' con LLM vacío: MENSAJE_CAMBIO_NO_APLICADO y esperando_modificacion", caso_5),
    ("6", "Regresión 2026-10-03: 'si, un dispensador usb' en '¿algo más?' suma el dispensador", caso_6),
    ("7", "Resumen mostrado + 'mejor que sean 2 bidones, no 3': vuelve directo al resumen", caso_7),
]


async def main() -> None:
    resultados = []
    for numero, descripcion, caso in CASOS:
        print("#" * 70)
        print(f"Caso {numero}: {descripcion}")
        print("#" * 70)
        clear_draft(PHONE)
        v = Verificador()
        try:
            await caso(v)
        except Exception as exc:
            print(f"    [ERROR] {exc!r}")
            v.errores.append(repr(exc))
        resultados.append((numero, descripcion, not v.errores))
        print()
    clear_draft(PHONE)

    print("=" * 70)
    print("Resumen final:")
    for numero, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {numero}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
