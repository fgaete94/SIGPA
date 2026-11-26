"""
Script temporal para validar el ticket de pedidos grandes (order_flow.
umbrales_superados / _derivar_pedido_grande y notificacion_ejecutiva.
avisar_pedido_grande):

- Un cliente existente que confirma MÁS de UMBRAL_BIDON_20L / UMBRAL_BIDON_12L
  / UMBRAL_DISPENSADOR_BASICO unidades (sumando nuevo y recarga) crea el
  pedido igual, marcado con requiere_revision y motivo_revision; recibe un
  mensaje amable y se avisa a la ejecutiva una vez por pedido (plantilla o
  texto libre). Justo en el umbral sigue el flujo normal.
- Cubre los cambios del borrador (agregar que cruza el umbral, restar que lo
  baja), 1000 bidones (ya no se rechazan), umbrales configurables, cliente
  nuevo sin cambios, fuera de horario, aviso fallido y la API del panel
  (GET /pedidos, GET /rutas/pedidos-pendientes, PATCH /pedidos/{id}).

No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 569333008xx
(incluida la ejecutiva de prueba). Al empezar y al terminar (también si
falla), el script BORRA de ese rango los clientes (con sus pedidos, detalles
y filas de auditoria por ID exacto), los mensajes de mensaje_whatsapp, la
conversación y el borrador. No toca ningún otro cliente.

Los turnos de conversación llaman al LLM real. Nunca se envía nada a Meta:
send_whatsapp_message y send_whatsapp_template se reemplazan por fakes que
solo registran en mensaje_whatsapp (como el envío real) y _post_a_meta por un
centinela que falla la prueba si algo se escapa. n8n no se llama.

Uso: python -m scripts.test_pedido_grande
"""

import asyncio
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.draft_store as draft_store
import app.services.notificacion_ejecutiva as notificacion_ejecutiva
import app.services.order_flow as order_flow
import app.services.whatsapp_client as whatsapp_client
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.main import app
from app.services.conversacion_bot_service import marcar_activa, marcar_inactiva
from app.services.draft_store import clear_draft, get_draft, save_draft
from app.services.mensaje_whatsapp_service import registrar_mensaje
from app.services.order_flow import procesar_mensaje

RANGO_TELEFONOS_SQL = r"^(56)?9333008\d{2}$"
EJECUTIVA_PRUEBA = "56933300899"
USUARIO_PRUEBA = "prueba-pedido-grande@sigpa.test"
PLANTILLA = "aviso_pedido_grande"

CHILE = ZoneInfo("America/Santiago")
DENTRO = datetime(2026, 10, 8, 10, 0, tzinfo=CHILE)  # jueves 10:00
FUERA = datetime(2026, 10, 8, 21, 0, tzinfo=CHILE)  # jueves 21:00

B20R, B20N, B12N = "Bidón 20L Recarga", "Bidón 20L Nuevo", "Bidón 12L Nuevo"
DBAS = "Dispensador Básico"


def _cliente(n: int, nombre: str, apellido: str) -> dict:
    return {
        "telefono_bd": f"+569333008{n:02d}",
        "telefono": f"569333008{n:02d}",
        "nombre": nombre,
        "apellido": apellido,
        "direccion": f"Av. Grande {n}00, Viña del Mar",
    }


CLIENTES = [_cliente(n, nombre, "Prueba") for n, nombre in enumerate(
    ["Ana", "Beto", "Cata", "Dani", "Eli", "Fede", "Gabi", "Hugo", "Inés", "Juan", "Kika", "Lalo",
     "Mora", "Nico", "Olga", "Pepe", "Quena", "Rosa", "Sara"], start=1)]
TELEFONO_NUEVO = "56933300850"


# --------------------------------------------------------------------------
# Fakes (nada sale a Meta)
# --------------------------------------------------------------------------

textos: list[tuple[str, str]] = []
plantillas: list[dict] = []
fugas: list[dict] = []
respuesta_plantilla: dict = {"valor": {"messages": [{"id": "wamid.fake-plantilla"}]}}
texto_falla: dict = {"activo": False}


