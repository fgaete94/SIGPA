"""
Script temporal para validar las correcciones de cantidad resueltas en código
(pruebas reales por WhatsApp con clientes existentes, 2026-11-26):

A. Con una aclaración de variante pendiente ("¿1000 bidón(es) de 20L ...?"):
   A1. "Perdón me equivoqué solo quiero 10" deja la línea pendiente en 10.
   A2. "10 bidones de 20 litros" sin señal de corrección pregunta "¿Son 10
       en total o sumo 10 a los 1000 que tenías?" y resuelve con la
       respuesta siguiente; con señal ("solo 10 bidones de 20 litros")
       reemplaza directo.
B. Con el pedido armado y "¿Despachamos a ...?" pendiente, una sola línea
   de bidones: "Me gustaría 10 bidoens" (errata) y "Que sea solamente 1"
   fijan la cantidad de esa línea.
C. "Quiero solamente 1 bidón" con una sola línea de 20L la fija en 1, sin
   preguntar la capacidad; "quiero solamente 1 de 12" es otra línea.
D. Variantes ("solo 10", "mejor que sean 8", "son 5", "me equivoqué, 12") y
   varias líneas candidatas ("¿A cuál producto le dejo N?").
E. Falsos positivos que NO corrigen: "agrega 5 bidones" suma, "quítale 2"
   resta, "solo agua" no es cantidad, "Los Leones 456" y una dirección con
   número no cambian el pedido; cero y la cota técnica siguen igual.
F. Erratas de producto en el parser (sin LLM): "bidoens", "bidonez",
   "vidones" sí; "bien", "dirección", "bidé" no.

No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 56933301000 a
56933301098 (569333010xx; el 99 es la ejecutiva de prueba). Al empezar y al
terminar (también si falla), el script BORRA de ese rango los clientes (con
sus pedidos, detalles, productos habituales y sus filas de auditoria por ID
exacto), los mensajes de mensaje_whatsapp, la conversación y el borrador. No
toca ningún otro cliente.

Los turnos llaman al LLM real. Nunca se envía nada a Meta: los envíos se
reemplazan por fakes que solo registran en mensaje_whatsapp, y _post_a_meta
por un centinela que falla la prueba si algo se escapa. n8n no se llama.

Uso: python -m scripts.test_correcciones_cantidad
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.draft_store as draft_store
import app.services.notificacion_ejecutiva as notificacion_ejecutiva
import app.services.order_flow as order_flow
import app.services.whatsapp_client as whatsapp_client
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.conversacion_bot_service import marcar_activa
from app.services.draft_store import clear_draft, get_draft
from app.services.mensaje_whatsapp_service import registrar_mensaje
from app.services.order_flow import procesar_mensaje

RANGO_TELEFONOS_SQL = r"^(56)?9333010\d{2}$"
EJECUTIVA_PRUEBA = "56933301099"

CHILE = ZoneInfo("America/Santiago")
DENTRO = datetime(2026, 10, 8, 10, 0, tzinfo=CHILE)  # jueves 10:00

B12N, B12R = "Bidón 12L Nuevo", "Bidón 12L Recarga"
B20N, B20R = "Bidón 20L Nuevo", "Bidón 20L Recarga"
DUSB = "Dispensador USB"

_siguiente = iter(range(0, 99))


def _cliente(nombre: str) -> dict:
    n = next(_siguiente)
    return {
        "telefono_bd": f"+569333010{n:02d}",
        "telefono": f"569333010{n:02d}",
        "nombre": nombre,
        "direccion": f"Av. Prueba {n + 1}00, Viña del Mar",
    }


# --------------------------------------------------------------------------
# Fakes de envío (nada sale a Meta)
# --------------------------------------------------------------------------

fugas: list[dict] = []


async def _fake_text(to, message):
    await registrar_mensaje(telefono=to, direccion="saliente", tipo="text", contenido=message,
                            meta_message_id="wamid.fake", estado="enviado")
    return {"messages": [{"id": "wamid.fake"}]}


async def _fake_template(to, template_name, language_code, body_params, texto_registro):
    fugas.append({"to": to, "tipo": "template inesperada"})
    return None


async def _centinela_meta(to, payload, tipo, contenido):
    fugas.append({"to": to, "tipo": tipo})
    return None


def _instalar_fakes() -> None:
    notificacion_ejecutiva.send_whatsapp_template = _fake_template
    order_flow.send_whatsapp_message = _fake_text
    whatsapp_route.send_whatsapp_message = _fake_text
    whatsapp_client._post_a_meta = _centinela_meta
    order_flow._ahora = lambda: DENTRO
    settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""
    settings.EJECUTIVA_PHONE = EJECUTIVA_PRUEBA
    settings.META_WHATSAPP_TOKEN = ""


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


_TEL = "regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
_IDS = f"select id from cliente where {_TEL}"
_PEDIDOS = f"select id from pedido where cliente_id in ({_IDS})"

telefonos_usados: list[str] = []


async def _limpiar_rango() -> None:
    for sql in (
        f"delete from auditoria where (entidad = 'cliente' and entidad_id in ({_IDS}))"
        f" or (entidad = 'pedido' and entidad_id in ({_PEDIDOS}))",
        f"delete from detalle_pedido where pedido_id in ({_PEDIDOS})",
        f"delete from mensaje_whatsapp where pedido_id in ({_PEDIDOS}) or cliente_id in ({_IDS}) or {_TEL}",
        f"delete from pedido where cliente_id in ({_IDS})",
        f"delete from cliente_producto_habitual where cliente_id in ({_IDS})",
        f"delete from cliente where id in ({_IDS})",
        f"delete from conversacion_bot where {_TEL}",
        f"delete from borrador_pedido where {_TEL}",
    ):
        await _ejecutar(sql, rango=RANGO_TELEFONOS_SQL)
    for telefono in telefonos_usados + [EJECUTIVA_PRUEBA]:
        draft_store._drafts.pop(telefono, None)
        draft_store._persistidos.pop(telefono, None)
        draft_store._pendientes_borrar.discard(telefono)
        order_flow._derivaciones_avisadas.pop(telefono, None)


async def _contar_rango() -> dict[str, int]:
    conteos = {}
    for tabla in ("cliente", "mensaje_whatsapp", "conversacion_bot", "borrador_pedido"):
        conteos[tabla] = (
            await _ejecutar(f"select count(*) from {tabla} where {_TEL}", rango=RANGO_TELEFONOS_SQL)
        ).scalar()
    conteos["pedido"] = (await _ejecutar(f"select count(*) from pedido where cliente_id in ({_IDS})",
                                         rango=RANGO_TELEFONOS_SQL)).scalar()
    conteos["detalle_pedido"] = (await _ejecutar(
        f"select count(*) from detalle_pedido where pedido_id in ({_PEDIDOS})", rango=RANGO_TELEFONOS_SQL
    )).scalar()
    return conteos


async def _crear_cliente(datos: dict) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :lat, :lon) returning id",
        nombre=datos["nombre"], telefono=datos["telefono_bd"], direccion=datos["direccion"],
        lat=Decimal("-33.024500"), lon=Decimal("-71.551800"),
    )
    return result.scalar_one()


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _nuevo_cliente(nombre: str) -> str:
    datos = _cliente(nombre)
    telefonos_usados.append(datos["telefono"])
    await _crear_cliente(datos)
    phone = datos["telefono"]
    await marcar_activa(phone)
    clear_draft(phone)
    return phone


async def _decir(phone: str, turno: str) -> str:
    print(f"  Cliente: {turno}")
    respuesta = await procesar_mensaje(phone, "text", turno, None)
    paso = (get_draft(phone) or {}).get("paso")
    print(f"  Bot [{paso}]: {respuesta}")
    return respuesta


def _lineas(phone: str) -> dict[str, int]:
    return {p["nombre_producto"]: p["cantidad"] for p in (get_draft(phone) or {}).get("productos") or []}


def _pendientes(phone: str) -> list[tuple]:
    aclaracion = (get_draft(phone) or {}).get("aclaracion_pendiente")
    return [(a.get("capacidad_litros"), a.get("cantidad")) for a in order_flow._lista_aclaraciones(aclaracion)]


def _paso(phone: str) -> str | None:
    return (get_draft(phone) or {}).get("paso")


def _pregunta_variante(cantidad: int, capacidad: int) -> str:
    return order_flow.PREGUNTA_ACLARACION_BIDON.format(cantidad=cantidad, capacidad=capacidad)


class Verificador:
    def __init__(self):
        self.resultados: list[tuple[str, bool, str]] = []
        self.caso = ""

    def check(self, condicion: bool, descripcion: str) -> None:
        self.resultados.append((self.caso, bool(condicion), descripcion))
        print(f"    [{'OK' if condicion else 'FALLA'}] {descripcion}")


async def _con_aclaracion_1000(v: Verificador, nombre: str) -> str:
    phone = await _nuevo_cliente(nombre)
    r = await _decir(phone, "quiero 1000 bidones de 20")
    v.check(_pregunta_variante(1000, 20) in r, "pregunta nuevo o recarga por los 1000 de 20L")
    return phone


async def _con_linea(v: Verificador, nombre: str, cantidad: int) -> str:
    """Cliente existente con N bidones de 20L recarga y "¿Despachamos a
    ...?" pendiente."""
    phone = await _nuevo_cliente(nombre)
    r = await _decir(phone, f"quiero {cantidad} bidones de 20 recarga")
    v.check(_lineas(phone) == {B20R: cantidad} and _paso(phone) == "confirmar_direccion",
            f"{cantidad}x Bidón 20L Recarga y '¿Despachamos a ...?' pendiente")
    return phone


