"""
Script temporal para validar, a través de procesar_mensaje y contra la BD, las
historias #90 y #91: el bot ofrece el producto habitual del cliente
(cliente_producto_habitual) cuando no tiene un pedido entregado que repetir
(#64), pregunta la cantidad, arma el pedido con el flujo estándar (resumen y
confirmación explícita) y deja pedir algo distinto (ver
_ofrecer_repetir_pedido y _responder_a_oferta_habitual en
app/services/order_flow.py).
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 569333005xx.
Al empezar y al terminar (también si falla), el script BORRA los clientes de
ese rango (con sus pedidos, detalles, mensajes, productos habituales,
conversación y borrador). Las filas de cliente_producto_habitual que crea son
solo de sus clientes de prueba; no toca las de ningún cliente real. Las filas
de auditoria no se borran (son el registro histórico).

Los casos llaman al LLM real (igual que los otros scripts de order_flow); no
se envía nada a Meta (procesar_mensaje devuelve el texto).

Uso: python -m scripts.test_pedido_habitual
"""

import asyncio
from decimal import Decimal

from sqlalchemy import text

import app.services.draft_store as draft_store
import app.services.order_flow as order_flow
from app.core.database import SessionLocal
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import procesar_mensaje

RANGO_TELEFONOS_SQL = r"^(56)?9333005\d{2}$"

HABITUAL = "Bidón 20L Recarga"
HABITUAL_12 = "Bidón 12L Recarga"


def _cliente(n: int, nombre: str, direccion: str) -> dict:
    return {
        "telefono_bd": f"+569333005{n:02d}",
        "telefono_whatsapp": f"569333005{n:02d}",
        "nombre": nombre,
        "direccion": direccion,
        "latitud": Decimal("-33.024500"),
        "longitud": Decimal("-71.551800"),
    }


UN_HABITUAL = _cliente(1, "Hilda Habitual", "Av. Libertad 5000, Viña del Mar")
DOS_HABITUALES = _cliente(2, "Dora Dosproductos", "Los Pinos 500, Quilpué")
CON_ENTREGADO = _cliente(3, "Elena Entregada", "Calle Falsa 500, Valparaíso")
HABITUAL_DE_12 = _cliente(4, "Doce Habitual", "Av. España 500, Valparaíso")
NO_REGISTRADO = "56933300599"

TELEFONOS_PRUEBA = (
    UN_HABITUAL["telefono_whatsapp"],
    DOS_HABITUALES["telefono_whatsapp"],
    CON_ENTREGADO["telefono_whatsapp"],
    HABITUAL_DE_12["telefono_whatsapp"],
    NO_REGISTRADO,
)

# Último pedido entregado de CON_ENTREGADO (gana sobre el producto habitual).
ULTIMO_PEDIDO = {"Bidón 12L Nuevo": 1}


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar_rango() -> None:
    """Borra los clientes de prueba (rango de teléfonos) con todo lo asociado:
    pedidos, detalles, mensajes, productos habituales, conversación y
    borrador. Las filas de auditoria no se borran."""
    ids_sql = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    pedidos_sql = f"select id from pedido where cliente_id in ({ids_sql})"
    tel_sql = "regexp_replace(telefono, '\\D', '', 'g') ~ :rango"
    for sql in (
        f"delete from detalle_pedido where pedido_id in ({pedidos_sql})",
        f"delete from mensaje_whatsapp where pedido_id in ({pedidos_sql}) or cliente_id in ({ids_sql}) or {tel_sql}",
        f"delete from pedido where cliente_id in ({ids_sql})",
        f"delete from cliente_producto_habitual where cliente_id in ({ids_sql})",
        f"delete from cliente where id in ({ids_sql})",
        f"delete from conversacion_bot where {tel_sql}",
        f"delete from borrador_pedido where {tel_sql}",
    ):
        await _ejecutar(sql, rango=RANGO_TELEFONOS_SQL)


