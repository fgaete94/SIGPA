"""
Script temporal para validar el bug del 2026-10-08: un mensaje con varias
líneas de bidón de distinta capacidad y sin variante ("3 de 20 y 1 de 12")
perdía líneas. Valida el parser (_lineas_bidon / _menciones_productos), la
aclaración de varias capacidades juntas (_resolver_aclaraciones_bidon) y la
validación de unidades por línea (_productos_no_registrados), a través de
procesar_mensaje y contra la BD.

Casos f-j (bug del 2026-10-08 en la primera prueba real): "20 nuevo y 12
recarga" como respuesta a "¿Los 2 bidones de 20L y el bidón de 12L...?" se
leía como 20 + 12 = 32 bidones. Validan la lectura de esos números como
capacidades (_reinterpretar_capacidades), el tope contra unidades sin
respaldo en _aplicar_resultado_llm y el caso ambiguo. Donde se simula el LLM,
el resultado se pasa directo a _aplicar_resultado_llm.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 569333001xx.
Al empezar y al terminar, el script BORRA de ese rango los clientes (con sus
pedidos y detalles), los mensajes de mensaje_whatsapp y las filas de
conversacion_bot. No toca ningún otro cliente ni pedido. Las filas de
auditoria no se borran (son el registro histórico).

No envía mensajes: procesar_mensaje devuelve el texto en vez de enviarlo, y
en el caso que pasa por el webhook (whatsapp._procesar_mensaje_entrante)
send_whatsapp_message se reemplaza por un fake.

Uso: python -m scripts.test_cantidades_por_linea
"""

import asyncio
import uuid

