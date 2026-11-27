"""
Script temporal para validar la aclaración de variante de bidones (nuevo /
recarga) y la protección anti-bucle (bug de producción del 2026-11-26: a
"¿8 bidón(es) de 12L nuevo(s) ... o de recarga ...?" el cliente respondió
"Nuevos", "Nuevos", "Nuevo" y el bot repitió exactamente la misma pregunta
las tres veces; con "Recarga" avanzó).

A. Respuestas a la aclaración: "nuevo", "nuevos", "Nuevo", "el nuevo",
   "nuevo con envase", "recarga", "recargas", "de recarga", "solo el agua",
   para 12L y 20L, con una o varias líneas pendientes.
B. Catálogo sin la variante (Bidón 12L Nuevo fuera del catálogo, simulado con
   un monkeypatch de order_flow._catalogo, sin tocar la tabla producto): el
   bot no pregunta por lo que no puede ofrecer y sigue con la que existe.
C. Anti-bucle: misma pregunta 2 veces seguidas sin avanzar → opciones
   numeradas ("1" / "2" se entienden); otro fallo → derivación a la
   ejecutiva con el aviso único de siempre.

No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 56933300900 a
56933300989 (569333009xx sin los 99x, que usa test_planificador_recordatorio).
Al empezar y al terminar (también si falla), el script BORRA de ese rango los
clientes (con sus pedidos, detalles, productos habituales y sus filas de
auditoria por ID exacto), los mensajes de mensaje_whatsapp, la conversación
y el borrador. No toca ningún otro cliente.

Los turnos llaman al LLM real. Nunca se envía nada a Meta: los envíos
(send_whatsapp_message, send_whatsapp_template) se reemplazan por fakes que
solo registran en mensaje_whatsapp, como el envío real, y _post_a_meta se
reemplaza por un centinela que falla la prueba si algo se escapa. n8n no se
llama.

Uso: python -m scripts.test_aclaracion_variante
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

RANGO_TELEFONOS_SQL = r"^(56)?9333009[0-8]\d$"
EJECUTIVA_PRUEBA = "56933300989"

CHILE = ZoneInfo("America/Santiago")
DENTRO = datetime(2026, 10, 8, 10, 0, tzinfo=CHILE)  # jueves 10:00

B12N, B12R = "Bidón 12L Nuevo", "Bidón 12L Recarga"
B20N, B20R = "Bidón 20L Nuevo", "Bidón 20L Recarga"

PREGUNTA_PROD = order_flow.PREGUNTA_ACLARACION_BIDON.format(cantidad=8, capacidad=12)

_siguiente = iter(range(0, 89))


def _cliente(nombre: str) -> dict:
    n = next(_siguiente)
    return {
        "telefono_bd": f"+569333009{n:02d}",
        "telefono": f"569333009{n:02d}",
        "nombre": nombre,
        "direccion": f"Av. Prueba {n + 1}00, Viña del Mar",
    }


# --------------------------------------------------------------------------
# Fakes de envío (nada sale a Meta) y conteo de avisos a la ejecutiva
# --------------------------------------------------------------------------

fugas: list[dict] = []
avisos_ejecutiva: list[str] = []
_avisar_real = order_flow._avisar_ejecutiva
_catalogo_real = order_flow._catalogo


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


async def _avisar_contado(phone, tipo_cliente, nombres, fuera_de_horario):
    avisos_ejecutiva.append(phone)
    return await _avisar_real(phone, tipo_cliente, nombres, fuera_de_horario)


async def _catalogo_sin_12_nuevo():
    return [p for p in await _catalogo_real() if p["nombre"] != B12N]


def _instalar_fakes() -> None:
    notificacion_ejecutiva.send_whatsapp_template = _fake_template
    order_flow.send_whatsapp_message = _fake_text
    whatsapp_route.send_whatsapp_message = _fake_text
    whatsapp_client._post_a_meta = _centinela_meta
    order_flow._avisar_ejecutiva = _avisar_contado
    order_flow._ahora = lambda: DENTRO
    settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""  # aviso por texto libre (fake)
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
    """Cliente existente (con dirección) y conversación activa, sin
    borrador."""
    datos = _cliente(nombre)
    telefonos_usados.append(datos["telefono"])
    await _crear_cliente(datos)
    phone = datos["telefono"]
    await marcar_activa(phone)
    clear_draft(phone)
    order_flow._derivaciones_avisadas.pop(phone, None)
    return phone


async def _decir(phone: str, turno: str) -> str:
    print(f"  Cliente: {turno}")
    respuesta = await procesar_mensaje(phone, "text", turno, None)
    paso = (get_draft(phone) or {}).get("paso")
    print(f"  Bot [{paso}]: {respuesta}")
    return respuesta


def _lineas(phone: str) -> dict[str, int]:
    return {p["nombre_producto"]: p["cantidad"] for p in (get_draft(phone) or {}).get("productos") or []}


def _pendientes(phone: str) -> list[dict]:
    return order_flow._lista_aclaraciones((get_draft(phone) or {}).get("aclaracion_pendiente"))


def _pregunta_variante(respuesta: str) -> bool:
    texto = respuesta.lower()
    return "nuevo(s) (con envase) o de recarga" in texto or "nuevos (con envase) o de recarga" in texto


class Verificador:
    def __init__(self):
        self.resultados: list[tuple[str, bool, str]] = []
        self.caso = ""

    def check(self, condicion: bool, descripcion: str) -> None:
        self.resultados.append((self.caso, bool(condicion), descripcion))
        print(f"    [{'OK' if condicion else 'FALLA'}] {descripcion}")


# --------------------------------------------------------------------------
# A. Respuestas a la aclaración
# --------------------------------------------------------------------------


async def caso_produccion(v: Verificador) -> None:
    v.caso = "A0 producción 8 de 12"
    print("\n=== A0: conversación de producción ('Quiero 8 bidones de 12' → 'Nuevos') ===")
    phone = await _nuevo_cliente("Paula")
    r = await _decir(phone, "Quiero 8 bidones de 12")
    v.check(PREGUNTA_PROD in r, "pregunta nuevo o recarga por los 8 de 12L")
    r = await _decir(phone, "Nuevos")
    v.check(_lineas(phone) == {B12N: 8}, f"'Nuevos' resuelve: 8x Bidón 12L Nuevo (líneas {_lineas(phone)})")
    v.check(not _pendientes(phone), "sin aclaración pendiente")
    v.check(PREGUNTA_PROD not in r, "no repite la pregunta")


RESPUESTAS_NUEVO = ("nuevo", "nuevos", "Nuevo", "el nuevo", "nuevo con envase")
RESPUESTAS_RECARGA = ("recarga", "recargas", "de recarga", "solo el agua")


async def caso_respuestas(v: Verificador) -> None:
    print("\n=== A1: respuestas sueltas a la aclaración, 12L y 20L ===")
    combinaciones = [(r, "Nuevo") for r in RESPUESTAS_NUEVO] + [(r, "Recarga") for r in RESPUESTAS_RECARGA]
    for i, (respuesta, variante) in enumerate(combinaciones):
        capacidad = 12 if i % 2 == 0 else 20
        cantidad = 2 + i % 3
        v.caso = f"A1 '{respuesta}' {capacidad}L"
        print(f"\n--- {v.caso} ---")
        phone = await _nuevo_cliente(f"Resp{i}")
        r = await _decir(phone, f"quiero {cantidad} bidones de {capacidad}")
        v.check(_pregunta_variante(r), "pregunta nuevo o recarga")
        await _decir(phone, respuesta)
        esperado = {f"Bidón {capacidad}L {variante}": cantidad}
        v.check(_lineas(phone) == esperado, f"queda {esperado} (líneas {_lineas(phone)})")
        v.check(not _pendientes(phone), "sin aclaración pendiente")


async def caso_varias_lineas(v: Verificador) -> None:
    print("\n=== A2: varias líneas pendientes (3 de 20 y 2 de 12) ===")
    for respuesta, esperado in (
        ("nuevos", {B20N: 3, B12N: 2}),
        ("el nuevo", {B20N: 3, B12N: 2}),
        ("solo el agua", {B20R: 3, B12R: 2}),
        ("recargas", {B20R: 3, B12R: 2}),
        ("los de 20 nuevos y los de 12 recarga", {B20N: 3, B12R: 2}),
    ):
        v.caso = f"A2 varias '{respuesta}'"
        print(f"\n--- {v.caso} ---")
        phone = await _nuevo_cliente("Varias")
        r = await _decir(phone, "quiero 3 bidones de 20 y 2 de 12")
        v.check(_pregunta_variante(r), "pregunta nuevo o recarga por las dos capacidades")
        await _decir(phone, respuesta)
        v.check(_lineas(phone) == esperado, f"queda {esperado} (líneas {_lineas(phone)})")
        v.check(not _pendientes(phone), "sin aclaración pendiente")


# --------------------------------------------------------------------------
# B. Catálogo sin la variante
# --------------------------------------------------------------------------


async def caso_catalogo_sin_variante(v: Verificador) -> None:
    order_flow._catalogo = _catalogo_sin_12_nuevo
    try:
        v.caso = "B1 sin 12L nuevo"
        print("\n=== B1: catálogo sin Bidón 12L Nuevo: 'Quiero 8 bidones de 12' → 'sí' ===")
        phone = await _nuevo_cliente("Catalina")
        r = await _decir(phone, "Quiero 8 bidones de 12")
        v.check(not _pregunta_variante(r), "no pregunta por una variante que no hay")
        v.check("solo lo tengo de recarga" in r, "dice que el de 12L solo está de recarga")
        v.check(_lineas(phone) == {}, "todavía no agrega nada sin confirmar")
        await _decir(phone, "sí")
        v.check(_lineas(phone) == {B12R: 8}, f"'sí' deja 8x Bidón 12L Recarga (líneas {_lineas(phone)})")

        v.caso = "B2 pide nuevo sin stock"
        print("\n=== B2: catálogo sin Bidón 12L Nuevo: 'Nuevos' → 'ok' ===")
        phone = await _nuevo_cliente("Catalina2")
        r = await _decir(phone, "Quiero 8 bidones de 12")
        r = await _decir(phone, "Nuevos")
        v.check(B12N not in _lineas(phone), "no agrega un producto que no está en el catálogo")
        v.check("solo lo tengo de recarga" in r, "vuelve a decir qué hay")
        v.check("No encontré" not in r, "sin el aviso genérico de producto no encontrado")
        await _decir(phone, "ok, déjalo así")
        v.check(_lineas(phone) == {B12R: 8}, f"'ok' deja 8x Bidón 12L Recarga (líneas {_lineas(phone)})")

        v.caso = "B3 nuevo explícito"
        print("\n=== B3: catálogo sin Bidón 12L Nuevo: 'quiero 2 bidones de 12 nuevos' ===")
        phone = await _nuevo_cliente("Catalina3")
        r = await _decir(phone, "quiero 2 bidones de 12 nuevos")
        v.check(B12N not in _lineas(phone), "no agrega el nuevo")
        v.check("solo lo tengo de recarga" in r, "ofrece la variante que hay")
        await _decir(phone, "recarga")
        v.check(_lineas(phone) == {B12R: 2}, f"'recarga' deja 2x Bidón 12L Recarga (líneas {_lineas(phone)})")

        v.caso = "B4 20L intacto"
        print("\n=== B4: catálogo sin Bidón 12L Nuevo: los de 20L siguen preguntando ===")
        phone = await _nuevo_cliente("Catalina4")
        r = await _decir(phone, "quiero 2 bidones de 20")
        v.check(_pregunta_variante(r), "20L: pregunta nuevo o recarga como siempre")
        await _decir(phone, "nuevos")
        v.check(_lineas(phone) == {B20N: 2}, f"queda 2x Bidón 20L Nuevo (líneas {_lineas(phone)})")
    finally:
        order_flow._catalogo = _catalogo_real


# --------------------------------------------------------------------------
# C. Anti-bucle
# --------------------------------------------------------------------------


async def _hasta_numeradas(v: Verificador, phone: str) -> str:
    r1 = await _decir(phone, "Quiero 8 bidones de 12")
    r2 = await _decir(phone, "mmm")
    v.check(r2 == r1 or PREGUNTA_PROD in r2, "1er fallo: repite la misma pregunta")
    r3 = await _decir(phone, "ehh")
    v.check("Responde 1 para nuevo o 2 para recarga" in r3, "2º fallo seguido: opciones numeradas")
    v.check(_lineas(phone) == {}, "sin líneas todavía")
    return r3


async def caso_anti_bucle(v: Verificador) -> None:
    for respuesta, esperado in (("2", {B12R: 8}), ("1", {B12N: 8})):
        v.caso = f"C1 numeradas '{respuesta}'"
        print(f"\n=== C1: anti-bucle, opciones numeradas y '{respuesta}' ===")
        phone = await _nuevo_cliente(f"Bucle{respuesta}")
        await _hasta_numeradas(v, phone)
        await _decir(phone, respuesta)
        v.check(_lineas(phone) == esperado, f"'{respuesta}' deja {esperado} (líneas {_lineas(phone)})")
        v.check(not _pendientes(phone), "sin aclaración pendiente")
        v.check(not (get_draft(phone) or {}).get("opciones_numeradas"), "las opciones numeradas se limpian")

    v.caso = "C2 derivación"
    print("\n=== C2: anti-bucle, otro fallo tras las numeradas → ejecutiva (aviso único) ===")
    phone = await _nuevo_cliente("Deriva")
    antes = avisos_ejecutiva.count(phone)
    await _hasta_numeradas(v, phone)
    r = await _decir(phone, "ni idea")
    v.check("ejecutiva" in r.lower(), "deriva a la ejecutiva")
    v.check(avisos_ejecutiva.count(phone) - antes == 1, "un aviso a la ejecutiva")
    v.check((get_draft(phone) or {}).get("aclaracion_pendiente") is not None, "el pedido en curso sigue guardado")
    # Otro ciclo de fallos no vuelve a avisar.
    await _decir(phone, "mmm")
    await _decir(phone, "ehh")
    r = await _decir(phone, "ni idea")
    v.check(avisos_ejecutiva.count(phone) - antes == 1, "aviso único: no se reenvía")
    # Y después todavía puede responder.
    await _decir(phone, "recarga")
    v.check(_lineas(phone) == {B12R: 8}, f"después responde 'recarga' y avanza (líneas {_lineas(phone)})")

    v.caso = "C3 numeradas sin variante"
    print("\n=== C3: anti-bucle con catálogo sin Bidón 12L Nuevo → '1' ===")
    order_flow._catalogo = _catalogo_sin_12_nuevo
    try:
        phone = await _nuevo_cliente("Bucle3")
        await _decir(phone, "Quiero 8 bidones de 12")
        await _decir(phone, "mmm")
        r = await _decir(phone, "ehh")
        v.check("Responde 1" in r and "nuevo" not in r.lower(), "numeradas solo con lo que hay")
        await _decir(phone, "1")
        v.check(_lineas(phone) == {B12R: 8}, f"'1' deja 8x Bidón 12L Recarga (líneas {_lineas(phone)})")
    finally:
        order_flow._catalogo = _catalogo_real

    v.caso = "C4 avanza no cuenta"
    print("\n=== C4: una respuesta que avanza reinicia el conteo ===")
    phone = await _nuevo_cliente("Avanza")
    await _decir(phone, "quiero 3 bidones de 20 y 2 de 12")
    await _decir(phone, "mmm")
    r = await _decir(phone, "los de 20 recarga")
    v.check(_lineas(phone) == {B20R: 3}, "resuelve los de 20L")
    v.check("Responde 1" not in r, "avanzó: no muestra opciones numeradas")
    r = await _decir(phone, "mmm")
    v.check("Responde 1" not in r, "un fallo después de avanzar: todavía no hay numeradas")


# --------------------------------------------------------------------------


async def main() -> None:
    _instalar_fakes()
    v = Verificador()
    try:
        await _limpiar_rango()
        for caso in (caso_produccion, caso_respuestas, caso_varias_lineas, caso_catalogo_sin_variante,
                     caso_anti_bucle):
            try:
                await caso(v)
            except Exception as exc:  # un caso roto no impide correr los demás
                order_flow._catalogo = _catalogo_real
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
