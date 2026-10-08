"""
Script temporal para validar el bug del 2026-10-08: un mensaje con varias
líneas de bidón de distinta capacidad y sin variante ("3 de 20 y 1 de 12")
perdía líneas. Valida el parser (_lineas_bidon / _menciones_productos), la
aclaración de varias capacidades juntas (_resolver_aclaraciones_bidon) y la
validación de unidades por línea (_productos_no_registrados), a través de
procesar_mensaje y contra la BD.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 569333001xx.
Al empezar y al terminar, el script BORRA de ese rango los clientes (con sus
pedidos y detalles), los mensajes de mensaje_whatsapp y las filas de
conversacion_bot. No toca ningún otro cliente ni pedido. Las filas de
auditoria no se borran (son el registro histórico).

No envía mensajes: procesar_mensaje devuelve el texto en vez de enviarlo.

Uso: python -m scripts.test_cantidades_por_linea
"""

import asyncio

from sqlalchemy import text

from app.core.database import SessionLocal
from app.models import Cliente
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import (
    _datos_cliente,
    _menciones_productos,
    _normalizar_texto,
    _productos_no_registrados,
    _responder_siguiente_paso,
    procesar_mensaje,
)

RANGO_TELEFONOS_SQL = r"^(56)?9333001\d{2}$"

CLIENTE = {
    "telefono": "56933300101",
    "nombre": "Lineas Prueba",
    "direccion": "Av. Las Capacidades 1220, Viña del Mar",
}

MENSAJE_BUG = "3 de 20 y 1 de 12"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar_rango() -> None:
    ids_sql = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    await _ejecutar(
        f"delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id in ({ids_sql}))",
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from mensaje_whatsapp where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from conversacion_bot where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    clear_draft(CLIENTE["telefono"])


async def _crear_cliente() -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion) values (:nombre, :telefono, :direccion) returning id",
        **CLIENTE,
    )
    return result.scalar_one()


