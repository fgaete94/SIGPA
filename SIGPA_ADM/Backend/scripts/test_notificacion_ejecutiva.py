"""
Script temporal para validar la notificación a la ejecutiva que dice quién
es el cliente (app/services/notificacion_ejecutiva.py), en el mensaje
espontáneo (whatsapp.py::_enrutar_a_ejecutiva) y en el teléfono duplicado
(order_flow.py::_escalar_telefono_duplicado).
No es parte del código final: solo para validar manualmente el comportamiento.

La hora siempre se inyecta (nunca depende del reloj real).

Datos de prueba: todos los teléfonos usados están en el rango 56933300xxx.
Al empezar y al terminar, el script BORRA de ese rango los clientes (con sus
pedidos y detalles), los mensajes de mensaje_whatsapp y las filas de
conversacion_bot. No toca ningún otro cliente ni pedido.

Nunca envía mensajes reales: send_whatsapp_message se reemplaza por un fake
en whatsapp.py y en order_flow.py.

Uso: python -m scripts.test_notificacion_ejecutiva
"""

import asyncio
import uuid
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.notificacion_ejecutiva as notificacion_ejecutiva
import app.services.order_flow as order_flow
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.conversacion_bot_service import esta_activa, marcar_activa
from app.services.draft_store import clear_draft, get_draft
from app.services.order_flow import procesar_mensaje

CHILE = ZoneInfo("America/Santiago")
DENTRO = datetime(2026, 10, 8, 10, 0, tzinfo=CHILE)  # jueves 10:00
FUERA = datetime(2026, 10, 8, 21, 0, tzinfo=CHILE)  # jueves 21:00

RANGO_TELEFONOS_SQL = r"^(56)?933300\d{3}$"

CIERRE = "Por favor revisa y continúa la conversación."
MARCA = " (fuera de horario)"

LEA_DENTRO = (
    "¡Hola! Soy Lea, tu asistente virtual. Gracias por escribirnos. "
    "Una ejecutiva revisará tu mensaje y te contactará a la brevedad."
)
LEA_FUERA = (
    "¡Hola! Soy Lea, tu asistente virtual. Gracias por escribirnos. "
    "Nuestro horario de atención es de 09:00 a 18:00 hrs. "
    "Una ejecutiva se pondrá en contacto contigo dentro del horario de atención."
)

# (nombre, apellido_paterno, apellido_materno, teléfono guardado en la BD)
CLIENTE_COMPLETO = ("Mariana", "Soto", "Rojas", "+56933300001")
CLIENTE_SIN_APELLIDOS = ("Pedro", None, None, "56933300002")
CLIENTE_UN_APELLIDO = ("Rosa", "Díaz", "", "933300005")
DUPLICADO_1 = ("Laura", "Pérez", None, "56933300003")
DUPLICADO_2 = ("Jorge", "Muñoz", "Vera", "+56 9 3330 0003")
CLIENTE_FORMATOS = ("Tomás", "Lagos", None, "+56 9 3330 0004")
TELEFONO_NUEVO = "56933300009"


def _existente(nombre: str, telefono: str) -> str:
    return f"Hola, el cliente {nombre} ({telefono}) escribió al WhatsApp de pedidos. {CIERRE}"


def _nuevo(telefono: str) -> str:
    return f"Hola, un cliente nuevo ({telefono}) escribió al WhatsApp de pedidos. {CIERRE}"


def _duplicado(telefono: str, nombres: str) -> str:
    return (
        f"Hola, el teléfono {telefono} está asociado a más de un cliente ({nombres}) "
        f"y escribió al WhatsApp de pedidos. {CIERRE}"
    )


def _generico(telefono: str) -> str:
    return f"Hola, un cliente ({telefono}) escribió al WhatsApp de pedidos. {CIERRE}"


# --------------------------------------------------------------------------
# Helpers de BD
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


async def _crear_clientes() -> None:
    for nombre, paterno, materno, telefono in (
        CLIENTE_COMPLETO,
        CLIENTE_SIN_APELLIDOS,
        CLIENTE_UN_APELLIDO,
        DUPLICADO_1,
        DUPLICADO_2,
        CLIENTE_FORMATOS,
    ):
        await _ejecutar(
            "insert into cliente (nombre, apellido_paterno, apellido_materno, telefono) "
            "values (:nombre, :paterno, :materno, :telefono)",
            nombre=nombre,
            paterno=paterno,
            materno=materno,
            telefono=telefono,
        )