async def _crear_cliente(datos: dict, habituales: list[str]) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        nombre=datos["nombre"],
        telefono=datos["telefono_bd"],
        direccion=datos["direccion"],
        latitud=datos["latitud"],
        longitud=datos["longitud"],
    )
    cliente_id = result.scalar_one()
    for nombre in habituales:
        await _ejecutar(
            "insert into cliente_producto_habitual (cliente_id, producto_id) "
            "select :cid, id from producto where nombre = :nombre",
            cid=cliente_id,
            nombre=nombre,
        )
    return cliente_id


async def _crear_pedido_entregado(cliente_id: int, lineas: dict[str, int]) -> int:
    result = await _ejecutar(
        "insert into pedido (cliente_id, estado, direccion_despacho, total, creado_en) "
        "values (:cid, cast('entregado' as estado_pedido), 'Dirección de prueba', 0, now() - interval '10 days') "
        "returning id",
        cid=cliente_id,
    )
    pedido_id = result.scalar_one()
    for nombre, cantidad in lineas.items():
        await _ejecutar(
            "insert into detalle_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario) "
            "select :pid, id, :cantidad, precio_unitario from producto where nombre = :nombre",
            pid=pedido_id,
            cantidad=cantidad,
            nombre=nombre,
        )
    return pedido_id


async def _pedidos_de(cliente_id: int) -> list[dict]:
    result = await _ejecutar("select * from pedido where cliente_id = :id order by id", id=cliente_id)
    return [dict(fila._mapping) for fila in result]


async def _lineas_de(pedido_id: int) -> dict[str, int]:
    result = await _ejecutar(
        "select p.nombre, d.cantidad_solicitada from detalle_pedido d "
        "join producto p on p.id = d.producto_id where d.pedido_id = :id",
        id=pedido_id,
    )
    return {fila.nombre: fila.cantidad_solicitada for fila in result}


async def _fila_borrador(telefono: str):
    result = await _ejecutar("select datos from borrador_pedido where telefono = :t", t=telefono)
    return result.first()


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


def reiniciar() -> None:
    """Simula un redeploy: se pierde todo lo que vive en la memoria."""
    draft_store._drafts.clear()
    draft_store._persistidos.clear()
    draft_store._pendientes_borrar.clear()


async def _conversar(phone: str, turnos: list[str]) -> list[str]:
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        paso = (get_draft(phone) or {}).get("paso")
        print(f"  Bot [{paso}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


async def _hasta_resumen(phone: str, maximo: int = 6) -> str | None:
    """Sigue el flujo estándar (confirmar dirección, "¿algo más?") hasta el
    resumen. Devuelve el último texto del bot."""
    ultimo = None
    for _ in range(maximo):
        await draft_store.cargar_draft(phone)  # tras un reinicio simulado, desde la BD
        draft = get_draft(phone) or {}
        if draft.get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION:
            break
        turno = {"confirmar_direccion": "sí", "algo_mas": "no"}.get(draft.get("paso"))
        if turno is None:
            break
        ultimo = (await _conversar(phone, [turno]))[0]
    return ultimo


def _lineas_draft(phone: str) -> dict[str, int]:
    productos = (get_draft(phone) or {}).get("productos") or []
    return {p["nombre_producto"]: p["cantidad"] for p in productos}


def _oferta_habitual(phone: str):
    return (get_draft(phone) or {}).get("oferta_habitual")


def _es_resumen(texto: str | None) -> bool:
    return bool(texto) and "Resumen de tu pedido" in texto and "¿Confirmas el pedido?" in texto


def _ofrece_habitual(texto: str) -> bool:
    return "de siempre" in texto.lower()


def _ofrece_repetir(texto: str) -> bool:
    return "último pedido" in texto.lower()


def _pregunta_cantidad(texto: str) -> bool:
    return "¿cuántos" in texto.lower()


def _reiniciar_conversacion(phone: str) -> None:
    clear_draft(phone)


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'OK' if condicion else 'FALLO'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    phone = UN_HABITUAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["hola"])
    v.check(_ofrece_habitual(respuestas[0]) and HABITUAL in respuestas[0], "'hola' -> ofrece el producto habitual")
    v.check(_oferta_habitual(phone) == {"producto": HABITUAL, "preguntas": 0}, "la oferta queda en el draft (JSON nativo)")
    v.check(not _lineas_draft(phone), "el draft sigue sin productos")

    respuestas += await _conversar(phone, ["sí"])
    v.check(_pregunta_cantidad(respuestas[1]) and HABITUAL in respuestas[1], "'sí' -> pregunta cuántos")
    v.check(not _lineas_draft(phone) and bool(_oferta_habitual(phone)), "la oferta se mantiene, sin productos")

    respuestas += await _conversar(phone, ["3"])
    v.check(_lineas_draft(phone) == {HABITUAL: 3}, f"'3' -> borrador con 3x {HABITUAL}: {_lineas_draft(phone)}")
    v.check(_oferta_habitual(phone) is None, "la oferta se quita del draft")
    v.check(f"¿Despachamos a {UN_HABITUAL['direccion']}?" in respuestas[2], "sigue el flujo estándar: confirma la dirección")

    resumen = await _hasta_resumen(phone)
    v.check(_es_resumen(resumen) and f"3x {HABITUAL}" in resumen, "muestra el resumen con 3x Bidón 20L Recarga")
    v.check(not await _pedidos_de(ctx["un_habitual"]), "sin pedido hasta la confirmación explícita")

    await _conversar(phone, ["sí"])
    pedidos = await _pedidos_de(ctx["un_habitual"])
    v.check(len(pedidos) == 1 and pedidos[0]["estado"] == "pendiente", "tras el sí: pedido creado en estado pendiente")
    if pedidos:
        v.check(await _lineas_de(pedidos[0]["id"]) == {HABITUAL: 3}, "con la línea 3x Bidón 20L Recarga")
    v.check(get_draft(phone) is None and await _fila_borrador(phone) is None, "sin borrador al confirmar")


