"""
Script temporal que valida la corrección del bug de la prueba real por
WhatsApp del 2026-10-07: con el resumen "1x Bidón 20L Recarga + 1x
Dispensador USB", el cliente escribió "Disculpa, quiero el dispensador
básico" y el bot repitió el mismo resumen, sin aviso.

1. Después del resumen, "quiero X" sin decir "cambia" ni "también" es una
   corrección implícita: si no se puede resolver, se avisa con
   MENSAJE_MODIFICACION_NO_ENTENDIDA en vez de repetir el resumen.
2. Un cambio de modelo, variante o capacidad se resuelve en código
   (_cambio_atributo_simple): con un cambio explícito ("mejor el dispensador
   básico") se reemplaza directo; con uno implícito se pregunta si cambiar
   o agregar.

Sin BD ni OpenAI: el resultado del LLM se simula ("productos" vacío salvo
que se indique), y _catalogo y construir_resumen_pedido se reemplazan por
versiones en memoria. Los drafts viven en memoria (draft_store), así que no
se escribe nada persistente.
No es parte del código final: solo para validar manualmente el comportamiento.

Uso: python -m scripts.test_cambio_modelo
"""

import asyncio

from app.services import order_flow
from app.services.agent_service import CATALOGO_NOMBRES
from app.services.draft_store import clear_draft, get_draft, save_draft
from app.services.order_flow import (
    ESTADO_ESPERANDO_CONFIRMACION,
    ESTADO_ESPERANDO_MODIFICACION,
    MENSAJE_CAMBIO_NO_APLICADO,
    MENSAJE_CORRECCION_RECHAZADA,
    MENSAJE_MODIFICACION_NO_ENTENDIDA,
    PREGUNTA_CAMBIAR_O_AGREGAR,
    _aplicar_resultado_llm,
    _cambio_atributo_simple,
    _normalizar_texto,
    _responder_a_correccion,
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

BIDON = {"nombre_producto": "Bidón 20L Recarga", "cantidad": 1}
USB = {"nombre_producto": "Dispensador USB", "cantidad": 1}
BASICO = {"nombre_producto": "Dispensador Básico", "cantidad": 1}
PEDIDO = [BIDON, USB]
PEDIDO_AMBIGUO = [
    {"nombre_producto": "Bidón 12L Recarga", "cantidad": 2},
    {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 1},
]

AGREGAR_BASICO = [{"nombre_producto": "Dispensador Básico", "cantidad": 1, "operacion": "agregar"}]


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


async def _turno(
    draft_previo: dict,
    mensaje: str,
    productos_llm: list[dict] | None = None,
    intencion: str = "pedido",
    respuesta_sugerida: str = "¡Listo!",
) -> str:
    resultado = {
        "intencion": intencion,
        "productos": productos_llm or [],
        "respuesta_sugerida": respuesta_sugerida,
    }
    texto = await _aplicar_resultado_llm(PHONE, resultado, draft_previo, CLIENTE, mensaje)
    _mostrar(mensaje, texto)
    return texto


async def _responder(mensaje: str) -> str | None:
    """Respuesta a una corrección pendiente, como en procesar_mensaje: la
    corrección se saca del draft y vale solo para este mensaje."""
    draft = get_draft(PHONE)
    correccion = draft.pop("correccion_pendiente", None)
    save_draft(PHONE, draft)
    texto = await _responder_a_correccion(PHONE, draft, CLIENTE, correccion, mensaje)
    _mostrar(mensaje, texto)
    return texto


def _mostrar(mensaje: str, texto: str | None) -> None:
    draft = get_draft(PHONE) or {}
    print(f"  Cliente: {mensaje}")
    print(f"  Bot [{draft.get('paso')}/{draft.get('estado')}]: {texto}")


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


def _sin_orden(lineas: list[dict]) -> list[tuple]:
    return sorted((item["nombre_producto"], item["cantidad"]) for item in lineas)


def _productos() -> list[tuple]:
    return _sin_orden((get_draft(PHONE) or {}).get("productos") or [])


PREGUNTA_USB_BASICO = PREGUNTA_CAMBIAR_O_AGREGAR.format(actual="Dispensador USB", nuevo="Dispensador Básico")
NO_ENTENDIDA = MENSAJE_MODIFICACION_NO_ENTENDIDA.format(pedido=_resumen_productos_corto(PEDIDO))


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def _preguntar_cambiar_o_agregar(v: Verificador, intencion: str = "pedido") -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "Disculpa, quiero el dispensador básico", intencion=intencion)
    draft = get_draft(PHONE) or {}
    v.check(texto == PREGUNTA_USB_BASICO, f"[{intencion}] pregunta si cambiar o agregar")
    v.check(_productos() == _sin_orden(PEDIDO), f"[{intencion}] el pedido no cambia todavía")
    v.check(draft.get("estado") == ESTADO_ESPERANDO_MODIFICACION, f"[{intencion}] estado esperando_modificacion")
    v.check(bool(draft.get("correccion_pendiente")), f"[{intencion}] propuesta guardada en correccion_pendiente")