# --------------------------------------------------------------------------
# Verificador y fakes
# --------------------------------------------------------------------------


class Verificador:
    def __init__(self) -> None:
        self.errores: list[str] = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'OK' if condicion else 'FALLO'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


@contextmanager
def _fake_send(enviados: list):
    async def fake_send(to: str, message: str):
        enviados.append((to, message))
        print(f"  [fake send] a {to}: {message}")
        return {"messages": [{"id": f"wamid.fake-{uuid.uuid4().hex}"}]}

    originales = (whatsapp_route.send_whatsapp_message, order_flow.send_whatsapp_message)
    whatsapp_route.send_whatsapp_message = fake_send
    order_flow.send_whatsapp_message = fake_send
    try:
        yield
    finally:
        whatsapp_route.send_whatsapp_message, order_flow.send_whatsapp_message = originales


async def _derivar(phone: str, ahora: datetime = DENTRO) -> tuple[str | None, str | None]:
    """Corre _enrutar_a_ejecutiva y devuelve (mensaje al cliente,
    notificación a la ejecutiva)."""
    enviados = []
    with _fake_send(enviados):
        await whatsapp_route._enrutar_a_ejecutiva(phone, ahora)
    al_cliente = next((m for to, m in enviados if to == phone), None)
    a_ejecutiva = next((m for to, m in enviados if to == settings.EJECUTIVA_PHONE), None)
    return al_cliente, a_ejecutiva


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(v: Verificador) -> None:
    _, notif = await _derivar("56933300001")
    esperado = _existente("Mariana Soto Rojas", "+56933300001")
    v.check(notif == esperado, f"texto exacto: {notif!r}")


async def caso_b(v: Verificador) -> None:
    _, notif = await _derivar("56933300002")
    v.check(notif == _existente("Pedro", "+56933300002"), f"sin apellidos: {notif!r}")
    _, notif = await _derivar("56933300005")
    v.check(notif == _existente("Rosa Díaz", "+56933300005"), f"solo apellido paterno (materno vacío): {notif!r}")
    v.check(
        all("None" not in (n or "") and "  " not in (n or "") for n in (notif,)),
        "no aparece 'None' ni espacio doble",
    )


async def caso_c(v: Verificador) -> None:
    _, notif = await _derivar(TELEFONO_NUEVO)
    v.check(notif == _nuevo("+56933300009"), f"cliente nuevo: {notif!r}")


async def caso_d(v: Verificador) -> None:
    _, notif = await _derivar("56933300003")
    v.check(
        notif == _duplicado("+56933300003", "Laura Pérez, Jorge Muñoz Vera"),
        f"lista ambos nombres: {notif!r}",
    )


async def caso_e(v: Verificador) -> None:
    esperado = _existente("Tomás Lagos", "+56933300004")
    for formato in ("56933300004", "+56933300004", "933300004", "+56 9 3330 0004", "56 9 3330 0004"):
        tipo, nombres = await notificacion_ejecutiva.clasificar_remitente(formato)
        _, notif = await _derivar(formato)
        v.check(
            tipo == notificacion_ejecutiva.CLIENTE_EXISTENTE and nombres == ["Tomás Lagos"] and notif == esperado,
            f"{formato!r} -> existente, teléfono +56933300004",
        )


async def caso_f(v: Verificador) -> None:
    for etiqueta, phone, base in (
        ("existente", "56933300001", _existente("Mariana Soto Rojas", "+56933300001")),
        ("nuevo", TELEFONO_NUEVO, _nuevo("+56933300009")),
        ("duplicado", "56933300003", _duplicado("+56933300003", "Laura Pérez, Jorge Muñoz Vera")),
    ):
        _, dentro = await _derivar(phone, DENTRO)
        _, fuera = await _derivar(phone, FUERA)
        v.check(dentro == base, f"{etiqueta} dentro de horario: sin marca")
        v.check(fuera == base + MARCA, f"{etiqueta} fuera de horario: con la marca al final")


