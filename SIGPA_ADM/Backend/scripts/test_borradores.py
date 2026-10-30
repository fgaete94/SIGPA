"""
Script temporal para validar la historia #150: los borradores de pedido del
bot se respaldan en Supabase (tabla borrador_pedido) con una caché en memoria
(app/services/draft_store.py), a través de procesar_mensaje y contra la BD.
No es parte del código final: solo para validar manualmente el comportamiento.

Requiere que la migración 20261029200000_crear_borrador_pedido.sql esté
aplicada: el script se detiene si la tabla no existe.

Casos con conversación real (c, g, b) pasan por el LLM de OpenAI igual que
test_cantidades_por_linea. Un redeploy se simula vaciando la caché en memoria
(reiniciar()).

Datos de prueba: todos los teléfonos usados están en el rango 569333004xx.
Al empezar, entre casos y al terminar, el script BORRA de ese rango los
clientes (con sus pedidos y detalles), los mensajes de mensaje_whatsapp, las
filas de conversacion_bot y los borradores de borrador_pedido. No toca ningún
otro dato. Las filas de auditoria no se borran (son el registro histórico).

No envía mensajes: procesar_mensaje devuelve el texto en vez de enviarlo.

Uso: python -m scripts.test_borradores
"""

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text

import app.services.draft_store as draft_store
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.conversacion_bot_service import marcar_activa
from app.services.draft_store import (
    cargar_draft,
    clear_draft,
    deserializar,
    get_draft,
    persistir_draft,
    save_draft,
    serializar,
)
from app.services.order_flow import procesar_mensaje

# Sin plantilla configurada el aviso a la ejecutiva no se envía a Meta.
settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""

RANGO_TELEFONOS_SQL = r"^(56)?9333004\d{2}$"

TEL_A = "56933300401"
TEL_B = "56933300402"
TEL_SIN_CLIENTE = "56933300403"
TEL_FALLOS = "56933300404"
TEL_TTL_VENCIDO = "56933300405"
TEL_TTL_VIGENTE = "56933300406"
TEL_REDUNDANTE = "56933300407"
TEL_ROUNDTRIP = "56933300408"
TEL_NUEVA_CONV = "56933300409"

CLIENTES = [
    ("Borrador A", TEL_A, "Av. Borradores 100, Viña del Mar"),
    ("Borrador B", TEL_B, "Calle Persistencia 200, Viña del Mar"),
    ("Borrador Fallos", TEL_FALLOS, "Pasaje Caída 300, Viña del Mar"),
]

MENSAJE_BIDONES = "2 de 20 y 1 de 12"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _tabla_existe() -> bool:
    return (await _ejecutar("select to_regclass('public.borrador_pedido')")).scalar() is not None


def reiniciar() -> None:
    """Simula un redeploy: se pierde todo lo que vive en la memoria."""
    draft_store._drafts.clear()
    draft_store._persistidos.clear()
    draft_store._pendientes_borrar.clear()