# --------------------------------------------------------------------------
# A. Aclaración de variante pendiente
# --------------------------------------------------------------------------


async def caso_a1(v: Verificador) -> None:
    v.caso = "A1 me equivoqué solo 10"
    print("\n=== A1: '¿1000 bidón(es)...?' → 'Perdón me equivoqué solo quiero 10' ===")
    phone = await _con_aclaracion_1000(v, "Andrea")
    r = await _decir(phone, "Perdón me equivoqué solo quiero 10")
    v.check(_pendientes(phone) == [(20, 10)], f"la línea pendiente pasa a 10 ({_pendientes(phone)})")
    v.check(_pregunta_variante(10, 20) in r, "pregunta '¿10 bidón(es) de 20L ...?'")
    v.check(_lineas(phone) == {}, "sin líneas agregadas")


async def caso_a2(v: Verificador) -> None:
    for respuesta, esperado in (("en total", 10), ("sumalos", 1010), ("10", 10)):
        v.caso = f"A2 sin señal → '{respuesta}'"
        print(f"\n=== A2: '10 bidones de 20 litros' sin señal → '{respuesta}' ===")
        phone = await _con_aclaracion_1000(v, f"Alan{esperado}")
        r = await _decir(phone, "10 bidones de 20 litros")
        v.check("¿Son 10 en total o sumo 10 a los 1000 que tenías?" in r, "pregunta si es en total o se suma")
        v.check(_pendientes(phone) == [(20, 1000)], f"no suma todavía ({_pendientes(phone)})")
        r = await _decir(phone, respuesta)
        v.check(_pendientes(phone) == [(20, esperado)], f"queda pendiente {esperado} ({_pendientes(phone)})")
        v.check(_pregunta_variante(esperado, 20) in r, f"pregunta '¿{esperado} bidón(es) de 20L ...?'")

    v.caso = "A2 con señal"
    print("\n=== A2: 'solo 10 bidones de 20 litros' (con señal) ===")
    phone = await _con_aclaracion_1000(v, "Amalia")
    r = await _decir(phone, "solo 10 bidones de 20 litros")
    v.check(_pendientes(phone) == [(20, 10)], f"reemplaza: pendiente 10 ({_pendientes(phone)})")
    v.check("¿Son 10 en total" not in r, "no pregunta si se suma")
    await _decir(phone, "recarga")
    v.check(_lineas(phone) == {B20R: 10}, f"'recarga' deja 10x Bidón 20L Recarga ({_lineas(phone)})")