async def _fake_text(to, message):
    if texto_falla["activo"] and to == EJECUTIVA_PRUEBA:
        await registrar_mensaje(telefono=to, direccion="saliente", tipo="text", contenido=message, estado="fallido")
        return None
    textos.append((to, message))
    await registrar_mensaje(telefono=to, direccion="saliente", tipo="text", contenido=message,
                            meta_message_id="wamid.fake", estado="enviado")
    return {"messages": [{"id": "wamid.fake"}]}


async def _fake_template(to, template_name, language_code, body_params, texto_registro):
    plantillas.append({"to": to, "template_name": template_name, "language_code": language_code,
                       "body_params": list(body_params), "texto_registro": texto_registro})
    respuesta = respuesta_plantilla["valor"]
    await registrar_mensaje(telefono=to, direccion="saliente", tipo="template", contenido=texto_registro,
                            meta_message_id="wamid.fake" if respuesta else None,
                            estado="enviado" if respuesta else "fallido")
    return respuesta


async def _centinela_meta(to, payload, tipo, contenido):
    fugas.append({"to": to, "tipo": tipo})
    return None


async def _usuario_prueba():
    return {"email": USUARIO_PRUEBA, "rol": "admin"}


def _instalar_fakes() -> None:
    notificacion_ejecutiva.send_whatsapp_template = _fake_template
    order_flow.send_whatsapp_message = _fake_text
    whatsapp_route.send_whatsapp_message = _fake_text
    whatsapp_client._post_a_meta = _centinela_meta
    order_flow._ahora = lambda: DENTRO
    settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME = ""
    settings.WHATSAPP_TEMPLATE_PEDIDO_GRANDE_NAME = ""
    settings.EJECUTIVA_PHONE = EJECUTIVA_PRUEBA
    settings.META_WHATSAPP_TOKEN = ""
    settings.UMBRAL_BIDON_20L, settings.UMBRAL_BIDON_12L, settings.UMBRAL_DISPENSADOR_BASICO = 10, 10, 3
    app.dependency_overrides[get_current_user] = _usuario_prueba


def _avisos_pedido(pedido_id: int) -> list[str]:
    """Avisos de pedido grande que llegaron a la ejecutiva (texto o plantilla)."""
    marca = f"{notificacion_ejecutiva.PREFIJO_PEDIDO_GRANDE}{pedido_id}:"
    return [m for to, m in textos if to == EJECUTIVA_PRUEBA and m.startswith(marca)] + [
        p["texto_registro"] for p in plantillas if p["texto_registro"].startswith(marca)
    ]


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


_TEL = "regexp_replace(coalesce(telefono,''), '[^0-9]', '', 'g') ~ :rango"
_IDS = f"select id from cliente where {_TEL}"
_PEDIDOS = f"select id from pedido where cliente_id in ({_IDS})"


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
    for telefono in [c["telefono"] for c in CLIENTES] + [TELEFONO_NUEVO, EJECUTIVA_PRUEBA]:
        draft_store._drafts.pop(telefono, None)
        draft_store._persistidos.pop(telefono, None)
        draft_store._pendientes_borrar.discard(telefono)
        order_flow._derivaciones_avisadas.pop(telefono, None)


async def _contar_rango() -> dict[str, int]:
    conteos = {}
    for tabla in ("cliente", "mensaje_whatsapp", "conversacion_bot", "borrador_pedido"):
        conteos[tabla] = (await _ejecutar(f"select count(*) from {tabla} where {_TEL}", rango=RANGO_TELEFONOS_SQL)).scalar()
    conteos["pedido"] = (await _ejecutar(f"select count(*) from pedido where cliente_id in ({_IDS})",
                                         rango=RANGO_TELEFONOS_SQL)).scalar()
    conteos["detalle_pedido"] = (await _ejecutar(f"select count(*) from detalle_pedido where pedido_id in ({_PEDIDOS})",
                                                 rango=RANGO_TELEFONOS_SQL)).scalar()
    return conteos