async def _limpiar_rango() -> None:
    ids_sql = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    await _ejecutar(
        f"delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id in ({ids_sql}))",
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from mensaje_whatsapp where cliente_id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from mensaje_whatsapp where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from conversacion_bot where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from borrador_pedido where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    reiniciar()


async def _crear_clientes() -> None:
    for nombre, telefono, direccion in CLIENTES:
        await _ejecutar(
            "insert into cliente (nombre, telefono, direccion) values (:n, :t, :d)",
            n=nombre,
            t=telefono,
            d=direccion,
        )


async def _fila(telefono: str):
    result = await _ejecutar(
        "select datos, actualizado_en from borrador_pedido where telefono = :t", t=telefono
    )
    return result.first()


async def _conteo_filas() -> int:
    result = await _ejecutar(
        "select count(*) from borrador_pedido where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL
    )
    return result.scalar_one()


class Verificador:
    def __init__(self) -> None:
        self.errores: list[str] = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'OK' if condicion else 'FALLO'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


async def _conversar(v: Verificador, phone: str, turnos: list[str]) -> list[str]:
    """Envía los turnos por procesar_mensaje. En cada uno comprueba que el
    borrador en memoria sobrevive a la ida y vuelta por JSON sin cambios (así
    se audita con borradores reales, no solo con los de ejemplo)."""
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        draft = get_draft(phone) or {}
        print(f"  Bot [{draft.get('paso')}/{draft.get('estado')}]: {respuesta}")
        if draft:
            v.check(
                deserializar(json.loads(json.dumps(serializar(draft)))) == draft,
                f"ida y vuelta JSON del borrador tras '{turno}' (paso {draft.get('paso')})",
            )
        respuestas.append(respuesta)
    return respuestas


def _productos(phone: str) -> dict[str, int]:
    draft = get_draft(phone) or {}
    return {p["nombre_producto"]: p["cantidad"] for p in draft.get("productos") or []}


def _es_resumen(texto: str) -> bool:
    return "Resumen de tu pedido" in texto and "¿Confirmas el pedido?" in texto


async def _detalles_pedido(telefono: str) -> dict[str, int]:
    """Detalle del último pedido del cliente."""
    result = await _ejecutar(
        "select p.nombre, d.cantidad_solicitada from detalle_pedido d "
        "join producto p on p.id = d.producto_id join pedido pe on pe.id = d.pedido_id "
        "join cliente c on c.id = pe.cliente_id where c.telefono = :t "
        "and pe.id = (select max(p2.id) from pedido p2 where p2.cliente_id = c.id)",
        t=telefono,
    )
    return {nombre: cantidad for nombre, cantidad in result.all()}


@contextmanager
def _bd_caida(*, contador: list | None = None):
    """Reemplaza SessionLocal de draft_store por uno que falla (o que solo
    cuenta las sesiones abiertas si se pasa un contador)."""
    original = draft_store.SessionLocal

    def falso(*args, **kwargs):
        if contador is not None:
            contador.append(1)
            return original(*args, **kwargs)
        raise ConnectionError("BD caída (simulada)")

    draft_store.SessionLocal = falso
    try:
        yield
    finally:
        draft_store.SessionLocal = original


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


def _borradores_ejemplo() -> dict[str, dict]:
    productos = [
        {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 2},
        {"nombre_producto": "Bidón 12L Recarga", "cantidad": 1},
    ]
    base = {
        "intencion": "pedido",
        "notas": None,
        "nombre_cliente": "Ana",
        "usa_direccion_habitual": True,
        "direccion_texto": None,
        "ubicacion": {"latitud": -33.0245, "longitud": -71.5518},
        "ubicacion_rechazada": False,
        "algo_mas_respondido": False,
        "pendientes_modelo": [],
        "no_encontrados": [],
    }
    unidades = {"Bidón": 3, "Bidón 20L": 2, "Bidón 12L": 1}
    return {
        "producto": {"paso": "producto", "estado": "armando", "productos": []},
        "aclaracion con dict (un bidón)": {
            **base,
            "paso": "aclaracion",
            "estado": "armando",
            "productos": [],
            "aclaracion_pendiente": {"capacidad_litros": 20, "cantidad": 2},
            "unidades_pedidas": {"Bidón": 2, "Bidón 20L": 2},
        },
        "aclaracion con lista (dos capacidades)": {
            **base,
            "paso": "aclaracion",
            "estado": "armando",
            "productos": [],
            "aclaracion_pendiente": [
                {"capacidad_litros": 20, "cantidad": 2},
                {"capacidad_litros": 12, "cantidad": 1},
            ],
            "unidades_pedidas": unidades,
        },
        "resumen": {
            **base,
            "paso": "confirmacion",
            "estado": "esperando_confirmacion",
            "productos": productos,
            "aclaracion_pendiente": None,
            "unidades_pedidas": unidades,
            "resumen": {
                "lineas": [
                    {"nombre": "Bidón 20L Nuevo", "cantidad": 2, "precio_unitario": 5990.0, "subtotal": 11980.0},
                    {"nombre": "Bidón 12L Recarga", "cantidad": 1, "precio_unitario": 2500.0, "subtotal": 2500.0},
                ],
                "total": 14480.0,
                "texto_resumen": "Resumen de tu pedido:\n- 2x Bidón 20L Nuevo — $11.980\n¿Confirmas el pedido? Responde SI para confirmar.",
            },
        },
        "esperando_modificacion": {
            **base,
            "paso": "confirmacion",
            "estado": "esperando_modificacion",
            "productos": productos,
            "aclaracion_pendiente": None,
            "unidades_pedidas": unidades,
        },
        "correccion_pendiente": {
            **base,
            "paso": "confirmacion",
            "estado": "esperando_confirmacion",
            "productos": productos,
            "aclaracion_pendiente": None,
            "unidades_pedidas": unidades,
            "correccion_pendiente": {
                "familia": "Bidón",
                "atributos": ["20l", "nuevo"],
                "permite_agregar": True,
            },
        },
        "oferta_repetir": {
            "paso": "producto",
            "estado": "armando",
            "productos": [],
            "oferta_repetir": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 3}],
        },
        "pendientes_modelo": {
            **base,
            "paso": "producto",
            "estado": "armando",
            "productos": [],
            "pendientes_modelo": [{"familia": "Dispensador", "cantidad": 1, "atributos": ["frio", "calor"]}],
            "no_encontrados": ["cafetera"],
        },
    }


