"""
Script temporal para validar el horario de atención y los mensajes de
derivación a la ejecutiva (app/services/horario_atencion.py), su uso en el
mensaje espontáneo (whatsapp.py) y en el teléfono duplicado (order_flow.py),
y la presentación del asistente (Lea) en una conversación activa.
No es parte del código final: solo para validar manualmente el comportamiento.

La hora siempre se inyecta (nunca depende del reloj real) y la configuración
(HORARIO_ATENCION_INICIO/FIN, DIAS_ATENCION, NOMBRE_ASISTENTE) se cambia en
settings durante cada caso y se restaura al terminarlo.

Datos de prueba: todos los teléfonos usados están en el rango 56933200xxx.
Al empezar y al terminar, el script BORRA de ese rango los clientes (con sus
pedidos y detalles), los mensajes de mensaje_whatsapp y las filas de
conversacion_bot. No toca ningún otro cliente ni pedido.

Nunca envía mensajes reales: send_whatsapp_message se reemplaza por un fake
en whatsapp.py y en order_flow.py.

Uso: python -m scripts.test_horario_atencion
"""

import asyncio
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import text

import app.api.routes.whatsapp as whatsapp_route
import app.services.order_flow as order_flow
from app.core.config import MENSAJE_NOTIFICACION_EJECUTIVA, settings
from app.core.database import SessionLocal
from app.services.conversacion_bot_service import esta_activa, marcar_activa
from app.services.draft_store import clear_draft, get_draft
from app.services.horario_atencion import (
    en_horario_atencion,
    marcar_notificacion_ejecutiva,
    mensaje_derivacion_ejecutiva,
)

CHILE = ZoneInfo("America/Santiago")
UTC = ZoneInfo("UTC")

RANGO_TELEFONOS_SQL = r"^(56)?933200\d{3}$"
TELEFONO_ESPONTANEO = "56933200001"
TELEFONO_ACTIVO = "56933200002"
TELEFONO_DUPLICADO = "56933200003"
TELEFONOS_PRUEBA = (TELEFONO_ESPONTANEO, TELEFONO_ACTIVO, TELEFONO_DUPLICADO)

MENSAJE_DENTRO = (
    "¡Hola! Soy Lea, tu asistente virtual. Gracias por escribirnos. "
    "Una ejecutiva revisará tu mensaje y te contactará a la brevedad."
)
MENSAJE_FUERA = (
    "¡Hola! Soy Lea, tu asistente virtual. Gracias por escribirnos. "
    "Nuestro horario de atención es de 09:00 a 18:00 hrs. "
    "Una ejecutiva se pondrá en contacto contigo dentro del horario de atención."
)

# Jueves 8 de octubre de 2026 (horario de verano en Chile, UTC-3).
JUEVES = (2026, 10, 8)
SABADO = (2026, 10, 10)
DOMINGO = (2026, 10, 11)
# Miércoles 10 de junio de 2026 (horario de invierno en Chile, UTC-4).
MIERCOLES_INVIERNO = (2026, 6, 10)


def _chile(fecha: tuple[int, int, int], hora: int, minuto: int = 0) -> datetime:
    return datetime(*fecha, hora, minuto, tzinfo=CHILE)


@contextmanager
def _config(**valores):
    originales = {clave: getattr(settings, clave) for clave in valores}
    for clave, valor in valores.items():
        setattr(settings, clave, valor)
    try:
        yield
    finally:
        for clave, valor in originales.items():
            setattr(settings, clave, valor)


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
    for phone in TELEFONOS_PRUEBA:
        clear_draft(phone)


# --------------------------------------------------------------------------
# Verificador
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


def _mensaje_texto(phone: str, texto: str) -> dict:
    return {
        "from": phone,
        "id": f"wamid.test-horario-{uuid.uuid4().hex}",
        "type": "text",
        "text": {"body": texto},
    }


# --------------------------------------------------------------------------
# Casos: función de horario
# --------------------------------------------------------------------------


async def caso_a(v: Verificador) -> None:
    v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 8, 59)) == MENSAJE_FUERA, "08:59 -> fuera de horario")