async def caso_b(ctx: dict, v: Verificador) -> None:
    phone = UN_HABITUAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["hola", "sí, 2"])
    v.check(_lineas_draft(phone) == {HABITUAL: 2}, f"'sí, 2' directo -> 2x {HABITUAL}: {_lineas_draft(phone)}")
    v.check(f"¿Despachamos a {UN_HABITUAL['direccion']}?" in respuestas[1], "sigue con la dirección")
    resumen = await _hasta_resumen(phone)
    v.check(_es_resumen(resumen) and f"2x {HABITUAL}" in resumen, "resumen con 2x")
    v.check(len(await _pedidos_de(ctx["un_habitual"])) == 0, "sin pedido hasta el sí explícito")
    await _conversar(phone, ["ok"])
    v.check(len(await _pedidos_de(ctx["un_habitual"])) == 0, "un 'ok' no confirma el pedido")
    await _conversar(phone, ["cancelar"])


async def caso_c(ctx: dict, v: Verificador) -> None:
    phone = UN_HABITUAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["hola", "no"])
    v.check(respuestas[1].startswith("Entendido") and "qué producto" in respuestas[1].lower(), "'no' -> 'Entendido…' y pregunta qué producto")
    v.check(_oferta_habitual(phone) is None and not _lineas_draft(phone), "la oferta se quita y no hay productos")

    await _conversar(phone, ["quiero 1 dispensador usb"])
    v.check(_lineas_draft(phone) == {"Dispensador USB": 1}, f"flujo normal: {_lineas_draft(phone)}")
    resumen = await _hasta_resumen(phone)
    v.check(_es_resumen(resumen) and "1x Dispensador USB" in resumen and HABITUAL not in resumen, "resumen distinto al habitual")
    v.check(len(await _pedidos_de(ctx["un_habitual"])) == 0, "sin pedido hasta el sí explícito")
    await _conversar(phone, ["sí"])
    pedidos = await _pedidos_de(ctx["un_habitual"])
    v.check(len(pedidos) == 1, "tras el sí: pedido creado")
    if pedidos:
        v.check(await _lineas_de(pedidos[0]["id"]) == {"Dispensador USB": 1}, "el pedido difiere del habitual: 1x Dispensador USB")

    # Pedir algo distinto directamente tras la oferta, sin pasar por el "no".
    for mensaje, esperado in (
        ("quiero 1 dispensador usb", {"Dispensador USB": 1}),
        ("3 de 12 nuevo", {"Bidón 12L Nuevo": 3}),
        ("quiero otra cosa", None),
    ):
        clear_draft(phone)
        await _conversar(phone, ["hola", mensaje])
        v.check(_oferta_habitual(phone) is None, f"'{mensaje}': descarta la oferta")
        if esperado is not None:
            v.check(_lineas_draft(phone) == esperado, f"'{mensaje}': no se pierde lo que dijo: {_lineas_draft(phone)}")
        else:
            v.check(not _lineas_draft(phone), "'quiero otra cosa': sigue el flujo normal sin productos")
    clear_draft(phone)