async def _crear_cliente(datos: dict) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, apellido_paterno, telefono, direccion, latitud, longitud) "
        "values (:nombre, :apellido, :telefono, :direccion, :lat, :lon) returning id",
        nombre=datos["nombre"], apellido=datos["apellido"], telefono=datos["telefono_bd"],
        direccion=datos["direccion"], lat=Decimal("-33.024500"), lon=Decimal("-71.551800"),
    )
    return result.scalar_one()


async def _ultimo_pedido(telefono: str) -> dict | None:
    rows = await _ejecutar(
        "select p.id, p.requiere_revision, p.motivo_revision, p.estado::text as estado from pedido p "
        "join cliente c on c.id = p.cliente_id "
        "where regexp_replace(coalesce(c.telefono,''), '[^0-9]', '', 'g') ~ :rango order by p.id desc limit 1",
        rango=f"^(56)?{telefono[2:]}$",
    )
    fila = rows.first()
    return dict(fila._mapping) if fila else None


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _nueva_conversacion(phone: str) -> None:
    await marcar_activa(phone)
    clear_draft(phone)
    order_flow._derivaciones_avisadas.pop(phone, None)


async def _decir(phone: str, turno: str) -> str:
    print(f"  Cliente: {turno}")
    respuesta = await procesar_mensaje(phone, "text", turno, None)
    print(f"  Bot [{(get_draft(phone) or {}).get('paso')}]: {respuesta}")
    return respuesta


def _sembrar(phone: str, productos: list[tuple[str, int]], notas: str | None = None) -> None:
    """Borrador de un cliente existente con su dirección confirmada,
    esperando "¿algo más?"."""
    draft = {
        "intencion": "pedido",
        "productos": [{"nombre_producto": n, "cantidad": c} for n, c in productos],
        "aclaracion_pendiente": None, "notas": notas, "nombre_cliente": None,
        "usa_direccion_habitual": True, "direccion_texto": None, "ubicacion": None,
        "ubicacion_rechazada": False, "algo_mas_respondido": False, "pendientes_modelo": [],
        "no_encontrados": [], "paso": "algo_mas", "estado": "armando",
    }
    draft["unidades_pedidas"] = order_flow._unidades_draft(draft)
    save_draft(phone, draft)


async def _hasta_resumen(phone: str) -> str:
    respuesta = ""
    for _ in range(6):
        if (get_draft(phone) or {}).get("estado") == order_flow.ESTADO_ESPERANDO_CONFIRMACION:
            break
        paso = (get_draft(phone) or {}).get("paso")
        respuesta = await _decir(phone, {"algo_mas": "no", "ubicacion": "no puedo compartir la ubicación"}.get(paso, "sí"))
    return respuesta


def _lineas(phone: str) -> dict[str, int]:
    return {p["nombre_producto"]: p["cantidad"] for p in (get_draft(phone) or {}).get("productos") or []}


async def _confirmar(phone: str) -> tuple[str, dict | None]:
    await _hasta_resumen(phone)
    r = await _decir(phone, "si")
    return r, await _ultimo_pedido(phone)


class Verificador:
    def __init__(self):
        self.resultados: list[tuple[str, bool, str]] = []
        self.caso = ""

    def check(self, condicion: bool, descripcion: str) -> None:
        self.resultados.append((self.caso, bool(condicion), descripcion))
        print(f"    [{'OK' if condicion else 'FALLA'}] {descripcion}")


def _verificar_normal(v: Verificador, r: str, pedido: dict | None, etiqueta: str) -> None:
    v.check(pedido is not None and r.startswith(f"¡Pedido #{pedido['id']} confirmado!"), f"{etiqueta}: confirmación normal")
    v.check(pedido is not None and not pedido["requiere_revision"] and pedido["motivo_revision"] is None,
            f"{etiqueta}: pedido sin marca de revisión")
    v.check(pedido is not None and not _avisos_pedido(pedido["id"]), f"{etiqueta}: sin aviso de pedido grande")