async def caso_a(v: Verificador) -> None:
    for nombre, draft in _borradores_ejemplo().items():
        # Pasa además por JSON de verdad, como al guardarse en jsonb.
        vuelta = deserializar(json.loads(json.dumps(serializar(draft))))
        v.check(vuelta == draft, f"{nombre}: deserializar(serializar(d)) == d")
    d = _borradores_ejemplo()
    for clave in ("aclaracion con dict (un bidón)", "aclaracion con lista (dos capacidades)"):
        original = d[clave]["aclaracion_pendiente"]
        vuelta = deserializar(json.loads(json.dumps(serializar(d[clave]))))["aclaracion_pendiente"]
        v.check(type(vuelta) is type(original) and vuelta == original, f"{clave}: vuelve como {type(original).__name__}")

    # Tipos que hoy no aparecen en los borradores pero el codec cubre.
    exoticos = {
        "set": {"20l", "nuevo"},
        "frozenset": frozenset({"a", "b"}),
        "tuple": (1, "x", (2, 3)),
        "decimal": Decimal("5990.50"),
        "datetime": datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc),
        "date": date(2026, 10, 29),
        "claves int": {1: "uno", 2: ["dos"]},
        "clave '__t'": {"__t": "set", "v": [1]},
        "float entero": 12345.0,
        "bool no es int": [True, 1, False, 0],
        "acentos": {"nota": "Bidón — ñandú ¿sí?"},
    }
    for nombre, valor in exoticos.items():
        vuelta = deserializar(json.loads(json.dumps(serializar(valor))))
        v.check(vuelta == valor and type(vuelta) is type(valor), f"{nombre}: ida y vuelta con el mismo tipo")
    v.check(
        isinstance(deserializar(json.loads(json.dumps(serializar([12345.0]))))[0], float),
        "float 12345.0 no se degrada a int",
    )
    for raro in (object(), float("nan"), b"bytes"):
        try:
            serializar({"x": raro})
            v.check(False, f"tipo no soportado ({type(raro).__name__}) falla en vez de guardarse mal")
        except TypeError:
            v.check(True, f"tipo no soportado ({type(raro).__name__}) falla en vez de guardarse mal")
    # El mismo borrador da siempre el mismo texto (base de la omisión de escrituras).
    v.check(
        draft_store._a_texto({"b": {"y", "x"}, "a": 1}) == draft_store._a_texto({"a": 1, "b": {"x", "y"}}),
        "el texto serializado es estable (sets y claves ordenados)",
    )