# --------------------------------------------------------------------------
# B / C. Pedido armado, "¿Despachamos a ...?" pendiente
# --------------------------------------------------------------------------


async def caso_b(v: Verificador) -> None:
    v.caso = "B1 me gustaría 10 bidoens"
    print("\n=== B1: 1010x 20L Recarga → 'Me gustaría 10 bidoens' ===")
    phone = await _con_linea(v, "Bruno", 1010)
    r = await _decir(phone, "Me gustaría 10 bidoens")
    v.check(_lineas(phone) == {B20R: 10}, f"fija la línea en 10 ({_lineas(phone)})")
    v.check("10x Bidón 20L Recarga" in r and "1010" not in r, "dice el pedido actualizado")
    v.check("¿Despachamos a" in r and _paso(phone) == "confirmar_direccion", "vuelve a preguntar la dirección")

    v.caso = "B2 que sea solamente 1"
    print("\n=== B2: 10x 20L Recarga → 'Que sea solamente 1' ===")
    phone = await _con_linea(v, "Beatriz", 10)
    r = await _decir(phone, "Que sea solamente 1")
    v.check(_lineas(phone) == {B20R: 1}, f"fija la línea en 1 ({_lineas(phone)})")
    v.check("¿Despachamos a" in r, "vuelve a preguntar la dirección")