def _verificar_grande(v: Verificador, r: str, pedido: dict | None, cliente: dict, detalle: str, motivo: str,
                      etiqueta: str) -> None:
    v.check(r == order_flow.MENSAJE_PEDIDO_GRANDE, f"{etiqueta}: mensaje amable exacto")
    v.check(pedido is not None and pedido["requiere_revision"] and pedido["motivo_revision"] == motivo,
            f"{etiqueta}: pedido marcado, motivo {motivo!r}")
    v.check(pedido is not None and pedido["estado"] == "pendiente", f"{etiqueta}: pedido creado (pendiente)")
    avisos = _avisos_pedido(pedido["id"]) if pedido else []
    v.check(len(avisos) == 1, f"{etiqueta}: exactamente un aviso a la ejecutiva ({len(avisos)})")
    aviso = avisos[0] if avisos else ""
    v.check(f"{cliente['nombre']} {cliente['apellido']}" in aviso and f"+{cliente['telefono']}" in aviso
            and detalle in aviso and motivo in aviso,
            f"{etiqueta}: el aviso trae nombre, teléfono, detalle y motivo")


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_umbrales(v: Verificador) -> None:
    for etiqueta, pedido_ok, pedido_grande, detalle, motivo, c_ok, c_grande in (
        ("Bidón 20L", "quiero 10 bidones de 20 recarga", "quiero 11 bidones de 20 recarga",
         "11x Bidón 20L Recarga", "Bidón 20L: 11 unidades, umbral 10", CLIENTES[0], CLIENTES[1]),
        ("Bidón 12L", "quiero 10 bidones de 12 nuevos", "quiero 11 bidones de 12 nuevos",
         "11x Bidón 12L Nuevo", "Bidón 12L: 11 unidades, umbral 10", CLIENTES[2], CLIENTES[3]),
        ("Dispensador básico", "quiero 3 dispensadores básicos", "quiero 4 dispensadores básicos",
         "4x Dispensador Básico", "Dispensador Básico: 4 unidades, umbral 3", CLIENTES[4], CLIENTES[5]),
    ):
        v.caso = f"umbral {etiqueta}"
        print(f"\n=== {etiqueta}: justo en el umbral no deriva; uno más sí ===")
        await _nueva_conversacion(c_ok["telefono"])
        await _decir(c_ok["telefono"], pedido_ok)
        r, pedido = await _confirmar(c_ok["telefono"])
        _verificar_normal(v, r, pedido, "en el umbral")
        await _nueva_conversacion(c_grande["telefono"])
        await _decir(c_grande["telefono"], pedido_grande)
        r, pedido = await _confirmar(c_grande["telefono"])
        _verificar_grande(v, r, pedido, c_grande, detalle, motivo, "uno más")


async def caso_suma_variantes(v: Verificador) -> None:
    v.caso = "suma de variantes"
    print("\n=== 6 de 20L nuevo + 5 de 20L recarga = 11: deriva ===")
    c = CLIENTES[6]
    await _nueva_conversacion(c["telefono"])
    await _decir(c["telefono"], "quiero 6 bidones de 20 nuevos y 5 de 20 recarga")
    v.check(_lineas(c["telefono"]) == {B20N: 6, B20R: 5}, "borrador 6x 20L Nuevo + 5x 20L Recarga")
    r, pedido = await _confirmar(c["telefono"])
    _verificar_grande(v, r, pedido, c, "6x Bidón 20L Nuevo", "Bidón 20L: 11 unidades, umbral 10", "suma")