async def caso_b(v: Verificador) -> None:
    v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 9, 0)) == MENSAJE_DENTRO, "09:00 exacto -> dentro de horario")


async def caso_c(v: Verificador) -> None:
    v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 17, 59)) == MENSAJE_DENTRO, "17:59 -> dentro de horario")


async def caso_d(v: Verificador) -> None:
    v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 18, 0)) == MENSAJE_FUERA, "18:00 exacto -> fuera de horario")


async def caso_e(v: Verificador) -> None:
    v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 23, 30)) == MENSAJE_FUERA, "23:30 -> fuera de horario")
    v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 3, 0)) == MENSAJE_FUERA, "03:00 -> fuera de horario")


async def caso_f(v: Verificador) -> None:
    for nombre, fecha in (("sábado", SABADO), ("domingo", DOMINGO)):
        v.check(
            mensaje_derivacion_ejecutiva(_chile(fecha, 11, 0)) == MENSAJE_DENTRO,
            f"{nombre} 11:00 con DIAS_ATENCION por defecto (todos los días) -> dentro",
        )
        v.check(
            mensaje_derivacion_ejecutiva(_chile(fecha, 20, 0)) == MENSAJE_FUERA,
            f"{nombre} 20:00 con DIAS_ATENCION por defecto -> fuera (por la hora)",
        )
    for dias in ("lunes,martes,miercoles,jueves,viernes", "lunes-viernes", "Lun-Vie"):
        with _config(DIAS_ATENCION=dias):
            texto_sabado = mensaje_derivacion_ejecutiva(_chile(SABADO, 11, 0))
            texto_domingo = mensaje_derivacion_ejecutiva(_chile(DOMINGO, 11, 0))
            v.check(
                not en_horario_atencion(_chile(SABADO, 11, 0))
                and "de lunes a viernes, de 09:00 a 18:00 hrs" in texto_sabado,
                f"DIAS_ATENCION={dias!r}: sábado 11:00 -> fuera, informa 'de lunes a viernes'",
            )
            v.check(
                not en_horario_atencion(_chile(DOMINGO, 11, 0)) and texto_domingo == texto_sabado,
                f"DIAS_ATENCION={dias!r}: domingo 11:00 -> fuera",
            )
            v.check(
                mensaje_derivacion_ejecutiva(_chile(JUEVES, 11, 0)) == MENSAJE_DENTRO,
                f"DIAS_ATENCION={dias!r}: jueves 11:00 -> dentro",
            )
    print(f"    texto con lunes-viernes: {texto_sabado}")


async def caso_g(v: Verificador) -> None:
    # 12:30 UTC = 09:30 en verano (UTC-3) y 08:30 en invierno (UTC-4).
    verano_utc = datetime(*JUEVES, 12, 30, tzinfo=UTC)
    invierno_utc = datetime(*MIERCOLES_INVIERNO, 12, 30, tzinfo=UTC)
    v.check(
        verano_utc.astimezone(CHILE).utcoffset() == timedelta(hours=-3)
        and invierno_utc.astimezone(CHILE).utcoffset() == timedelta(hours=-4),
        "zoneinfo: octubre es UTC-3 (verano) y junio UTC-4 (invierno)",
    )
    v.check(mensaje_derivacion_ejecutiva(verano_utc) == MENSAJE_DENTRO, "verano: 12:30 UTC = 09:30 Chile -> dentro")
    v.check(mensaje_derivacion_ejecutiva(invierno_utc) == MENSAJE_FUERA, "invierno: 12:30 UTC = 08:30 Chile -> fuera")
    # Fecha sin zona horaria: se asume UTC (como datetime.utcnow()).
    v.check(
        mensaje_derivacion_ejecutiva(verano_utc.replace(tzinfo=None)) == MENSAJE_DENTRO
        and mensaje_derivacion_ejecutiva(invierno_utc.replace(tzinfo=None)) == MENSAJE_FUERA,
        "fecha sin zona horaria se interpreta como UTC",
    )
    # 21:30 UTC = 18:30 en verano (fuera) y 17:30 en invierno (dentro).
    v.check(
        mensaje_derivacion_ejecutiva(datetime(*JUEVES, 21, 30, tzinfo=UTC)) == MENSAJE_FUERA
        and mensaje_derivacion_ejecutiva(datetime(*MIERCOLES_INVIERNO, 21, 30, tzinfo=UTC)) == MENSAJE_DENTRO,
        "21:30 UTC: fuera en verano (18:30), dentro en invierno (17:30)",
    )