async def caso_c(v: Verificador) -> None:
    v.caso = "C1 solamente 1 bidón"
    print("\n=== C1: 10x 20L Recarga → 'Quiero solamente 1 bidón' ===")
    phone = await _con_linea(v, "Carlos", 10)
    r = await _decir(phone, "Quiero solamente 1 bidón")
    v.check(_lineas(phone) == {B20R: 1}, f"fija la línea de 20L en 1 ({_lineas(phone)})")
    v.check("12L" not in r and not _pendientes(phone), "no pregunta la capacidad ni agrega otra línea")

    v.caso = "C1b otra capacidad"
    print("\n=== C1b: 10x 20L Recarga → 'quiero solamente 1 de 12' (otra línea) ===")
    phone = await _con_linea(v, "Cecilia", 10)
    await _decir(phone, "quiero solamente 1 de 12")
    total_12 = sum(n for nombre, n in _lineas(phone).items() if "12L" in nombre) + sum(
        n for cap, n in _pendientes(phone) if cap == 12
    )
    v.check(_lineas(phone).get(B20R) == 10, "la línea de 20L sigue en 10")
    v.check(total_12 == 1, f"el de 12L va aparte (líneas {_lineas(phone)}, pendientes {_pendientes(phone)})")


# --------------------------------------------------------------------------
# D. Variantes y varias líneas candidatas
# --------------------------------------------------------------------------