async def caso_b(v: Verificador) -> None:
    await _conversar(v, TEL_A, [MENSAJE_BIDONES])
    fila = await _fila(TEL_A)
    v.check(fila is not None, "tras un mensaje que deja borrador existe la fila en borrador_pedido")
    if fila is not None:
        v.check(deserializar(fila.datos) == get_draft(TEL_A), "el contenido de la fila es el borrador en memoria")
        v.check(isinstance(fila.datos["aclaracion_pendiente"], list), "aclaracion_pendiente (lista) guardada como lista")

    await _conversar(v, TEL_A, ["las de 20 recarga y la de 12 nuevo", "si", "no"])
    fila = await _fila(TEL_A)
    v.check(fila is not None and deserializar(fila.datos) == get_draft(TEL_A), "en el resumen la fila sigue al borrador")
    respuestas = await _conversar(v, TEL_A, ["SI"])
    v.check(get_draft(TEL_A) is None, "al confirmar el pedido no queda borrador en memoria")
    v.check(await _fila(TEL_A) is None, "al confirmar el pedido la fila ya no existe")
    esperado = {"Bidón 20L Recarga": 2, "Bidón 12L Nuevo": 1}
    detalles = await _detalles_pedido(TEL_A)
    v.check(detalles == esperado, f"el pedido confirmado es {esperado}: {detalles}")
    print(f"  Bot: {respuestas[0][:120]}")

    # Cancelar también borra la fila.
    await _conversar(v, TEL_B, ["quiero 3 bidones de 20 recarga"])
    v.check(await _fila(TEL_B) is not None, "B: queda borrador con fila")
    await _conversar(v, TEL_B, ["cancelar"])
    v.check(get_draft(TEL_B) is None and await _fila(TEL_B) is None, "al cancelar no queda borrador ni fila")


async def caso_c(v: Verificador) -> None:
    await _conversar(v, TEL_A, [MENSAJE_BIDONES])
    v.check(isinstance(get_draft(TEL_A).get("aclaracion_pendiente"), list), "queda aclaración pendiente por capacidad")
    reiniciar()
    v.check(get_draft(TEL_A) is None, "tras el 'redeploy' la memoria está vacía")
    await _conversar(v, TEL_A, ["20 nuevo y 12 recarga"])
    esperado = {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1}
    v.check(_productos(TEL_A) == esperado, f"continúa tras el reinicio con {esperado}: {_productos(TEL_A)}")
    v.check(not get_draft(TEL_A).get("aclaracion_pendiente"), "ya no queda nada por aclarar")

    respuestas = await _conversar(v, TEL_A, ["si", "no"])
    v.check(_es_resumen(respuestas[-1]), "llega al resumen")
    # Segundo reinicio: después del resumen, antes del "sí".
    reiniciar()
    v.check(get_draft(TEL_A) is None, "segundo 'redeploy' después del resumen")
    await _conversar(v, TEL_A, ["SI"])
    detalles = await _detalles_pedido(TEL_A)
    v.check(detalles == esperado, f"el pedido final es 2x20L Nuevo y 1x12L Recarga: {detalles}")
    v.check(await _fila(TEL_A) is None, "confirmado: sin fila")


async def caso_d(v: Verificador) -> None:
    ttl = settings.BORRADOR_TTL_HORAS
    v.check(ttl == 24, "BORRADOR_TTL_HORAS por defecto = 24")
    draft = {"paso": "producto", "estado": "armando", "productos": []}
    datos = json.dumps(serializar(draft))
    await _ejecutar(
        "insert into borrador_pedido (telefono, datos, actualizado_en) values "
        "(:t, cast(:d as jsonb), now() - make_interval(hours => :h))",
        t=TEL_TTL_VENCIDO,
        d=datos,
        h=ttl + 1,
    )
    await _ejecutar(
        "insert into borrador_pedido (telefono, datos, actualizado_en) values "
        "(:t, cast(:d as jsonb), now() - make_interval(hours => :h))",
        t=TEL_TTL_VIGENTE,
        d=datos,
        h=ttl - 1,
    )
    await cargar_draft(TEL_TTL_VENCIDO)
    v.check(get_draft(TEL_TTL_VENCIDO) is None, f"borrador de hace {ttl + 1} h no se carga")
    v.check(await _fila(TEL_TTL_VENCIDO) is None, "y se elimina de la tabla")
    await cargar_draft(TEL_TTL_VIGENTE)
    v.check(get_draft(TEL_TTL_VIGENTE) == draft, f"borrador de hace {ttl - 1} h sí se carga")
    v.check(await _fila(TEL_TTL_VIGENTE) is not None, "y la fila se conserva")

    # Con el TTL configurable.
    antes = settings.BORRADOR_TTL_HORAS
    settings.BORRADOR_TTL_HORAS = 1
    try:
        reiniciar()
        await cargar_draft(TEL_TTL_VIGENTE)
        v.check(get_draft(TEL_TTL_VIGENTE) is None, "con BORRADOR_TTL_HORAS=1 el de hace 23 h ya no vale")
    finally:
        settings.BORRADOR_TTL_HORAS = antes
    v.check(await _fila(TEL_TTL_VIGENTE) is None, "y también se elimina")