from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.order_flow as order_flow
from app.core.database import SessionLocal
from app.models import Cliente
from app.services.conversacion_bot_service import marcar_activa
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import (
    MENSAJE_ACLARACION_NO_ENTENDIDA,
    _aplicar_resultado_llm,
    _datos_cliente,
    _menciones_productos,
    _normalizar_texto,
    _productos_no_registrados,
    _resolver_aclaracion_bidon,
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

# Cliente nuevo para los casos con el LLM simulado.
TELEFONO_SIMULADO = "56933300102"

PENDIENTES_REAL = [{"capacidad_litros": 20, "cantidad": 2}, {"capacidad_litros": 12, "cantidad": 1}]


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
    clear_draft(TELEFONO_SIMULADO)


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


def _lineas(resultado: dict | None) -> dict[str, int] | None:
    if resultado is None:
        return None
    return {linea["nombre_producto"]: linea["cantidad"] for linea in resultado["lineas"]}


async def _llm_simulado(mensaje: str, previo: dict, resultado_llm: dict | None = None) -> str:
    """_aplicar_resultado_llm con un resultado del LLM fijo (cliente nuevo,
    sin pasar por OpenAI)."""
    resultado = resultado_llm or {"intencion": "pedido", "productos": [], "respuesta_sugerida": "¿Algo más?"}
    texto = await _aplicar_resultado_llm(TELEFONO_SIMULADO, resultado, dict(previo), None, mensaje)
    print(f"  Cliente: {mensaje}\n  Bot [LLM simulado]: {texto}")
    return texto


def _previo_pendiente(aclaracion) -> dict:
    pendientes = aclaracion if isinstance(aclaracion, list) else [aclaracion]
    unidades = {"Bidón": sum(p["cantidad"] for p in pendientes)}
    for p in pendientes:
        unidades[f"Bidón {p['capacidad_litros']}L"] = p["cantidad"]
    return {
        "intencion": "pedido",
        "productos": [],
        "aclaracion_pendiente": aclaracion,
        "paso": "producto",
        "estado": "armando",
        "unidades_pedidas": unidades,
    }


async def caso_f(ctx: dict, v: Verificador) -> None:
    esperados = {
        "20 nuevo y 12 recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "20 nuevos y 12 recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "20 nuevo, 12 recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "nuevo 20 y recarga 12": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "12 recarga y 20 nuevo": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "20 recarga y 12 nuevo": {"Bidón 20L Recarga": 2, "Bidón 12L Nuevo": 1},
        "20 nuevo e 12 recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "20 nuevo + 12 recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        # Los que ya funcionaban.
        "20L nuevo y 12L recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "de 20 nuevo y de 12 recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "los de 20 nuevo y el de 12 recarga": {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        "todos recarga": {"Bidón 20L Recarga": 2, "Bidón 12L Recarga": 1},
    }
    for mensaje, esperado in esperados.items():
        resultado = _resolver_aclaracion_bidon(PENDIENTES_REAL, mensaje)
        v.check(
            _lineas(resultado) == esperado and not resultado["aclaracion"],
            f"{mensaje!r} -> {_lineas(resultado)}",
        )
    # "2 recarga y 1 nuevo": cantidades, solo si calzan con un único pendiente.
    tres_y_uno = [{"capacidad_litros": 20, "cantidad": 3}, {"capacidad_litros": 12, "cantidad": 1}]
    resultado = _resolver_aclaracion_bidon(tres_y_uno, "2 recarga y 1 nuevo")
    v.check(
        _lineas(resultado) == {"Bidón 20L Recarga": 2, "Bidón 20L Nuevo": 1}
        and resultado["aclaracion"] == {"capacidad_litros": 12, "cantidad": 1},
        "'2 recarga y 1 nuevo' con 3x 20L y 1x 12L: reparte los 3 de 20L, el de 12L sigue pendiente",
    )
    v.check(
        _resolver_aclaracion_bidon(PENDIENTES_REAL, "2 recarga y 1 nuevo") is None,
        "'2 recarga y 1 nuevo' con 2x 20L y 1x 12L: no calza con ninguno, no adivina",
    )
    resultado = _resolver_aclaracion_bidon({"capacidad_litros": 20, "cantidad": 4}, "2 recargas")
    v.check(
        _lineas(resultado) == {"Bidón 20L Recarga": 2}
        and resultado["aclaracion"] == {"capacidad_litros": 20, "cantidad": 2},
        "'2 recargas' de 4 pendientes: quedan 2",
    )
    resultado = _resolver_aclaracion_bidon({"capacidad_litros": 20, "cantidad": 2}, "20 recarga")
    v.check(
        _lineas(resultado) == {"Bidón 20L Recarga": 2} and resultado["aclaracion"] is None,
        "un solo pendiente de 20L y '20 recarga': es la capacidad, no 20 unidades",
    )
    resultado = _resolver_aclaracion_bidon(PENDIENTES_REAL, "20 bidones nuevos y 12 recarga")
    v.check(
        _lineas(resultado) == {"Bidón 12L Recarga": 1}
        and resultado["aclaracion"] == {"capacidad_litros": 20, "cantidad": 2},
        "'20 bidones nuevos' es cantidad (dice 'bidones'): no calza y los de 20L siguen pendientes",
    )


async def caso_g(ctx: dict, v: Verificador) -> None:
    """Conversación real del 2026-10-08 por el webhook, con conversación
    activa: sin pregunta por "32 bidones" y con el pedido en la BD."""
    phone = CLIENTE["telefono"]
    await _ejecutar(
        "delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id = :id)",
        id=ctx["cliente_id"],
    )
    await _ejecutar("delete from pedido where cliente_id = :id", id=ctx["cliente_id"])
    await marcar_activa(phone)
    enviados = []

    async def fake_send(to: str, message: str):
        enviados.append((to, message))
        return {"messages": [{"id": f"wamid.fake-{uuid.uuid4().hex}"}]}

    original = whatsapp_route.send_whatsapp_message
    whatsapp_route.send_whatsapp_message = fake_send
    respuestas = []
    try:
        for turno in ("hola", "2 de 20 y 1 de 12", "20 nuevo y 12 recarga", "si", "no", "sí"):
            print(f"  Cliente: {turno}")
            await whatsapp_route._procesar_mensaje_entrante(
                {"from": phone, "id": f"wamid.test-lineas-{uuid.uuid4().hex}", "type": "text", "text": {"body": turno}}
            )
            respuesta = enviados[-1][1] if enviados else ""
            print(f"  Bot: {respuesta}")
            respuestas.append(respuesta)
    finally:
        whatsapp_route.send_whatsapp_message = original
    v.check(
        "2 bidones de 20L" in respuestas[1] and "bidón de 12L" in respuestas[1],
        "pregunta por capacidad (2x 20L y 1x 12L)",
    )
    v.check("32" not in " ".join(respuestas), "nunca pregunta por 32 bidones")
    v.check(CLIENTE["direccion"] in respuestas[2], "'20 nuevo y 12 recarga' resuelve y pasa a la dirección")
    v.check(
        _es_resumen(respuestas[4]) and "2x Bidón 20L Nuevo" in respuestas[4] and "1x Bidón 12L Recarga" in respuestas[4],
        "resumen con 2x 20L Nuevo y 1x 12L Recarga",
    )
    v.check("confirmado" in respuestas[5], "'sí' confirma el pedido")
    result = await _ejecutar(
        "select p.nombre, d.cantidad_solicitada from detalle_pedido d join producto p on p.id = d.producto_id "
        "join pedido pe on pe.id = d.pedido_id where pe.cliente_id = :id",
        id=ctx["cliente_id"],
    )
    detalles = {nombre: cantidad for nombre, cantidad in result.all()}
    v.check(
        detalles == {"Bidón 20L Nuevo": 2, "Bidón 12L Recarga": 1},
        f"pedido en la BD con 2x 20L Nuevo y 1x 12L Recarga: {detalles}",
    )
    await _ejecutar(
        "delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id = :id)",
        id=ctx["cliente_id"],
    )
    await _ejecutar("delete from pedido where cliente_id = :id", id=ctx["cliente_id"])


async def caso_h(ctx: dict, v: Verificador) -> None:
    previo = _previo_pendiente(PENDIENTES_REAL)
    llm_32 = {
        "intencion": "pedido",
        "productos": [],
        "aclaracion_pendiente": {"capacidad_litros": None, "cantidad": 32},
        "respuesta_sugerida": "¿Los 32 bidones los quieres de 12L o de 20L?",
    }
    texto = await _llm_simulado("20 nuevo y 12 recarga", previo, llm_32)
    draft = get_draft(TELEFONO_SIMULADO) or {}
    v.check(
        draft.get("productos")
        == [
            {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 2},
            {"nombre_producto": "Bidón 12L Recarga", "cantidad": 1},
        ]
        and not draft.get("aclaracion_pendiente"),
        "con el LLM devolviendo 32 pendientes, el código resuelve 2x 20L Nuevo y 1x 12L Recarga",
    )
    v.check("32" not in texto, "no pregunta por 32 bidones")

    # Tope: con un resolver que no entiende la respuesta (como antes del
    # arreglo), las menciones sumaban 32 bidones sin capacidad.
    original = order_flow._resolver_aclaracion_bidon
    order_flow._resolver_aclaracion_bidon = lambda aclaracion, mensaje: None
    try:
        texto = await _llm_simulado("20 nuevo y 12 recarga", previo, llm_32)
    finally:
        order_flow._resolver_aclaracion_bidon = original
    draft = get_draft(TELEFONO_SIMULADO) or {}
    v.check(
        draft.get("aclaracion_pendiente") == PENDIENTES_REAL and not draft.get("productos"),
        "tope: descarta los 32 bidones sin respaldo y conserva lo pendiente",
    )
    v.check(
        texto.startswith(MENSAJE_ACLARACION_NO_ENTENDIDA)
        and "2 bidones de 20L y el bidón de 12L" in texto
        and "por ejemplo" in texto
        and "32" not in texto,
        "re-pregunta por capacidad con un ejemplo, sin el 32",
    )
    clear_draft(TELEFONO_SIMULADO)


async def caso_i(ctx: dict, v: Verificador) -> None:
    # Con bidones pendientes, "agrega 5 bidones de 20 recarga" no lo descarta
    # el tope (el mensaje respalda las 5 unidades). El resolver toma la línea
    # completa de 20L como respuesta a lo pendiente de 20L (comportamiento
    # previo a este arreglo); el de 12L sigue pendiente.
    llm = {
        "intencion": "pedido",
        "productos": [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 5, "operacion": "agregar"}],
        "respuesta_sugerida": "Listo.",
    }
    texto = await _llm_simulado("agrega 5 bidones de 20 recarga", _previo_pendiente(PENDIENTES_REAL), llm)
    draft = get_draft(TELEFONO_SIMULADO) or {}
    v.check(
        draft.get("productos") == [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 5}]
        and draft.get("aclaracion_pendiente") == {"capacidad_litros": 12, "cantidad": 1}
        and MENSAJE_ACLARACION_NO_ENTENDIDA not in texto,
        "con pendientes, 'agrega 5 bidones de 20 recarga' quedan 5x 20L Recarga (el tope no lo descarta)",
    )
    # "3 recarga" con 2x 20L pendientes: el mensaje respalda las 3 unidades.
    await _llm_simulado("3 recarga", _previo_pendiente({"capacidad_litros": 20, "cantidad": 2}))
    v.check(
        (get_draft(TELEFONO_SIMULADO) or {}).get("productos")
        == [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 3}],
        "'3 recarga' con 2 pendientes: el cliente dice 3, no se descarta",
    )
    clear_draft(TELEFONO_SIMULADO)

    # Conversación real: pedido en curso, "agrega 5 bidones de 20 recarga" y
    # "mejor 10 de 20".
    clear_draft(CLIENTE["telefono"])
    await _conversar(["3 de 20 recarga y 1 de 12 nuevo"])
    await _conversar(["agrega 5 bidones de 20 recarga"])
    v.check(
        _productos_draft() == {"Bidón 20L Recarga": 8, "Bidón 12L Nuevo": 1},
        f"'agrega 5 bidones de 20 recarga' suma 5: {_productos_draft()}",
    )
    respuestas = await _conversar(["si", "no", "mejor 10 de 20"])
    v.check(
        _productos_draft() == {"Bidón 20L Recarga": 10, "Bidón 12L Nuevo": 1},
        f"'mejor 10 de 20' cambia a 10: {_productos_draft()}",
    )
    v.check(
        _es_resumen(respuestas[-1]) and "no quedó registrado" not in respuestas[-1],
        "vuelve al resumen, sin avisos falsos",
    )
    await _conversar(["cancelar"])