async def caso_d(v: Verificador) -> None:
    v.caso = "D1 solo 10 (aclaración)"
    print("\n=== D1: '¿1000 bidón(es)...?' → 'solo 10' ===")
    phone = await _con_aclaracion_1000(v, "Daniel")
    await _decir(phone, "solo 10")
    v.check(_pendientes(phone) == [(20, 10)], f"pendiente 10 ({_pendientes(phone)})")

    for mensaje, esperado in (("mejor que sean 8", 8), ("son 5", 5), ("me equivoqué, 12", 12), ("solo 10", 10)):
        v.caso = f"D2 '{mensaje}'"
        print(f"\n=== D2: 3x 20L Recarga → '{mensaje}' ===")
        phone = await _con_linea(v, f"Dora{esperado}", 3)
        r = await _decir(phone, mensaje)
        v.check(_lineas(phone) == {B20R: esperado}, f"fija la línea en {esperado} ({_lineas(phone)})")
        v.check("¿Despachamos a" in r, "vuelve a preguntar la dirección")

    v.caso = "D3 varias candidatas"
    print("\n=== D3: 3x 20L Recarga + 2x Dispensador USB → 'solo 1' → 'el dispensador' ===")
    phone = await _nuevo_cliente("Diana")
    await _decir(phone, "quiero 3 bidones de 20 recarga y 2 dispensadores usb")
    v.check(_lineas(phone) == {B20R: 3, DUSB: 2}, f"pedido inicial ({_lineas(phone)})")
    r = await _decir(phone, "solo 1")
    v.check("¿A cuál producto le dejo 1?" in r, "pregunta a cuál línea")
    v.check(_lineas(phone) == {B20R: 3, DUSB: 2}, "no cambia nada todavía")
    await _decir(phone, "el dispensador")
    v.check(_lineas(phone) == {B20R: 3, DUSB: 1}, f"deja 1x Dispensador USB ({_lineas(phone)})")

    v.caso = "D4 nombrando el producto"
    print("\n=== D4: 3x 20L Recarga + 2x Dispensador USB → 'mejor solo 2 bidones' ===")
    phone = await _nuevo_cliente("Dino")
    await _decir(phone, "quiero 3 bidones de 20 recarga y 2 dispensadores usb")
    await _decir(phone, "mejor solo 2 bidones")
    v.check(_lineas(phone) == {B20R: 2, DUSB: 2}, f"fija solo la de bidones ({_lineas(phone)})")


# --------------------------------------------------------------------------
# E. Falsos positivos y protecciones
# --------------------------------------------------------------------------


def _bidones_totales(phone: str) -> int:
    return sum(n for nombre, n in _lineas(phone).items() if nombre.startswith("Bidón")) + sum(
        n for _, n in _pendientes(phone)
    )


async def caso_e(v: Verificador) -> None:
    v.caso = "E1 agrega 5 bidones"
    print("\n=== E1: 3x 20L Recarga → 'agrega 5 bidones' (suma) ===")
    phone = await _con_linea(v, "Elena", 3)
    await _decir(phone, "agrega 5 bidones")
    v.check(_bidones_totales(phone) == 8, f"suma: 8 bidones (líneas {_lineas(phone)}, pendientes {_pendientes(phone)})")

    v.caso = "E2 quítale 2"
    print("\n=== E2: 3x 20L Recarga → 'quítale 2' (resta) ===")
    phone = await _con_linea(v, "Emilio", 3)
    await _decir(phone, "quítale 2")
    v.check(_lineas(phone) == {B20R: 1}, f"resta: queda 1 ({_lineas(phone)})")

    v.caso = "E3 solo agua"
    print("\n=== E3: '¿4 bidón(es) de 20L...?' → 'solo agua' ===")
    phone = await _nuevo_cliente("Eva")
    await _decir(phone, "quiero 4 bidones de 20")
    r = await _decir(phone, "solo agua")
    v.check(_lineas(phone) == {B20R: 4}, f"es la variante, no una cantidad ({_lineas(phone)})")
    v.check("¿Son" not in r and "¿A cuál" not in r, "no pregunta por cantidades")

    v.caso = "E4 Los Leones 456"
    print("\n=== E4: 3x 20L Recarga → 'Los Leones 456' ===")
    phone = await _con_linea(v, "Ernesto", 3)
    await _decir(phone, "Los Leones 456")
    v.check(_lineas(phone) == {B20R: 3}, f"el pedido no cambia ({_lineas(phone)})")

    v.caso = "E5 dirección con número"
    print("\n=== E5: 3x 20L Recarga → 'no' → 'Av. Libertad 1234, depto 5' ===")
    phone = await _con_linea(v, "Esteban", 3)
    await _decir(phone, "no, es otra")
    await _decir(phone, "Av. Libertad 1234, depto 5")
    v.check(_lineas(phone) == {B20R: 3}, f"el pedido no cambia ({_lineas(phone)})")

    v.caso = "E7 '20' es capacidad"
    print("\n=== E7: 'quiero 5 bidones' → '20' (respuesta a '¿de 12L o de 20L?', no una cantidad) ===")
    phone = await _nuevo_cliente("Eugenio")
    await _decir(phone, "quiero 5 bidones")
    r = await _decir(phone, "20")
    v.check("¿Son 20 en total" not in r, "no pregunta si se suma")
    v.check(_pendientes(phone) == [(20, 5)], f"quedan 5 de 20L por aclarar ({_pendientes(phone)})")

    v.caso = "E6 protecciones"
    print("\n=== E6: 3x 20L Recarga → 'solo 0', 'solo 20000' ===")
    phone = await _con_linea(v, "Eloisa", 3)
    r = await _decir(phone, "solo 0")
    v.check(r == order_flow.MENSAJE_CANTIDAD_NO_POSITIVA, "cero: mensaje de cantidad no positiva")
    r = await _decir(phone, "solo 20000")
    v.check(r == order_flow.MENSAJE_CANTIDAD_ALTA, "sobre la cota técnica: mensaje de cantidad alta")
    v.check(_lineas(phone) == {B20R: 3}, f"el pedido no cambia ({_lineas(phone)})")