async def caso_e(v: Verificador) -> None:
    # e1: falla la carga.
    with _bd_caida():
        respuesta = await procesar_mensaje(TEL_FALLOS, "text", "quiero 2 bidones de 20 recarga", None)
    print(f"  Bot (BD caída al cargar y persistir): {respuesta}")
    from app.api.routes.whatsapp import MENSAJE_ERROR_GENERICO

    v.check(respuesta and respuesta != MENSAJE_ERROR_GENERICO, "con la BD caída el cliente no recibe el mensaje genérico de error")
    v.check(_productos(TEL_FALLOS) == {"Bidón 20L Recarga": 2}, "la conversación sigue con la caché en memoria")
    v.check(await _fila(TEL_FALLOS) is None, "no se pudo persistir: no hay fila")

    # e2: falla persistir, con el borrador ya cargado.
    with _bd_caida():
        respuesta = await procesar_mensaje(TEL_FALLOS, "text", "y 1 de 12 nuevo", None)
    v.check(respuesta != MENSAJE_ERROR_GENERICO, "falla al persistir: sin mensaje genérico")
    productos = _productos(TEL_FALLOS)
    v.check(productos.get("Bidón 12L Nuevo") == 1, f"el borrador sigue creciendo en memoria: {productos}")
    v.check(await _fila(TEL_FALLOS) is None, "la fila sigue sin existir")

    # e3: el siguiente mensaje con la BD de vuelta reintenta persistir.
    await procesar_mensaje(TEL_FALLOS, "text", "no", None)
    fila = await _fila(TEL_FALLOS)
    v.check(fila is not None and deserializar(fila.datos) == get_draft(TEL_FALLOS), "con la BD de vuelta se persiste el estado actual")

    # e4: un cancelar cuyo delete falla no resucita el borrador desde la BD.
    with _bd_caida():
        await procesar_mensaje(TEL_FALLOS, "text", "cancelar", None)
    v.check(get_draft(TEL_FALLOS) is None, "cancelar con la BD caída: sin borrador en memoria")
    v.check(await _fila(TEL_FALLOS) is not None, "la fila quedó (el delete falló)")
    await procesar_mensaje(TEL_FALLOS, "text", "hola", None)
    draft = get_draft(TEL_FALLOS) or {}
    v.check(not draft.get("productos"), "el borrador cancelado no revive desde la BD en el siguiente mensaje")
    fila = await _fila(TEL_FALLOS)
    v.check(fila is None or not deserializar(fila.datos).get("productos"), "y la fila ya no tiene los productos viejos")


async def caso_f(v: Verificador) -> None:
    draft = {"paso": "producto", "estado": "armando", "productos": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 1}]}
    contador: list = []
    with _bd_caida(contador=contador):
        save_draft(TEL_REDUNDANTE, draft)
        await persistir_draft(TEL_REDUNDANTE)
        primera = len(contador)
        await persistir_draft(TEL_REDUNDANTE)
        await persistir_draft(TEL_REDUNDANTE)
        sin_cambios = len(contador) - primera
        save_draft(TEL_REDUNDANTE, {**draft, "estado": "esperando_confirmacion"})
        await persistir_draft(TEL_REDUNDANTE)
        con_cambio = len(contador) - primera - sin_cambios
        clear_draft(TEL_REDUNDANTE)
        await persistir_draft(TEL_REDUNDANTE)
        borrado = len(contador) - primera - sin_cambios - con_cambio
        await persistir_draft(TEL_REDUNDANTE)
        borrado_repetido = len(contador) - primera - sin_cambios - con_cambio - borrado
    v.check(primera == 1, "la primera pasada escribe una vez")
    v.check(sin_cambios == 0, "dos pasadas sin cambios no vuelven a escribir")
    v.check(con_cambio == 1, "un cambio real escribe una vez")
    v.check(borrado == 1, "al descartar el borrador se borra la fila una vez")
    v.check(borrado_repetido == 0, "y no se vuelve a borrar")
    v.check(await _fila(TEL_REDUNDANTE) is None, "no queda fila")

    # Por procesar_mensaje: un mensaje que no cambia nada tampoco escribe.
    await procesar_mensaje(TEL_B, "text", "quiero 3 bidones de 20 recarga", None)
    contador.clear()
    with _bd_caida(contador=contador):
        await persistir_draft(TEL_B)
    v.check(len(contador) == 0, "tras procesar_mensaje, persistir de nuevo no escribe")
    await procesar_mensaje(TEL_B, "text", "cancelar", None)