async def _conversar(turnos: list[str]) -> list[str]:
    phone = CLIENTE["telefono"]
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        draft = get_draft(phone) or {}
        print(f"  Bot [{draft.get('paso')}/{draft.get('estado')}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


def _es_resumen(texto: str) -> bool:
    return "Resumen de tu pedido" in texto and "¿Confirmas el pedido?" in texto


def _productos_draft() -> dict[str, int]:
    draft = get_draft(CLIENTE["telefono"]) or {}
    return {p["nombre_producto"]: p["cantidad"] for p in draft.get("productos") or []}


class Verificador:
    def __init__(self) -> None:
        self.errores: list[str] = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'OK' if condicion else 'FALLO'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


async def _hasta_aclaracion(v: Verificador) -> None:
    """Primer mensaje del bug: el bot pregunta nuevo/recarga de ambas
    capacidades a la vez, sin agregar ni perder nada."""
    respuesta = (await _conversar([MENSAJE_BUG]))[0]
    draft = get_draft(CLIENTE["telefono"]) or {}
    pendientes = draft.get("aclaracion_pendiente")
    v.check(
        isinstance(pendientes, list)
        and sorted((p["capacidad_litros"], p["cantidad"]) for p in pendientes) == [(12, 1), (20, 3)],
        f"quedan pendientes 3x 20L y 1x 12L: {pendientes}",
    )
    v.check(not draft.get("productos"), "no asume variante (no agrega productos)")
    v.check(
        "3 bidones de 20L" in respuesta and "bidón de 12L" in respuesta and "nuevos" in respuesta and "recarga" in respuesta,
        "pregunta nuevo/recarga para ambas capacidades",
    )
    v.check(draft.get("unidades_pedidas", {}).get("Bidón 12L") == 1, "registra 1 unidad pedida de 12L")


async def _hasta_resumen(v: Verificador, respuesta_variantes: str, esperado: dict[str, int]) -> str:
    await _hasta_aclaracion(v)
    respuestas = await _conversar([respuesta_variantes])
    v.check(_productos_draft() == esperado, f"'{respuesta_variantes}' -> {esperado}")
    v.check(not (get_draft(CLIENTE["telefono"]) or {}).get("aclaracion_pendiente"), "no queda nada por aclarar")
    v.check(CLIENTE["direccion"] in respuestas[0], "avanza a confirmar la dirección")
    respuestas = await _conversar(["si", "no"])
    resumen = respuestas[-1]
    v.check(_es_resumen(resumen), "muestra el resumen")
    v.check("no quedó registrado" not in resumen, "sin aviso de productos no registrados")
    for nombre, cantidad in esperado.items():
        v.check(f"{cantidad}x {nombre}" in resumen, f"el resumen trae {cantidad}x {nombre}")
    return resumen


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    esperados = {
        "3 de 20 y 1 de 12": [(3, 20, None), (1, 12, None)],
        "quiero 3 bidones de 20 y 1 de 12": [(3, 20, None), (1, 12, None)],
        "necesito 3 bidones de 20 litros y 1 de 12 litros": [(3, 20, None), (1, 12, None)],
        "3 bidones de 20 y uno de 12": [(3, 20, None), (1, 12, None)],
        "3 de 20 recarga y 1 de 12 nuevo": [(3, 20, "Recarga"), (1, 12, "Nuevo")],
        "tres de 20 y uno de 12": [(3, 20, None), (1, 12, None)],
        "3x20 y 1x12": [(3, 20, None), (1, 12, None)],
        "3 de 20 recarga y 1 de 12": [(3, 20, "Recarga"), (1, 12, None)],
        "2 de 20 litros y 1 de 12 litros": [(2, 20, None), (1, 12, None)],
        "2 de 20L más 1 de 12": [(2, 20, None), (1, 12, None)],
        "3 de 20, además 1 de 12": [(3, 20, None), (1, 12, None)],
        # Comportamiento previo que se mantiene.
        "quiero 5 bidones": [(5, None, None)],
        "2 bidones de 20 litros recarga y 1 de 12 nuevo": [(2, 20, "Recarga"), (1, 12, "Nuevo")],
        "4 bidones, 2 recargas y 2 nuevos": [(2, None, "Recarga"), (2, None, "Nuevo")],
        # Falsos positivos: no son bidones.
        "el 5 de octubre": [],
        "a las 12": [],
        "una dirección nueva": [],
        "mi pedido nuevo": [],
        "llego a 20 cuadras": [],
    }
    for mensaje, esperado in esperados.items():
        obtenido = [
            (m["cantidad"], m.get("capacidad"), m.get("variante"))
            for m in _menciones_productos(_normalizar_texto(mensaje))
            if m["familia"] == "Bidón"
        ]
        v.check(obtenido == esperado, f"{mensaje!r} -> {obtenido}")


async def caso_b(ctx: dict, v: Verificador) -> None:
    esperado = {"Bidón 20L Recarga": 3, "Bidón 12L Nuevo": 1}
    await _hasta_resumen(v, "las de 20 recarga y la de 12 nuevo", esperado)
    await _conversar(["SI"])
    result = await _ejecutar(
        "select p.nombre, d.cantidad_solicitada from detalle_pedido d join producto p on p.id = d.producto_id "
        "join pedido pe on pe.id = d.pedido_id where pe.cliente_id = :id",
        id=ctx["cliente_id"],
    )
    detalles = {nombre: cantidad for nombre, cantidad in result.all()}
    v.check(detalles == esperado, f"el pedido confirmado tiene 3x 20L Recarga y 1x 12L Nuevo: {detalles}")


async def caso_c(ctx: dict, v: Verificador) -> None:
    await _hasta_resumen(v, "todos recarga", {"Bidón 20L Recarga": 3, "Bidón 12L Recarga": 1})
    await _conversar(["cancelar"])
    clear_draft(CLIENTE["telefono"])
    await _hasta_resumen(v, "la de 12 nuevo y las de 20 recarga", {"Bidón 20L Recarga": 3, "Bidón 12L Nuevo": 1})
    await _conversar(["cancelar"])


async def caso_d(ctx: dict, v: Verificador) -> None:
    await _hasta_resumen(v, "las de 20 recarga y la de 12 nuevo", {"Bidón 20L Recarga": 3, "Bidón 12L Nuevo": 1})
    respuesta = (await _conversar(["mejor 2 de 20"]))[0]
    v.check(
        _productos_draft() == {"Bidón 20L Recarga": 2, "Bidón 12L Nuevo": 1},
        "baja solo los de 20L y deja intacto el de 12L",
    )
    v.check(_es_resumen(respuesta) and "2x Bidón 20L Recarga" in respuesta, "vuelve al resumen actualizado")
    v.check("no quedó registrado" not in respuesta, "sin falso positivo de unidades perdidas")
    await _conversar(["cancelar"])


async def caso_e(ctx: dict, v: Verificador) -> None:
    pedidas = {"Bidón": 4, "Bidón 20L": 3, "Bidón 12L": 1}
    perdido = {"productos": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 4}], "unidades_pedidas": pedidas}
    v.check(_productos_no_registrados(perdido) == {"Bidón 12L": 1}, "detecta el 12L perdido aunque el total cuadre")
    completo = {
        "productos": [
            {"nombre_producto": "Bidón 20L Recarga", "cantidad": 3},
            {"nombre_producto": "Bidón 12L Nuevo", "cantidad": 1},
        ],
        "unidades_pedidas": pedidas,
    }
    v.check(_productos_no_registrados(completo) == {}, "sin falso positivo con las dos líneas")
    pendiente = {
        "productos": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 3}],
        "aclaracion_pendiente": {"capacidad_litros": 12, "cantidad": 1},
        "unidades_pedidas": pedidas,
    }
    v.check(_productos_no_registrados(pendiente) == {}, "el 12L pendiente de aclarar cuenta como pedido")
    v.check(
        _productos_no_registrados({"productos": [], "unidades_pedidas": {"Bidón": 4, "Dispensador": 1}})
        == {"Bidón": 4, "Dispensador": 1},
        "registros por familia (drafts anteriores) siguen funcionando",
    )

    # En el flujo: no muestra el resumen y le dice qué falta.
    async with SessionLocal() as session:
        cliente = _datos_cliente(await session.get(Cliente, ctx["cliente_id"]))
    draft = {**perdido, "intencion": "pedido", "usa_direccion_habitual": True, "algo_mas_respondido": True}
    paso, texto = await _responder_siguiente_paso(CLIENTE["telefono"], draft, cliente)
    print(f"  Bot [{paso}]: {texto}")
    v.check(paso != "confirmacion" and not _es_resumen(texto), "no muestra el resumen")
    v.check("1x Bidón 12L" in texto, "le dice que falta 1x Bidón 12L")
    clear_draft(CLIENTE["telefono"])