async def caso_1(v: Verificador) -> None:
    # Como en la conversación real, el LLM pudo marcarlo duda_pedido.
    await _preguntar_cambiar_o_agregar(v, "duda_pedido")
    clear_draft(PHONE)

    # (a) "cambiar": reemplaza y muestra el resumen.
    await _preguntar_cambiar_o_agregar(v)
    texto = await _responder("cambiar")
    v.check(_productos() == _sin_orden([BIDON, BASICO]), "(a) 1x Dispensador Básico, bidón intacto")
    v.check("Resumen de tu pedido" in (texto or ""), "(a) muestra el resumen actualizado")
    v.check((get_draft(PHONE) or {}).get("estado") == ESTADO_ESPERANDO_CONFIRMACION, "(a) espera confirmación")
    clear_draft(PHONE)

    # (b) "agregar": suma el Básico con la misma cantidad.
    await _preguntar_cambiar_o_agregar(v)
    texto = await _responder("agregar")
    v.check(_productos() == _sin_orden([BIDON, USB, BASICO]), "(b) USB y Básico")
    v.check("Resumen de tu pedido" in (texto or ""), "(b) muestra el resumen actualizado")
    clear_draft(PHONE)

    # (c) "no": nada cambia y pregunta qué quiere cambiar.
    await _preguntar_cambiar_o_agregar(v)
    texto = await _responder("no")
    v.check(texto == MENSAJE_CORRECCION_RECHAZADA, "(c) pregunta qué quiere cambiar")
    v.check(_productos() == _sin_orden(PEDIDO), "(c) el pedido no cambia")
    clear_draft(PHONE)

    # (d) "sí": responde la pregunta (reemplaza), nunca confirma el pedido.
    await _preguntar_cambiar_o_agregar(v)
    texto = await _responder("sí")
    draft = get_draft(PHONE)
    v.check(_productos() == _sin_orden([BIDON, BASICO]), "(d) 1x Dispensador Básico, bidón intacto")
    v.check("Resumen de tu pedido" in (texto or ""), "(d) muestra el resumen actualizado")
    v.check(draft is not None and draft.get("estado") == ESTADO_ESPERANDO_CONFIRMACION, "(d) espera confirmación")
    v.check("confirmado" not in (texto or ""), "(d) no confirma el pedido")
    clear_draft(PHONE)


async def caso_2(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "mejor el dispensador básico")
    v.check(_productos() == _sin_orden([BIDON, BASICO]), "1x Dispensador Básico, bidón intacto")
    v.check("Resumen de tu pedido" in texto, "vuelve directo al resumen, sin preguntar")
    v.check((get_draft(PHONE) or {}).get("estado") == ESTADO_ESPERANDO_CONFIRMACION, "espera confirmación")
    clear_draft(PHONE)


async def caso_3(v: Verificador) -> None:
    await _turno(_draft(PEDIDO, "confirmacion"), "cambia el bidón a nuevo")
    v.check(
        _productos() == _sin_orden([{"nombre_producto": "Bidón 20L Nuevo", "cantidad": 1}, USB]),
        "1x Bidón 20L Nuevo, dispensador intacto",
    )
    clear_draft(PHONE)


async def caso_4(v: Verificador) -> None:
    await _turno(_draft(PEDIDO, "confirmacion"), "mejor de 12 litros el bidón")
    v.check(
        _productos() == _sin_orden([{"nombre_producto": "Bidón 12L Recarga", "cantidad": 1}, USB]),
        "1x Bidón 12L Recarga, dispensador intacto",
    )
    clear_draft(PHONE)


async def caso_5(v: Verificador) -> None:
    llm = [
        {"nombre_producto": "Dispensador USB", "cantidad": 1, "operacion": "quitar"},
        {"nombre_producto": "Dispensador Básico", "cantidad": 1, "operacion": "agregar"},
    ]
    await _turno(_draft(PEDIDO, "confirmacion"), "mejor el dispensador básico", llm)
    v.check(_productos() == _sin_orden([BIDON, BASICO]), "un solo dispensador, el Básico (no se duplica)")
    clear_draft(PHONE)


async def caso_6(v: Verificador) -> None:
    mensaje = "quiero los bidones de recarga"
    v.check(
        _cambio_atributo_simple(PEDIDO_AMBIGUO, _normalizar_texto(mensaje), await _catalogo_fake()) is None,
        "dos líneas de bidones: no se resuelve en código (ambiguo)",
    )
    texto = await _turno(_draft(PEDIDO_AMBIGUO, "confirmacion"), mensaje)
    v.check(_productos() == _sin_orden(PEDIDO_AMBIGUO), "el pedido no cambia")
    v.check(not texto.startswith("¿Quieres cambiar"), "no pregunta cambiar/agregar")
    clear_draft(PHONE)