# --------------------------------------------------------------------------
# F. Erratas (sin LLM)
# --------------------------------------------------------------------------


def caso_f(v: Verificador) -> None:
    v.caso = "F erratas"
    print("\n=== F: erratas de producto en el parser ===")
    for palabra in ("bidoens", "bidonez", "vidones", "bidon", "bidones", "Bidónes"):
        v.check(order_flow._menciona_tipo("bidon", order_flow._normalizar_texto(f"quiero 2 {palabra}")),
                f"'{palabra}' es bidón")
    for palabra in ("bien", "dirección", "bidé", "video", "botones"):
        v.check(not order_flow._menciona_tipo("bidon", order_flow._normalizar_texto(f"esta {palabra}")),
                f"'{palabra}' no es bidón")


# --------------------------------------------------------------------------


async def main() -> None:
    _instalar_fakes()
    v = Verificador()
    try:
        await _limpiar_rango()
        caso_f(v)
        for caso in (caso_a1, caso_a2, caso_b, caso_c, caso_d, caso_e):
            try:
                await caso(v)
            except Exception as exc:  # un caso roto no impide correr los demás
                v.caso = caso.__name__
                v.check(False, f"excepción: {exc!r}")
        v.caso = "seguridad"
        v.check(not fugas, f"nada llegó a Meta ({len(fugas)} envíos interceptados)")
    finally:
        await _limpiar_rango()
        conteos = await _contar_rango()
        print(f"\nFilas de prueba tras la limpieza: {conteos}")
        v.caso = "limpieza"
        v.check(all(n == 0 for n in conteos.values()), "0 filas de prueba")

    print("\n=== Resumen por caso ===")
    casos_ok: dict[str, list[bool]] = {}
    for caso, ok, _ in v.resultados:
        casos_ok.setdefault(caso, []).append(ok)
    for caso, oks in casos_ok.items():
        print(f"  {caso:32s} {sum(oks)}/{len(oks)} {'OK' if all(oks) else 'FALLA'}")
    fallas = [(c, d) for c, ok, d in v.resultados if not ok]
    print(f"\nTotal: {len(v.resultados) - len(fallas)}/{len(v.resultados)} OK")
    for c, d in fallas:
        print(f"  FALLA [{c}] {d}")
    if fallas:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