async def caso_modificaciones(v: Verificador) -> None:
    v.caso = "agregar cruza el umbral"
    print("\n=== 8 de 20L + 'agrega 5 bidones de 20 recarga' = 13: deriva ===")
    c = CLIENTES[7]
    await _nueva_conversacion(c["telefono"])
    _sembrar(c["telefono"], [(B20R, 8)])
    await _decir(c["telefono"], "agrega 5 bidones de 20 recarga")
    v.check(_lineas(c["telefono"]) == {B20R: 13}, "borrador con 13")
    r, pedido = await _confirmar(c["telefono"])
    _verificar_grande(v, r, pedido, c, "13x Bidón 20L Recarga", "Bidón 20L: 13 unidades, umbral 10", "8 + 5")

    v.caso = "restar baja del umbral"
    print("\n=== 12 de 20L + 'quítale 3' = 9: no deriva ===")
    c = CLIENTES[8]
    await _nueva_conversacion(c["telefono"])
    _sembrar(c["telefono"], [(B20R, 12)])
    await _decir(c["telefono"], "quítale 3")
    v.check(_lineas(c["telefono"]) == {B20R: 9}, "12 - 3 = 9")
    r, pedido = await _confirmar(c["telefono"])
    _verificar_normal(v, r, pedido, "12 - 3")

    v.caso = "resta correcta"
    print("\n=== 20 de 20L + 'descuéntame 5' = 15 (resta, no suma ni reemplaza) ===")
    c = CLIENTES[9]
    await _nueva_conversacion(c["telefono"])
    await _decir(c["telefono"], "quiero 20 bidones de 20 recarga")
    v.check(_lineas(c["telefono"]) == {B20R: 20}, "borrador con 20")
    await _decir(c["telefono"], "descuéntame 5")
    v.check(_lineas(c["telefono"]) == {B20R: 15}, "20 - 5 = 15 (no 25 ni 5)")
    r, pedido = await _confirmar(c["telefono"])
    _verificar_grande(v, r, pedido, c, "15x Bidón 20L Recarga", "Bidón 20L: 15 unidades, umbral 10", "15")


async def caso_mil(v: Verificador) -> None:
    v.caso = "1000 bidones"
    print("\n=== 1000 bidones: ya no se rechaza, se guarda marcado y se deriva ===")
    c = CLIENTES[10]
    await _nueva_conversacion(c["telefono"])
    r = await _decir(c["telefono"], "quiero 1000 bidones de 20 recarga")
    v.check(r != order_flow.MENSAJE_CANTIDAD_ALTA, "no responde 'cantidad muy alta'")
    v.check(_lineas(c["telefono"]) == {B20R: 1000}, "1000 entran al borrador")
    r, pedido = await _confirmar(c["telefono"])
    _verificar_grande(v, r, pedido, c, "1000x Bidón 20L Recarga", "Bidón 20L: 1000 unidades, umbral 10", "1000")


async def caso_cliente_nuevo(v: Verificador) -> None:
    v.caso = "cliente nuevo"
    print("\n=== Cliente nuevo con 11 bidones: su flujo no cambia ===")
    phone = TELEFONO_NUEVO
    await _nueva_conversacion(phone)
    for turno in ("quiero 11 bidones de 20 recarga", "Nora Nueva", "Calle Nueva 123, Viña del Mar",
                  "no puedo compartir la ubicación"):
        await _decir(phone, turno)
    r, pedido = await _confirmar(phone)
    _verificar_normal(v, r, pedido, "cliente nuevo")


async def caso_configurable(v: Verificador) -> None:
    v.caso = "umbral configurable"
    print("\n=== Umbrales configurables: UMBRAL_BIDON_20L=15 (11 no deriva) y =5 (6 deriva) ===")
    v.check(order_flow.umbrales_superados([{"nombre": B20R, "cantidad": 11}]) != [], "con el default 10, 11 supera")
    settings.UMBRAL_BIDON_20L = 15
    try:
        c = CLIENTES[11]
        await _nueva_conversacion(c["telefono"])
        _sembrar(c["telefono"], [(B20R, 11)])
        r, pedido = await _confirmar(c["telefono"])
        _verificar_normal(v, r, pedido, "umbral 15, pide 11")
        settings.UMBRAL_BIDON_20L = 5
        c = CLIENTES[12]
        await _nueva_conversacion(c["telefono"])
        _sembrar(c["telefono"], [(B20R, 6)])
        r, pedido = await _confirmar(c["telefono"])
        _verificar_grande(v, r, pedido, c, "6x Bidón 20L Recarga", "Bidón 20L: 6 unidades, umbral 5", "umbral 5, pide 6")
    finally:
        settings.UMBRAL_BIDON_20L = 10
    settings.UMBRAL_DISPENSADOR_BASICO = 1
    try:
        v.check(order_flow.umbrales_superados([{"nombre": DBAS, "cantidad": 2}])
                == ["Dispensador Básico: 2 unidades, umbral 1"], "UMBRAL_DISPENSADOR_BASICO también se lee del setting")
    finally:
        settings.UMBRAL_DISPENSADOR_BASICO = 3
    v.check(order_flow.umbrales_superados([{"nombre": "Promo Dispensador Básico + 2 Bidones", "cantidad": 2},
                                           {"nombre": DBAS, "cantidad": 2}, {"nombre": "Dispensador USB", "cantidad": 9}])
            == ["Dispensador Básico: 4 unidades, umbral 3"], "promo básica cuenta como dispensador básico; el USB no")