async def caso_7(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "algo_mas"), "sí, quiero un dispensador básico", AGREGAR_BASICO)
    v.check(_productos() == _sin_orden([BIDON, USB, BASICO]), "se suma: USB y Básico")
    v.check(not texto.startswith("¿Quieres cambiar"), "no pregunta cambiar/agregar")
    clear_draft(PHONE)


async def caso_8(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "quiero también un dispensador básico", AGREGAR_BASICO)
    v.check(_productos() == _sin_orden([BIDON, USB, BASICO]), "se suma: USB y Básico")
    v.check(not texto.startswith("¿Quieres cambiar"), "no pregunta cambiar/agregar")
    clear_draft(PHONE)


async def caso_9(v: Verificador) -> None:
    respuesta = "El Dispensador Básico cuesta $5.000."
    texto = await _turno(
        _draft(PEDIDO, "confirmacion"),
        "¿cuánto cuesta el dispensador básico?",
        intencion="consulta_precio",
        respuesta_sugerida=respuesta,
    )
    v.check(texto.startswith(respuesta), "responde la consulta")
    v.check(_productos() == _sin_orden(PEDIDO), "el pedido no cambia")
    v.check(not (get_draft(PHONE) or {}).get("correccion_pendiente"), "no deja pregunta pendiente")
    v.check(not texto.startswith(("¿Quieres cambiar", "No entendí", "No pude")), "sin pregunta ni aviso")
    clear_draft(PHONE)


async def caso_10(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "quiero el dispensador USB")
    v.check(texto == NO_ENTENDIDA, "MENSAJE_MODIFICACION_NO_ENTENDIDA")
    v.check(_productos() == _sin_orden(PEDIDO), "el pedido no cambia (no suma otro USB)")
    clear_draft(PHONE)


async def caso_11(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "quiero el dispensador azul")
    v.check(
        texto == NO_ENTENDIDA or texto.startswith("No encontré"),
        "aviso de no encontrado o MENSAJE_MODIFICACION_NO_ENTENDIDA",
    )
    v.check("Resumen de tu pedido" not in texto, "no repite el resumen sin aviso")
    v.check(_productos() == _sin_orden(PEDIDO), "el pedido no cambia")
    clear_draft(PHONE)


async def caso_12(v: Verificador) -> None:
    texto = await _turno(_draft(PEDIDO, "confirmacion"), "cambia eso")
    v.check(
        texto == MENSAJE_CAMBIO_NO_APLICADO.format(pedido=_resumen_productos_corto(PEDIDO)),
        "MENSAJE_CAMBIO_NO_APLICADO (texto del PR #117)",
    )
    clear_draft(PHONE)


async def caso_13(v: Verificador) -> None:
    mensaje = "solo el dispensador básico"
    v.check(
        _cambio_atributo_simple(PEDIDO, _normalizar_texto(mensaje), await _catalogo_fake()) is None,
        "'solo': el helper no lo resuelve (podría querer quitar el bidón)",
    )
    texto = await _turno(_draft(PEDIDO, "confirmacion"), mensaje)
    v.check(
        texto == MENSAJE_CAMBIO_NO_APLICADO.format(pedido=_resumen_productos_corto(PEDIDO)),
        "MENSAJE_CAMBIO_NO_APLICADO ('solo' es modificación explícita)",
    )
    v.check(_productos() == _sin_orden(PEDIDO), "no se reemplaza nada en código")
    clear_draft(PHONE)


CASOS = [
    ("1", "'Disculpa, quiero el dispensador básico': pregunta cambiar/agregar; (a) cambiar (b) agregar (c) no (d) sí", caso_1),
    ("2", "'mejor el dispensador básico': reemplaza directo", caso_2),
    ("3", "'cambia el bidón a nuevo': 1x Bidón 20L Nuevo", caso_3),
    ("4", "'mejor de 12 litros el bidón': 1x Bidón 12L Recarga", caso_4),
    ("5", "'mejor el dispensador básico' con el LLM devolviendo quitar+agregar: no se duplica", caso_5),
    ("6", "[2x 12L Recarga, 1x 20L Nuevo] + 'quiero los bidones de recarga': ambiguo", caso_6),
    ("7", "'¿algo más?' + 'sí, quiero un dispensador básico': se suma sin preguntar", caso_7),
    ("8", "'quiero también un dispensador básico': se suma sin preguntar", caso_8),
    ("9", "'¿cuánto cuesta el dispensador básico?' (consulta_precio): sin cambio, pregunta ni aviso", caso_9),
    ("10", "'quiero el dispensador USB' (el mismo): MENSAJE_MODIFICACION_NO_ENTENDIDA", caso_10),
    ("11", "'quiero el dispensador azul' (no existe): aviso, nunca el mismo resumen", caso_11),
    ("12", "'cambia eso': MENSAJE_CAMBIO_NO_APLICADO", caso_12),
    ("13", "'solo el dispensador básico': no se resuelve en código, MENSAJE_CAMBIO_NO_APLICADO", caso_13),
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