async def caso_h(v: Verificador) -> None:
    v.check(
        mensaje_derivacion_ejecutiva(_chile(JUEVES, 10, 0))
        == "¡Hola! Soy Lea, tu asistente virtual. Gracias por escribirnos. Una ejecutiva revisará tu mensaje y te contactará a la brevedad.",
        "texto dentro de horario exacto",
    )
    v.check(
        mensaje_derivacion_ejecutiva(_chile(JUEVES, 20, 0))
        == "¡Hola! Soy Lea, tu asistente virtual. Gracias por escribirnos. Nuestro horario de atención es de 09:00 a 18:00 hrs. Una ejecutiva se pondrá en contacto contigo dentro del horario de atención.",
        "texto fuera de horario exacto",
    )
    with _config(HORARIO_ATENCION_INICIO="08:30", HORARIO_ATENCION_FIN="17:00"):
        fuera = mensaje_derivacion_ejecutiva(_chile(JUEVES, 17, 0))
        v.check("de 08:30 a 17:00 hrs" in fuera and "09:00" not in fuera, "horario configurado 08:30-17:00 se refleja en el texto")
        v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 8, 30)) == MENSAJE_DENTRO, "08:30 con inicio 08:30 -> dentro")
        v.check(mensaje_derivacion_ejecutiva(_chile(JUEVES, 8, 29)) != MENSAJE_DENTRO, "08:29 con inicio 08:30 -> fuera")
    with _config(DIAS_ATENCION="lunes,miercoles,viernes"):
        fuera = mensaje_derivacion_ejecutiva(_chile(JUEVES, 11, 0))
        v.check("es lunes, miércoles y viernes, de 09:00 a 18:00 hrs." in fuera, f"días sueltos: {fuera}")
    with _config(NOMBRE_ASISTENTE="Ana"):
        v.check(
            mensaje_derivacion_ejecutiva(_chile(JUEVES, 10, 0)).startswith("¡Hola! Soy Ana, tu asistente virtual."),
            "NOMBRE_ASISTENTE configurado se refleja en el texto",
        )
    v.check(
        marcar_notificacion_ejecutiva("Aviso", _chile(JUEVES, 10, 0)) == "Aviso"
        and marcar_notificacion_ejecutiva("Aviso", _chile(JUEVES, 20, 0)) == "Aviso (fuera de horario)",
        "marca '(fuera de horario)' solo fuera de horario",
    )


# --------------------------------------------------------------------------
# Casos: flujos completos
# --------------------------------------------------------------------------


async def caso_flujo_espontaneo(v: Verificador) -> None:
    phone = TELEFONO_ESPONTANEO
    notificacion = MENSAJE_NOTIFICACION_EJECUTIVA.format(telefono=phone)
    for etiqueta, ahora, esperado, notif_esperada in (
        ("dentro (jueves 10:00)", _chile(JUEVES, 10, 0), MENSAJE_DENTRO, notificacion),
        ("fuera (jueves 21:00)", _chile(JUEVES, 21, 0), MENSAJE_FUERA, f"{notificacion} (fuera de horario)"),
    ):
        v.check(not await esta_activa(phone), f"{etiqueta}: no hay conversación activa")
        enviados = []
        with _fake_send(enviados):
            await whatsapp_route._procesar_mensaje_entrante(_mensaje_texto(phone, "hola, necesito agua"), ahora)
        v.check(len(enviados) == 2, f"{etiqueta}: se envían 2 mensajes (cliente y ejecutiva)")
        v.check(enviados[0] == (phone, esperado), f"{etiqueta}: el cliente recibe el mensaje correcto")
        v.check(
            enviados[1] == (settings.EJECUTIVA_PHONE, notif_esperada),
            f"{etiqueta}: la ejecutiva recibe la notificación"
            + (" con '(fuera de horario)'" if "fuera" in etiqueta else " sin la marca"),
        )
        v.check(get_draft(phone) is None, f"{etiqueta}: el bot no procesa el mensaje (sin draft)")