async def caso_g(v: Verificador) -> None:
    await _conversar(v, TEL_A, ["quiero 3 bidones de 20 recarga"])
    await _conversar(v, TEL_B, ["quiero 1 bidón de 12 nuevo"])
    esperado_a, esperado_b = {"Bidón 20L Recarga": 3}, {"Bidón 12L Nuevo": 1}
    v.check(_productos(TEL_A) == esperado_a and _productos(TEL_B) == esperado_b, "cada teléfono tiene su borrador")
    fila_a, fila_b = await _fila(TEL_A), await _fila(TEL_B)
    v.check(fila_a is not None and fila_b is not None and fila_a.datos != fila_b.datos, "dos filas distintas")
    reiniciar()
    await cargar_draft(TEL_A)
    v.check(_productos(TEL_A) == esperado_a and get_draft(TEL_B) is None, "tras reiniciar, cargar A no trae el de B")
    await cargar_draft(TEL_B)
    v.check(_productos(TEL_B) == esperado_b, "cargar B trae el suyo")
    await _conversar(v, TEL_B, ["cancelar"])
    v.check(await _fila(TEL_B) is None and await _fila(TEL_A) is not None, "cancelar B no toca la fila de A")
    await _conversar(v, TEL_A, ["cancelar"])


@contextmanager
def _capturar_errores():
    registros: list[str] = []

    class Capturador(logging.Handler):
        def emit(self, record):
            registros.append(record.getMessage())

    manejador = Capturador(level=logging.ERROR)
    logger = logging.getLogger("app.services.draft_store")
    logger.addHandler(manejador)
    try:
        yield registros
    finally:
        logger.removeHandler(manejador)


async def _conversacion_activa(telefono: str):
    result = await _ejecutar("select activa from conversacion_bot where telefono = :t", t=telefono)
    fila = result.first()
    return None if fila is None else fila.activa


async def caso_h(v: Verificador) -> None:
    viejo = {
        "paso": "producto",
        "estado": "armando",
        "intencion": "pedido",
        "productos": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 3}],
    }
    await _ejecutar(
        "insert into borrador_pedido (telefono, datos, actualizado_en) values "
        "(:t, cast(:d as jsonb), now() - interval '20 hours')",
        t=TEL_A,
        d=json.dumps(serializar(viejo)),
    )
    await cargar_draft(TEL_A)
    v.check(_productos(TEL_A) == {"Bidón 20L Recarga": 3}, "el borrador de ayer está en memoria (vigente por TTL)")
    await marcar_activa(TEL_A)
    v.check(get_draft(TEL_A) is None, "tras marcar_activa no hay borrador en memoria")
    v.check(await _fila(TEL_A) is None, "ni en la tabla")
    v.check(await _conversacion_activa(TEL_A) is True, "y la conversación quedó activa")
    # También con la memoria vacía (el proceso se reinició después del borrador).
    await _ejecutar(
        "insert into borrador_pedido (telefono, datos, actualizado_en) values "
        "(:t, cast(:d as jsonb), now() - interval '20 hours')",
        t=TEL_A,
        d=json.dumps(serializar(viejo)),
    )
    reiniciar()
    await marcar_activa(TEL_A)
    v.check(await _fila(TEL_A) is None, "con la memoria vacía también se borra la fila")
    await _conversar(v, TEL_A, ["quiero 1 bidón de 12 nuevo"])
    v.check(_productos(TEL_A) == {"Bidón 12L Nuevo": 1}, f"la conversación nueva parte limpia: {_productos(TEL_A)}")
    await _conversar(v, TEL_A, ["cancelar"])