async def caso_plantilla(v: Verificador) -> None:
    v.caso = "plantilla"
    print("\n=== Plantilla configurada: 4 variables en una línea; rechazada: texto libre; una vez por pedido ===")
    largo = ", ".join(f"{n}x Bidón 20L Recarga" for n in range(1, 40))
    params = notificacion_ejecutiva.parametros_pedido_grande("Ana\nMaría  Soto", "+56 9 3330 0801", largo, "a\nb")
    v.check(params[0] == "Ana María Soto" and params[3] == "a b" and params[1] == "+56933300801",
            "saltos de línea y espacios dobles colapsados; teléfono normalizado")
    v.check(len(params[2]) <= 200 and params[2].endswith("…"), f"detalle largo recortado a 200 ({len(params[2])})")
    settings.WHATSAPP_TEMPLATE_PEDIDO_GRANDE_NAME = PLANTILLA
    try:
        c = CLIENTES[13]
        await _nueva_conversacion(c["telefono"])
        _sembrar(c["telefono"], [(B20R, 30), (B12N, 25), ("Dispensador USB", 5), (DBAS, 9),
                                  ("Promo Dispensador Básico + 2 Bidones", 3), ("Bidón 12L Recarga", 14)],
                 notas="Bidones de 20L para la promo")
        antes = len(plantillas)
        r, pedido = await _confirmar(c["telefono"])
        nuevas = plantillas[antes:]
        v.check(r == order_flow.MENSAJE_PEDIDO_GRANDE and pedido["requiere_revision"], "pedido marcado y mensaje amable")
        v.check(len(nuevas) == 1 and nuevas[0]["template_name"] == PLANTILLA and nuevas[0]["language_code"] == "es_CL",
                "una plantilla aviso_pedido_grande es_CL")
        params = nuevas[0]["body_params"] if nuevas else []
        v.check(len(params) == 4 and all("\n" not in p and p == " ".join(p.split()) for p in params),
                f"4 variables, cada una en una sola línea: {params}")
        v.check(len(params) == 4 and params[0] == f"{c['nombre']} {c['apellido']}" and params[1] == f"+{c['telefono']}",
                "{{1}} nombre y {{2}} teléfono")
        v.check(len(params) == 4 and len(params[2]) <= 200, f"{{{{3}}}} detalle recortado a 200 ({len(params[2]) if params else 0})")
        v.check(len(params) == 4 and "Bidón 20L: 30 unidades, umbral 10" in params[3]
                and "Bidón 12L: 39 unidades, umbral 10" in params[3]
                and "Dispensador Básico: 12 unidades, umbral 3" in params[3], f"{{{{4}}}} motivo: {params[3] if params else ''}")
        v.check(not [m for to, m in textos if to == EJECUTIVA_PRUEBA and m.startswith(f"Pedido grande #{pedido['id']}:")],
                "con la plantilla aceptada no se envía texto libre")
        # Una sola vez por pedido aunque se vuelva a pedir el aviso.
        await notificacion_ejecutiva.avisar_pedido_grande(pedido["id"], "x", c["telefono"], "x", "x", _fake_text)
        notificacion_ejecutiva._pedidos_grandes_avisados.discard(pedido["id"])  # solo queda la marca en la BD
        await notificacion_ejecutiva.avisar_pedido_grande(pedido["id"], "x", c["telefono"], "x", "x", _fake_text)
        v.check(len(_avisos_pedido(pedido["id"])) == 1, "re-pedir el aviso no lo reenvía (marca en memoria y en la BD)")

        respuesta_plantilla["valor"] = None  # Meta rechaza la plantilla
        c = CLIENTES[14]
        await _nueva_conversacion(c["telefono"])
        _sembrar(c["telefono"], [(B20R, 12)])
        r, pedido = await _confirmar(c["telefono"])
        avisos_texto = [m for to, m in textos if to == EJECUTIVA_PRUEBA and m.startswith(f"Pedido grande #{pedido['id']}:")]
        v.check(len(avisos_texto) == 1, "plantilla rechazada: se envía el texto libre, una vez")
    finally:
        settings.WHATSAPP_TEMPLATE_PEDIDO_GRANDE_NAME = ""
        respuesta_plantilla["valor"] = {"messages": [{"id": "wamid.fake-plantilla"}]}
    v.check(sum(1 for p in plantillas if p["template_name"] == order_flow.settings.WHATSAPP_TEMPLATE_EJECUTIVA_NAME) == 0,
            "la plantilla de derivación no se usó")