async def caso_flujo_duplicado(v: Verificador) -> None:
    """Teléfono en más de un cliente: mismo mensaje de derivación (según
    horario) y marca en la notificación a la ejecutiva."""
    phone = TELEFONO_DUPLICADO
    clientes = [SimpleNamespace(id=1001), SimpleNamespace(id=1002)]
    datetime_original = order_flow.datetime
    for etiqueta, ahora, esperado in (
        ("dentro (jueves 10:00)", _chile(JUEVES, 10, 0), MENSAJE_DENTRO),
        ("fuera (domingo 07:00)", _chile(DOMINGO, 7, 0), MENSAJE_FUERA),
    ):

        class RelojFijo(datetime):
            @classmethod
            def now(cls, tz=None):
                return ahora if tz is None else ahora.astimezone(tz)

        enviados = []
        order_flow.datetime = RelojFijo
        try:
            with _fake_send(enviados):
                respuesta = await order_flow._escalar_telefono_duplicado(phone, clientes)
        finally:
            order_flow.datetime = datetime_original
        v.check(respuesta == esperado, f"{etiqueta}: el cliente recibe el mensaje de derivación correcto")
        notif = enviados[0][1] if enviados else ""
        v.check(
            len(enviados) == 1 and enviados[0][0] == settings.EJECUTIVA_PHONE and "1001, 1002" in notif,
            f"{etiqueta}: notifica a la ejecutiva",
        )
        v.check(
            notif.endswith("(fuera de horario)") == ("fuera" in etiqueta),
            f"{etiqueta}: marca '(fuera de horario)' solo fuera de horario",
        )


async def caso_i(v: Verificador) -> None:
    """Conversación activa fuera de horario: el agente toma el pedido."""
    phone = TELEFONO_ACTIVO
    await marcar_activa(phone)
    ahora = _chile(JUEVES, 23, 30)
    enviados = []
    with _fake_send(enviados):
        await whatsapp_route._procesar_mensaje_entrante(
            _mensaje_texto(phone, "Hola, quiero 2 bidones de 20 litros recarga"), ahora
        )
    v.check(len(enviados) == 1 and enviados[0][0] == phone, "responde solo al cliente (no notifica a la ejecutiva)")
    respuesta = enviados[0][1] if enviados else ""
    v.check("ejecutiva" not in respuesta.lower() and "horario" not in respuesta.lower(), "no manda el mensaje de derivación")
    draft = get_draft(phone) or {}
    v.check(
        any(p.get("nombre_producto") == "Bidón 20L Recarga" and p.get("cantidad") == 2 for p in draft.get("productos") or []),
        "el pedido queda en curso (2x Bidón 20L Recarga en el draft)",
    )
    v.check(await esta_activa(phone), "la conversación sigue activa")