async def caso_i(v: Verificador) -> None:
    try:
        await marcar_activa(TEL_NUEVA_CONV)
        v.check(True, "marcar_activa sin borrador no falla")
    except Exception as exc:
        v.check(False, f"marcar_activa sin borrador no falla: {exc!r}")
    v.check(await _conversacion_activa(TEL_NUEVA_CONV) is True, "la conversación queda activa")
    v.check(await _fila(TEL_NUEVA_CONV) is None and get_draft(TEL_NUEVA_CONV) is None, "no se crea ningún borrador")
    await marcar_activa(TEL_NUEVA_CONV)
    v.check(await _conversacion_activa(TEL_NUEVA_CONV) is True, "repetirlo tampoco falla")


async def caso_j(v: Verificador) -> None:
    viejo = {"paso": "producto", "estado": "armando", "productos": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 3}]}
    await _ejecutar(
        "insert into borrador_pedido (telefono, datos) values (:t, cast(:d as jsonb))",
        t=TEL_FALLOS,
        d=json.dumps(serializar(viejo)),
    )
    await cargar_draft(TEL_FALLOS)
    v.check(get_draft(TEL_FALLOS) == viejo, "hay un borrador previo en memoria y en la tabla")
    with _capturar_errores() as errores, _bd_caida():
        try:
            await marcar_activa(TEL_FALLOS)
            v.check(True, "con el descarte fallando, marcar_activa no lanza")
        except Exception as exc:
            v.check(False, f"con el descarte fallando, marcar_activa no lanza: {exc!r}")
    v.check(await _conversacion_activa(TEL_FALLOS) is True, "la conversación queda activa igual")
    v.check(any(TEL_FALLOS in e for e in errores), "el error se loguea")
    v.check(get_draft(TEL_FALLOS) is None, "el borrador se descartó al menos en memoria")
    v.check(await _fila(TEL_FALLOS) is not None, "la fila quedó (el delete falló)")
    # Con la BD de vuelta, la fila vieja no revive y el primer mensaje parte limpio.
    await _conversar(v, TEL_FALLOS, ["quiero 1 bidón de 12 nuevo"])
    v.check(_productos(TEL_FALLOS) == {"Bidón 12L Nuevo": 1}, f"el borrador viejo no revive: {_productos(TEL_FALLOS)}")
    await _conversar(v, TEL_FALLOS, ["cancelar"])


CASOS = [
    ("a", "Ida y vuelta de serialización con borradores de cada paso", caso_a),
    ("b", "Persistencia: fila tras el mensaje; sin fila al confirmar o cancelar", caso_b),
    ("c", "Reinicio: aclaración por capacidad y resumen sobreviven", caso_c),
    ("d", "TTL: el vencido no se carga y se elimina", caso_d),
    ("e", "Fallos de la BD: la conversación sigue, sin mensaje genérico", caso_e),
    ("f", "Sin escrituras redundantes", caso_f),
    ("g", "Dos teléfonos no se mezclan", caso_g),
    ("h", "marcar_activa descarta el borrador de ayer; la conversación parte limpia", caso_h),
    ("i", "marcar_activa sin borrador no falla", caso_i),
    ("j", "Si el descarte falla, marcar_activa igual activa la conversación", caso_j),
]


async def main() -> None:
    if not await _tabla_existe():
        print("La tabla borrador_pedido no existe: aplica supabase/migrations/20261029200000_crear_borrador_pedido.sql")
        return
    await _limpiar_rango()
    resultados = []
    try:
        await _crear_clientes()
        for letra, descripcion, caso in CASOS:
            print("#" * 70)
            print(f"Caso {letra}: {descripcion}")
            print("#" * 70)
            v = Verificador()
            try:
                await caso(v)
            except Exception as exc:
                print(f"    [ERROR] {exc!r}")
                v.errores.append(repr(exc))
            resultados.append((letra, descripcion, not v.errores))
            await _ejecutar("delete from borrador_pedido where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
            reiniciar()
            print()
    finally:
        await _limpiar_rango()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print(f"  {sum(ok for *_, ok in resultados)} de {len(resultados)} OK")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