async def caso_derivacion_independiente(v: Verificador) -> None:
    v.caso = "aviso independiente"
    print("\n=== El aviso de pedido grande y la derivación por conversación no se bloquean ===")
    c = CLIENTES[15]
    phone = c["telefono"]
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 12)])
    r = await _decir(phone, "pásame con la ejecutiva")
    v.check("ya le avisé a una ejecutiva" in r.lower(), "deriva a la ejecutiva (aviso de conversación)")
    r, pedido = await _confirmar(phone)
    v.check(len(_avisos_pedido(pedido["id"])) == 1, "la derivación previa no bloquea el aviso del pedido grande")
    # Otro cliente: primero el pedido grande y después, en una conversación
    # nueva, la derivación: el aviso del pedido no la bloquea.
    c = CLIENTES[18]
    phone = c["telefono"]
    await _nueva_conversacion(phone)
    _sembrar(phone, [(B20R, 12)])
    r, pedido = await _confirmar(phone)
    v.check(len(_avisos_pedido(pedido["id"])) == 1, "aviso del pedido grande enviado")
    v.check(not await notificacion_ejecutiva.ejecutiva_avisada_desde(phone, datetime.utcnow()),
            "ejecutiva_avisada_desde ignora la marca de pedido grande")
    await _nueva_conversacion(phone)
    antes = len([1 for to, m in textos if to == EJECUTIVA_PRUEBA and f"+{phone}" in m and not m.startswith("Pedido grande #")])
    r = await _decir(phone, "pásame con la ejecutiva")
    despues = len([1 for to, m in textos if to == EJECUTIVA_PRUEBA and f"+{phone}" in m and not m.startswith("Pedido grande #")])
    v.check(r.startswith("Listo") and despues == antes + 1, "el aviso del pedido grande no bloquea la derivación")


async def caso_fuera_horario_y_falla(v: Verificador) -> None:
    v.caso = "fuera de horario"
    print("\n=== Fuera de horario: texto de horario de atención ===")
    c = CLIENTES[16]
    order_flow._ahora = lambda: FUERA
    try:
        await _nueva_conversacion(c["telefono"])
        _sembrar(c["telefono"], [(B20R, 11)])
        r, pedido = await _confirmar(c["telefono"])
    finally:
        order_flow._ahora = lambda: DENTRO
    v.check(r.startswith("¡Gracias por tu pedido! Como es un pedido grande") and "te contactará a la brevedad" not in r
            and "Nuestro horario de atención es de 09:00 a 18:00 hrs" in r, "usa el texto de horario de atención")
    v.check(pedido["requiere_revision"], "pedido marcado")

    v.caso = "aviso fallido"
    print("\n=== Si el aviso falla: el pedido queda guardado y marcado; error en el log ===")
    c = CLIENTES[17]
    texto_falla["activo"] = True
    try:
        await _nueva_conversacion(c["telefono"])
        _sembrar(c["telefono"], [(B20R, 11)])
        r, pedido = await _confirmar(c["telefono"])
    finally:
        texto_falla["activo"] = False
    v.check(r == order_flow.MENSAJE_PEDIDO_GRANDE, "el cliente igual recibe el mensaje amable")
    v.check(pedido is not None and pedido["requiere_revision"], "pedido guardado y marcado")
    v.check(not _avisos_pedido(pedido["id"]), "no hay aviso registrado como enviado")