async def caso_d(ctx: dict, v: Verificador) -> None:
    phone = UN_HABITUAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["quiero 3 de 12 nuevo"])
    v.check(not _ofrece_habitual(respuestas[0]), "primer mensaje con producto y cantidad: sin oferta")
    v.check(_oferta_habitual(phone) is None, "sin oferta en el draft")
    v.check(_lineas_draft(phone) == {"Bidón 12L Nuevo": 3}, f"flujo normal con lo pedido: {_lineas_draft(phone)}")
    clear_draft(phone)
    respuestas = await _conversar(phone, ["quiero 20 bidones de 20 recarga"])
    v.check(_oferta_habitual(phone) is None and _lineas_draft(phone) == {HABITUAL: 20}, "mención con cantidad va por el flujo normal")
    clear_draft(phone)


async def caso_e(ctx: dict, v: Verificador) -> None:
    for mensaje in ("sí", "si", "dale", "ok", "sí necesito recarga", "recarga", "sí, recarga por favor", "bidón"):
        clear_draft(UN_HABITUAL["telefono_whatsapp"])
        phone = UN_HABITUAL["telefono_whatsapp"]
        # Cómo clasifica el LLM real este primer mensaje (informativo).
        resultado = await order_flow._interpretar_con_debug(phone, mensaje, False, None)
        print(
            f"  [LLM] {mensaje!r}: intencion={resultado.get('intencion')} productos={resultado.get('productos')} "
            f"aclaracion={resultado.get('aclaracion_pendiente')}"
        )
        r = (await _conversar(phone, [mensaje]))[0]
        draft = get_draft(phone) or {}
        v.check(
            HABITUAL in r and (_ofrece_habitual(r) or _pregunta_cantidad(r)),
            f"{mensaje!r} como primer mensaje -> oferta o pregunta de cantidad",
        )
        v.check(bool(draft.get("oferta_habitual")) and not draft.get("aclaracion_pendiente") and not draft.get("productos"), "con la oferta en el draft, sin aclaración ni productos")
        v.check("capacidad" not in r.lower() and "nuevo" not in r.lower() and "12L" not in r, "no pregunta capacidad ni variante")
    # Después de la pregunta de cantidad se continúa con normalidad.
    clear_draft(UN_HABITUAL["telefono_whatsapp"])
    await _conversar(UN_HABITUAL["telefono_whatsapp"], ["sí necesito recarga", "2"])
    v.check(_lineas_draft(UN_HABITUAL["telefono_whatsapp"]) == {HABITUAL: 2}, "'sí necesito recarga' y luego '2' -> 2x del habitual")
    clear_draft(UN_HABITUAL["telefono_whatsapp"])