async def caso_j(ctx: dict, v: Verificador) -> None:
    # 12x 12L pendientes y "12 recarga": las dos lecturas dan lo mismo.
    resultado = _resolver_aclaracion_bidon({"capacidad_litros": 12, "cantidad": 12}, "12 recarga")
    v.check(
        _lineas(resultado) == {"Bidón 12L Recarga": 12} and not resultado["aclaracion"],
        "12x 12L pendientes y '12 recarga': se resuelve (cantidad y capacidad coinciden)",
    )
    # 12x 20L y 1x 12L pendientes: "12 recarga" puede ser los 12 de 20L o el
    # de 12L. No se adivina.
    pendientes = [{"capacidad_litros": 20, "cantidad": 12}, {"capacidad_litros": 12, "cantidad": 1}]
    texto = await _llm_simulado("12 recarga", _previo_pendiente(pendientes))
    draft = get_draft(TELEFONO_SIMULADO) or {}
    v.check(
        not draft.get("productos") and draft.get("aclaracion_pendiente") == pendientes,
        "ambiguo: no agrega nada y sigue pendiente",
    )
    v.check(
        "No me quedó claro si «12» es la cantidad de bidones o su capacidad en litros." in texto
        and "12 bidones de 20L y el bidón de 12L" in texto,
        "re-pregunta clara, por capacidad",
    )
    clear_draft(TELEFONO_SIMULADO)


CASOS = [
    ("a", "Parser: todas las líneas de bidón, con y sin variante; sin falsos positivos", caso_a),
    ("b", "'3 de 20 y 1 de 12' completo: pregunta ambas, confirma 3x 20L Recarga y 1x 12L Nuevo", caso_b),
    ("c", "Respuestas 'todos recarga' y 'la de 12 nuevo y las de 20 recarga'", caso_c),
    ("d", "'mejor 2 de 20' baja solo los de 20L, sin falso positivo", caso_d),
    ("e", "_productos_no_registrados compara por capacidad", caso_e),
    ("f", "'20 nuevo y 12 recarga' y variantes: el número es la capacidad pendiente", caso_f),
    ("g", "Conversación real 2026-10-08 por el webhook: sin '32 bidones', pedido en BD", caso_g),
    ("h", "LLM con 32 pendientes: el código resuelve; tope descarta unidades sin respaldo", caso_h),
    ("i", "Sin falsos positivos del tope: 'agrega 5 bidones de 20 recarga', 'mejor 10 de 20'", caso_i),
    ("j", "Caso ambiguo: 12x 12L se resuelve; 12x 20L + 1x 12L re-pregunta", caso_j),
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