async def caso_j(v: Verificador) -> None:
    """La presentación de Lea aparece una sola vez en la conversación activa
    (sigue la conversación del caso i)."""
    phone = TELEFONO_ACTIVO
    previos = await _ejecutar(
        "select contenido from mensaje_whatsapp where telefono = :t and direccion = 'entrante' order by id", t=phone
    )
    v.check(len(previos.all()) == 1, "el caso i dejó un mensaje entrante registrado")
    enviados = []
    ahora = _chile(JUEVES, 23, 35)
    with _fake_send(enviados):
        for texto in ("Santa Maria 793, Viña del Mar", "Juan Pérez"):
            await whatsapp_route._procesar_mensaje_entrante(_mensaje_texto(phone, texto), ahora)
    primera = await _ejecutar(
        "select count(*) from mensaje_whatsapp where telefono = :t and direccion = 'entrante'", t=phone
    )
    v.check(primera.scalar_one() == 3, "hay 3 mensajes entrantes en la conversación")
    v.check(all("Lea" not in mensaje for _, mensaje in enviados), "las respuestas siguientes no repiten la presentación")
    clear_draft(phone)

    # Funciones puras: inserción natural y sin saludos duplicados.
    presentar = whatsapp_route._presentar_asistente
    casos = [
        ("¡Hola, Felipe! ¿Qué producto quieres?", "¡Hola, Felipe! Soy Lea, tu asistente virtual. ¿Qué producto quieres?"),
        ("¡Hola! ¿Qué producto quieres?", "¡Hola! Soy Lea, tu asistente virtual. ¿Qué producto quieres?"),
        ("Hola Felipe, ¿qué producto quieres?", "¡Hola! Soy Lea, tu asistente virtual. ¿Qué producto quieres?"),
        ("Perfecto, anoté 2 bidones.", "¡Hola! Soy Lea, tu asistente virtual. Perfecto, anoté 2 bidones."),
        ("¡Hola, Leandro! ¿Qué quieres?", "¡Hola, Leandro! Soy Lea, tu asistente virtual. ¿Qué quieres?"),
        (MENSAJE_DENTRO, MENSAJE_DENTRO),
    ]
    for entrada, esperado in casos:
        salida = presentar(entrada)
        v.check(salida == esperado and salida.lower().count("hola") == 1, f"{entrada!r} -> {salida!r}")


async def caso_j_primer_mensaje(v: Verificador) -> None:
    """Primer mensaje de una conversación activa nueva: lleva la
    presentación una sola vez (se reactiva la conversación del caso i)."""
    phone = TELEFONO_ACTIVO
    await _ejecutar("delete from mensaje_whatsapp where telefono = :t", t=phone)
    await marcar_activa(phone)
    clear_draft(phone)
    enviados = []
    with _fake_send(enviados):
        for texto in ("Hola, quiero 1 bidón de 20 litros recarga", "no, nada más"):
            await whatsapp_route._procesar_mensaje_entrante(_mensaje_texto(phone, texto), _chile(JUEVES, 12, 0))
    respuestas = [mensaje for to, mensaje in enviados if to == phone]
    v.check(len(respuestas) == 2, "2 respuestas al cliente")
    v.check(
        bool(respuestas) and "Soy Lea, tu asistente virtual." in respuestas[0] and respuestas[0].lower().count("hola") == 1,
        "la primera respuesta presenta a Lea, con un solo saludo",
    )
    v.check(all("Lea" not in r for r in respuestas[1:]), "la segunda respuesta no repite la presentación")
    v.check(
        sum(r.count("Soy Lea") for r in respuestas) == 1,
        "la presentación aparece exactamente una vez en la conversación",
    )
    clear_draft(phone)


CASOS = [
    ("a", "08:59 -> fuera de horario", caso_a),
    ("b", "09:00 exacto -> dentro de horario", caso_b),
    ("c", "17:59 -> dentro de horario", caso_c),
    ("d", "18:00 exacto -> fuera de horario", caso_d),
    ("e", "23:30 y 03:00 -> fuera de horario", caso_e),
    ("f", "Sábado y domingo: por defecto dentro, con lunes-viernes fuera", caso_f),
    ("g", "Horario de verano e invierno en America/Santiago", caso_g),
    ("h", "Textos exactos y configurables, marca de la notificación", caso_h),
    ("h2", "Flujo mensaje espontáneo dentro y fuera de horario", caso_flujo_espontaneo),
    ("h3", "Flujo teléfono duplicado dentro y fuera de horario", caso_flujo_duplicado),
    ("i", "Conversación activa fuera de horario sigue tomando pedidos", caso_i),
    ("j", "Presentación de Lea: no se repite en los mensajes siguientes", caso_j),
    ("j2", "Presentación de Lea: una sola vez en una conversación nueva", caso_j_primer_mensaje),
]


async def main() -> None:
    await _limpiar_rango()
    resultados = []
    try:
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