async def caso_f(ctx: dict, v: Verificador) -> None:
    phone = UN_HABITUAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["hola", "20"])
    v.check(respuestas[1] == order_flow.PREGUNTA_CANTIDAD_AMBIGUA, "'20' -> re-pregunta de cantidad")
    v.check(not _lineas_draft(phone) and bool(_oferta_habitual(phone)), "no crea 20 bidones; la oferta se mantiene")
    await _conversar(phone, ["20 bidones"])
    v.check(_lineas_draft(phone) == {HABITUAL: 20}, f"'20 bidones' -> 20x: {_lineas_draft(phone)}")
    clear_draft(phone)

    # Con el habitual de 12L, "12" es ambiguo y "12 x" no.
    phone12 = HABITUAL_DE_12["telefono_whatsapp"]
    respuestas = await _conversar(phone12, ["hola", "12"])
    v.check(respuestas[1] == order_flow.PREGUNTA_CANTIDAD_AMBIGUA and not _lineas_draft(phone12), "habitual de 12L: '12' -> re-pregunta")
    await _conversar(phone12, ["4"])
    v.check(_lineas_draft(phone12) == {HABITUAL_12: 4}, f"y luego '4' -> 4x {HABITUAL_12}: {_lineas_draft(phone12)}")
    clear_draft(phone12)

    # Otras formas de decir la cantidad.
    for mensaje, esperado in (("dos", 2), ("2 bidones", 2), ("2 por favor", 2), ("sí, 3", 3), ("quiero 5", 5), ("3 de 20 recarga", 3)):
        clear_draft(phone)
        await _conversar(phone, ["hola", mensaje])
        v.check(_lineas_draft(phone) == {HABITUAL: esperado}, f"{mensaje!r} -> {esperado}x: {_lineas_draft(phone)}")
    clear_draft(phone)