async def caso_g(v: Verificador) -> None:
    class SesionRota:
        def __call__(self):
            raise ConnectionError("BD caída (simulada)")

    original = notificacion_ejecutiva.SessionLocal
    notificacion_ejecutiva.SessionLocal = SesionRota()
    try:
        al_cliente, notif = await _derivar("56933300001", FUERA)
    finally:
        notificacion_ejecutiva.SessionLocal = original
    v.check(al_cliente == LEA_FUERA, "el cliente recibe igual el mensaje de Lea")
    v.check(notif == _generico("+56933300001") + MARCA, f"la ejecutiva recibe el texto genérico: {notif!r}")
    _, notif = await _derivar("56933300001")
    v.check(notif == _existente("Mariana Soto Rojas", "+56933300001"), "con la BD de vuelta, vuelve al texto con nombre")


async def caso_h(v: Verificador) -> None:
    for etiqueta, phone, ahora, lea, notif_esperada in (
        ("existente, dentro", "56933300001", DENTRO, LEA_DENTRO, _existente("Mariana Soto Rojas", "+56933300001")),
        ("nuevo, fuera", TELEFONO_NUEVO, FUERA, LEA_FUERA, _nuevo("+56933300009") + MARCA),
    ):
        v.check(not await esta_activa(phone), f"{etiqueta}: no hay conversación activa")
        enviados = []
        mensaje = {
            "from": phone,
            "id": f"wamid.test-notif-{uuid.uuid4().hex}",
            "type": "text",
            "text": {"body": "hola, necesito agua"},
        }
        with _fake_send(enviados):
            await whatsapp_route._procesar_mensaje_entrante(mensaje, ahora)
        v.check(enviados[:1] == [(phone, lea)], f"{etiqueta}: el cliente recibe el mensaje de Lea correcto")
        v.check(
            enviados[1:] == [(settings.EJECUTIVA_PHONE, notif_esperada)],
            f"{etiqueta}: la ejecutiva recibe el texto correcto",
        )
        v.check(get_draft(phone) is None, f"{etiqueta}: el bot no procesa el mensaje")


async def caso_i(v: Verificador) -> None:
    phone = "56933300003"
    await marcar_activa(phone)
    datetime_original = order_flow.datetime

    class RelojFijo(datetime):
        @classmethod
        def now(cls, tz=None):
            return FUERA if tz is None else FUERA.astimezone(tz)

    enviados = []
    order_flow.datetime = RelojFijo
    try:
        with _fake_send(enviados):
            respuesta = await procesar_mensaje(
                phone=phone, message_type="text", message_text="quiero 2 bidones de 20 litros", location=None
            )
    finally:
        order_flow.datetime = datetime_original
    v.check(respuesta == LEA_FUERA, "el cliente recibe el mensaje de Lea (fuera de horario)")
    v.check(
        enviados == [(settings.EJECUTIVA_PHONE, _duplicado("+56933300003", "Laura Pérez, Jorge Muñoz Vera") + MARCA)],
        "la ejecutiva recibe el texto de duplicado con la marca",
    )
    v.check(not await esta_activa(phone), "se cierra la conversación del bot (igual que antes)")
    v.check(get_draft(phone) is None, "no toma el pedido")
    clear_draft(phone)


CASOS = [
    ("a", "Cliente existente con nombre y apellidos", caso_a),
    ("b", "Cliente existente sin apellido: sin 'None' ni espacio doble", caso_b),
    ("c", "Teléfono sin cliente: 'cliente nuevo'", caso_c),
    ("d", "Teléfono en dos clientes: lista ambos nombres", caso_d),
    ("e", "Mismo cliente con el teléfono en formatos distintos", caso_e),
    ("f", "Marca '(fuera de horario)' solo fuera de horario", caso_f),
    ("g", "Falla de la BD: texto genérico y el flujo sigue", caso_g),
    ("h", "Flujo completo de mensaje espontáneo (existente y nuevo)", caso_h),
    ("i", "Flujo de teléfono duplicado (order_flow) usa el texto de duplicado", caso_i),
]


async def main() -> None:
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