CASOS = [
    ("a", "Parser: todas las líneas de bidón, con y sin variante; sin falsos positivos", caso_a),
    ("b", "'3 de 20 y 1 de 12' completo: pregunta ambas, confirma 3x 20L Recarga y 1x 12L Nuevo", caso_b),
    ("c", "Respuestas 'todos recarga' y 'la de 12 nuevo y las de 20 recarga'", caso_c),
    ("d", "'mejor 2 de 20' baja solo los de 20L, sin falso positivo", caso_d),
    ("e", "_productos_no_registrados compara por capacidad", caso_e),
]


async def main() -> None:
    await _limpiar_rango()
    resultados = []
    try:
        ctx = {"cliente_id": await _crear_cliente()}
        print(f"Cliente de prueba: id={ctx['cliente_id']} {CLIENTE}")
        for letra, descripcion, caso in CASOS:
            print("#" * 70)
            print(f"Caso {letra}: {descripcion}")
            print("#" * 70)
            clear_draft(CLIENTE["telefono"])
            v = Verificador()
            try:
                await caso(ctx, v)
            except Exception as exc:
                print(f"    [ERROR] {exc!r}")
                v.errores.append(repr(exc))
            resultados.append((letra, descripcion, not v.errores))
            print()
    finally:
        await _limpiar_rango()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