async def caso_api(v: Verificador) -> None:
    v.caso = "API panel"
    print("\n=== API: GET /pedidos, GET /rutas/pedidos-pendientes, PATCH /pedidos/{id} ===")
    pedido = await _ultimo_pedido(CLIENTES[1]["telefono"])  # el de 11 bidones de 20L
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        r = await http.get("/pedidos", params={"requiere_revision": "true"})
        ids = {p["id"]: p for p in r.json()}
        v.check(r.status_code == 200 and pedido["id"] in ids and ids[pedido["id"]]["requiere_revision"]
                and ids[pedido["id"]]["motivo_revision"] == "Bidón 20L: 11 unidades, umbral 10",
                "GET /pedidos?requiere_revision=true lo incluye con el motivo")
        r = await http.get("/rutas/pedidos-pendientes")
        fila = next((p for p in r.json() if p["pedido_id"] == pedido["id"]), None)
        v.check(fila is not None and fila["requiere_revision"] and fila["motivo_revision"], "pedidos-pendientes expone los campos")
        r = await http.get(f"/pedidos/{pedido['id']}")
        v.check(r.status_code == 200 and r.json()["requiere_revision"], "GET /pedidos/{id} expone los campos")
        r = await http.patch(f"/pedidos/{pedido['id']}", json={"requiere_revision": False})
        v.check(r.status_code == 200 and r.json()["requiere_revision"] is False and r.json()["motivo_revision"] is None,
                "PATCH requiere_revision=false limpia la marca y el motivo")
    auditoria = (await _ejecutar(
        "select antes->>'requiere_revision', despues->>'requiere_revision' from auditoria "
        "where entidad = 'pedido' and entidad_id = :id and usuario = :u and accion = 'actualizar'",
        id=pedido["id"], u=USUARIO_PRUEBA)).all()
    v.check(auditoria == [("true", "false")], f"auditado como el resto: {auditoria}")
    del app.dependency_overrides[get_current_user]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            r = await http.patch(f"/pedidos/{pedido['id']}", json={"requiere_revision": False})
        v.check(r.status_code == 401, "sin JWT: 401")
    finally:
        app.dependency_overrides[get_current_user] = _usuario_prueba


# --------------------------------------------------------------------------


async def main() -> None:
    _instalar_fakes()
    v = Verificador()
    try:
        await _limpiar_rango()
        for datos in CLIENTES:
            await _crear_cliente(datos)
        for caso in (caso_umbrales, caso_suma_variantes, caso_modificaciones, caso_mil, caso_cliente_nuevo,
                     caso_configurable, caso_plantilla, caso_derivacion_independiente, caso_fuera_horario_y_falla,
                     caso_api):
            try:
                await caso(v)
            except Exception as exc:  # un caso roto no impide correr los demás
                v.caso = caso.__name__
                v.check(False, f"excepción: {exc!r}")
        v.caso = "seguridad"
        v.check(not fugas, f"nada llegó a Meta ({len(fugas)} envíos interceptados)")
    finally:
        await _limpiar_rango()
        app.dependency_overrides.pop(get_current_user, None)
        conteos = await _contar_rango()
        print(f"\nFilas de prueba tras la limpieza: {conteos}")
        v.caso = "limpieza"
        v.check(all(n == 0 for n in conteos.values()), "0 filas de prueba")

    print("\n=== Resumen por caso ===")
    casos_ok: dict[str, list[bool]] = {}
    for caso, ok, _ in v.resultados:
        casos_ok.setdefault(caso, []).append(ok)
    for caso, oks in casos_ok.items():
        print(f"  {caso:28s} {sum(oks)}/{len(oks)} {'OK' if all(oks) else 'FALLA'}")
    fallas = [(c, d) for c, ok, d in v.resultados if not ok]
    print(f"\nTotal: {len(v.resultados) - len(fallas)}/{len(v.resultados)} OK")
    for c, d in fallas:
        print(f"  FALLA [{c}] {d}")
    if fallas:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