async def caso_g(ctx: dict, v: Verificador) -> None:
    phone = DOS_HABITUALES["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["hola"])
    v.check(not _ofrece_habitual(respuestas[0]) and not _ofrece_repetir(respuestas[0]), "con 2 productos habituales: sin oferta")
    v.check(_oferta_habitual(phone) is None, "sin oferta en el draft")
    v.check("qué producto" in respuestas[0].lower(), "sigue el flujo normal: pregunta el producto")
    clear_draft(phone)
    respuestas = await _conversar(phone, ["sí"])
    v.check(not _ofrece_habitual(respuestas[0]) and _oferta_habitual(phone) is None, "'sí' con 2 habituales tampoco ofrece")
    clear_draft(phone)


async def caso_h(ctx: dict, v: Verificador) -> None:
    phone = CON_ENTREGADO["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["hola"])
    draft = get_draft(phone) or {}
    v.check(_ofrece_repetir(respuestas[0]) and "1x Bidón 12L Nuevo" in respuestas[0], "gana la oferta de #64: último pedido")
    v.check(bool(draft.get("oferta_repetir")) and not draft.get("oferta_habitual"), "oferta_repetir en el draft, sin oferta_habitual")
    v.check(not _ofrece_habitual(respuestas[0]), "no ofrece el producto habitual")
    clear_draft(phone)
    respuestas = await _conversar(phone, ["sí"])
    v.check(_ofrece_repetir(respuestas[0]) and not _ofrece_habitual(respuestas[0]), "'sí' como primer mensaje: también la oferta de #64")
    clear_draft(phone)


async def caso_i(ctx: dict, v: Verificador) -> None:
    phone = NO_REGISTRADO
    for mensaje in ("hola", "sí", "recarga"):
        clear_draft(phone)
        respuestas = await _conversar(phone, [mensaje])
        draft = get_draft(phone) or {}
        v.check(
            not _ofrece_habitual(respuestas[0]) and not _ofrece_repetir(respuestas[0]) and not draft.get("oferta_habitual"),
            f"cliente no registrado, {mensaje!r}: sin oferta",
        )
    clear_draft(phone)


async def caso_j(ctx: dict, v: Verificador) -> None:
    phone = UN_HABITUAL["telefono_whatsapp"]
    await _conversar(phone, ["hola"])
    fila = await _fila_borrador(phone)
    v.check(fila is not None and fila.datos.get("oferta_habitual") == {"producto": HABITUAL, "preguntas": 0}, "la oferta quedó guardada en borrador_pedido")
    reiniciar()
    v.check(get_draft(phone) is None, "tras el 'redeploy' la memoria está vacía")
    r = (await _conversar(phone, ["sí"]))[0]
    v.check(_pregunta_cantidad(r) and HABITUAL in r, "tras reiniciar, 'sí' -> pregunta cuántos")
    reiniciar()
    await _conversar(phone, ["3"])
    v.check(_lineas_draft(phone) == {HABITUAL: 3}, f"tras reiniciar otra vez, '3' -> 3x: {_lineas_draft(phone)}")
    reiniciar()
    resumen = await _hasta_resumen(phone)
    v.check(_es_resumen(resumen) and f"3x {HABITUAL}" in resumen, "el flujo llega al resumen aun reiniciando en medio")
    reiniciar()
    await _conversar(phone, ["sí"])
    pedidos = await _pedidos_de(ctx["un_habitual"])
    v.check(len(pedidos) == 1 and await _lineas_de(pedidos[0]["id"]) == {HABITUAL: 3}, "y el pedido final es 3x del habitual")


async def caso_k(ctx: dict, v: Verificador) -> None:
    # Una sola pregunta de cantidad: si tras ella no hay cantidad, se descarta.
    phone = UN_HABITUAL["telefono_whatsapp"]
    respuestas = await _conversar(phone, ["hola", "sí", "sí"])
    v.check(_pregunta_cantidad(respuestas[1]), "primer 'sí' -> pregunta cuántos")
    v.check(_oferta_habitual(phone) is None and not _lineas_draft(phone), "segundo 'sí' sin cantidad: se descarta la oferta")
    clear_draft(phone)
    # "cancelar" con la oferta pendiente cancela y limpia.
    await _conversar(phone, ["hola", "cancelar"])
    v.check(get_draft(phone) is None, "'cancelar' con la oferta pendiente deja sin borrador")
    clear_draft(phone)


CASOS = [
    ("a", "Un habitual: hola -> oferta, sí -> cuántos, 3 -> resumen -> pedido", caso_a),
    ("b", "'sí, 2' directo tras la oferta", caso_b),
    ("c", "#91: 'no' / algo distinto -> flujo normal y pedido distinto", caso_c),
    ("d", "#91: primer mensaje con producto y cantidad -> sin oferta", caso_d),
    ("e", "Respuestas al recordatorio como primer mensaje", caso_e),
    ("f", "Ambigüedad número = capacidad y formas de la cantidad", caso_f),
    ("g", "Dos productos habituales -> sin oferta", caso_g),
    ("h", "Con pedido entregado gana la oferta de #64", caso_h),
    ("i", "Cliente no registrado -> sin oferta", caso_i),
    ("j", "Reinicio simulado en medio del flujo", caso_j),
    ("k", "Una sola pregunta de cantidad; cancelar", caso_k),
]


async def main() -> None:
    await _limpiar_rango()
    resultados = []
    try:
        ctx = {
            "un_habitual": await _crear_cliente(UN_HABITUAL, [HABITUAL]),
            "dos_habituales": await _crear_cliente(DOS_HABITUALES, [HABITUAL, HABITUAL_12]),
            "con_entregado": await _crear_cliente(CON_ENTREGADO, [HABITUAL]),
            "habitual_12": await _crear_cliente(HABITUAL_DE_12, [HABITUAL_12]),
        }
        await _crear_pedido_entregado(ctx["con_entregado"], ULTIMO_PEDIDO)
        print(f"Clientes de prueba creados: {ctx}")

        for letra, descripcion, caso in CASOS:
            print("#" * 70)
            print(f"Caso {letra}: {descripcion}")
            print("#" * 70)
            reiniciar()
            await _ejecutar("delete from borrador_pedido where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
            await _ejecutar(
                "delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id = any(:ids) "
                "and estado = 'pendiente')",
                ids=[ctx["un_habitual"], ctx["dos_habituales"], ctx["habitual_12"]],
            )
            await _ejecutar(
                "delete from pedido where cliente_id = any(:ids) and estado = 'pendiente'",
                ids=[ctx["un_habitual"], ctx["dos_habituales"], ctx["habitual_12"]],
            )
            v = Verificador()
            try:
                await caso(ctx, v)
            except Exception as exc:
                print(f"    [ERROR] {exc!r}")
                v.errores.append(repr(exc))
            resultados.append((letra, descripcion, not v.errores))
            print()
    finally:
        for phone in TELEFONOS_PRUEBA:
            clear_draft(phone)
        await _limpiar_rango()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print(f"  {sum(ok for *_, ok in resultados)} de {len(resultados)} OK")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
