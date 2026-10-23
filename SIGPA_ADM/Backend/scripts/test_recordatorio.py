"""
Script temporal para validar el recordatorio diario (POST
/internal/recordatorio-diario en app/api/routes/internal.py) y el envío de
plantillas (send_whatsapp_template en app/services/whatsapp_client.py).
No es parte del código final: solo para validar manualmente el comportamiento.

La hora siempre se inyecta parcheando internal._ahora_chile (nunca depende del
reloj real) y la configuración (INTERNAL_CRON_SECRET, plantilla, teléfonos de
prueba) se cambia en settings durante cada caso y se restaura al terminarlo.

Datos de prueba: todos los teléfonos usados están en el rango 569333003xx.
Al empezar, entre casos y al terminar, el script BORRA de ese rango los
clientes, los mensajes de mensaje_whatsapp y las filas de conversacion_bot.
No toca ningún otro cliente.

Nunca envía mensajes reales: en el endpoint send_whatsapp_template se
reemplaza por un fake que solo "envía" a teléfonos del rango de prueba (a
cualquier otro, p. ej. un cliente real con reparto mañana, responde None, así
el endpoint nunca marca su conversación), y marcar_activa se envuelve para
fallar si recibe un teléfono fuera del rango. En el caso a, httpx.AsyncClient
se reemplaza por un fake.

Uso: python -m scripts.test_recordatorio
"""

import asyncio
import json
import re
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

import app.api.routes.internal as internal
import app.services.whatsapp_client as whatsapp_client
from app.core.config import Settings, settings
from app.core.database import SessionLocal
from app.models.cliente import Cliente
from app.models.enums import DiaSemana
from app.services.cliente_lookup import normalizar_telefono

CHILE = ZoneInfo("America/Santiago")

RANGO_TELEFONOS_SQL = r"^(56)?9333003\d{2}$"
RANGO_TELEFONOS = re.compile(RANGO_TELEFONOS_SQL)
SECRETO = "secreto-test-recordatorio"
# Idioma por defecto de la clase Settings (no el del .env local).
LANG_POR_DEFECTO = Settings.model_fields["WHATSAPP_TEMPLATE_RECORDATORIO_LANG"].default
PLANTILLA = "recordatorio_reparto_test"

TEL_JUEVES = "56933300301"
TEL_LUNES = "56933300302"
TEL_OPT_OUT = "56933300303"
TEL_INACTIVO = "56933300304"
TEL_NOMBRE_VACIO = "56933300305"
TEL_NOMBRE_MULTILINEA = "56933300306"
TEL_FALLA = "56933300307"
# Guardado con formato distinto para probar la normalización del modo prueba.
TEL_SIN_DIA_GUARDADO = "+56 9 3330 0308"
TEL_SIN_DIA = "56933300308"
TEL_PAYLOAD = "56933300399"

# Miércoles 21/10/2026 (mañana jueves), viernes 23 (mañana sábado) y
# sábado 24 (mañana domingo), siempre a las 15:05 hora Chile.
MIERCOLES_15 = datetime(2026, 10, 21, 15, 5, tzinfo=CHILE)
MIERCOLES_16 = datetime(2026, 10, 21, 16, 5, tzinfo=CHILE)
MIERCOLES_14 = datetime(2026, 10, 21, 14, 59, tzinfo=CHILE)
VIERNES_15 = datetime(2026, 10, 23, 15, 5, tzinfo=CHILE)
VIERNES_10 = datetime(2026, 10, 23, 10, 0, tzinfo=CHILE)
SABADO_15 = datetime(2026, 10, 24, 15, 5, tzinfo=CHILE)


def _texto(nombre: str) -> str:
    return internal._texto_recordatorio(nombre)


def _es_de_prueba(telefono: str | None) -> bool:
    return bool(RANGO_TELEFONOS.match(normalizar_telefono(telefono)))


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


def _config_base(telefonos_prueba: str = "", plantilla: str = PLANTILLA):
    return _config(
        INTERNAL_CRON_SECRET=SECRETO,
        WHATSAPP_TEMPLATE_RECORDATORIO_NAME=plantilla,
        WHATSAPP_TEMPLATE_RECORDATORIO_LANG=LANG_POR_DEFECTO,
        RECORDATORIO_TELEFONOS_PRUEBA=telefonos_prueba,
    )


@contextmanager
def _hora(ahora: datetime):
    original = internal._ahora_chile
    internal._ahora_chile = lambda: ahora
    try:
        yield
    finally:
        internal._ahora_chile = original


class FakeEnvio:
    """Reemplaza send_whatsapp_template y marcar_activa en el endpoint."""

    def __init__(self, fallan: set[str] | None = None) -> None:
        self.envios: list[dict] = []
        self.fallan = fallan or set()

    async def send(self, to, template_name, language_code, body_params, texto_registro):
        if not _es_de_prueba(to):
            # Cliente real con reparto mañana: no se "envía" ni se marca.
            return None
        self.envios.append(
            {
                "to": to,
                "template_name": template_name,
                "language_code": language_code,
                "body_params": body_params,
                "texto_registro": texto_registro,
            }
        )
        if to in self.fallan:
            return None
        return {"messages": [{"id": f"wamid.fake-{len(self.envios)}"}]}

    def a(self, telefono: str) -> list[dict]:
        return [e for e in self.envios if e["to"] == telefono]


@contextmanager
def _fake_envio(fake: FakeEnvio):
    marcar_original = internal.marcar_activa

    async def marcar_guardado(phone, ahora=None):
        if not _es_de_prueba(phone):
            raise RuntimeError(f"marcar_activa con teléfono fuera del rango de prueba: {phone}")
        await marcar_original(phone, ahora)

    send_original = internal.send_whatsapp_template
    internal.send_whatsapp_template = fake.send
    internal.marcar_activa = marcar_guardado
    try:
        yield
    finally:
        internal.send_whatsapp_template = send_original
        internal.marcar_activa = marcar_original


async def _llamar(forzar: bool = False):
    resultado = await internal.recordatorio_diario(x_cron_secret=SECRETO, forzar=forzar)
    if hasattr(resultado, "body"):
        return resultado.status_code, json.loads(resultado.body)
    return 200, resultado


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar_estado() -> None:
    await _ejecutar(
        "delete from mensaje_whatsapp where regexp_replace(telefono, '\\D', '', 'g') ~ :rango",
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(
        "delete from conversacion_bot where regexp_replace(telefono, '\\D', '', 'g') ~ :rango",
        rango=RANGO_TELEFONOS_SQL,
    )


async def _limpiar_rango() -> None:
    ids_sql = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    await _ejecutar(
        f"delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id in ({ids_sql}))",
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from mensaje_whatsapp where cliente_id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _limpiar_estado()


async def _crear_clientes() -> None:
    datos = [
        ("Ana", TEL_JUEVES, DiaSemana.JUEVES, True, False),
        ("Luis", TEL_LUNES, DiaSemana.LUNES, True, False),
        ("Olga", TEL_OPT_OUT, DiaSemana.JUEVES, True, True),
        ("Ines", TEL_INACTIVO, DiaSemana.JUEVES, False, False),
        ("", TEL_NOMBRE_VACIO, DiaSemana.JUEVES, True, False),
        ("  Juan\n  Pérez \r\n", TEL_NOMBRE_MULTILINEA, DiaSemana.JUEVES, True, False),
        ("Fabio", TEL_FALLA, DiaSemana.JUEVES, True, False),
        ("Sofia", TEL_SIN_DIA_GUARDADO, None, True, False),
    ]
    async with SessionLocal() as session:
        for nombre, telefono, dia, activo, opt_out in datos:
            session.add(
                Cliente(
                    nombre=nombre,
                    telefono=telefono,
                    dia_reparto=dia,
                    activo=activo,
                    opt_out_whatsapp=opt_out,
                )
            )
        await session.commit()


async def _conversacion(telefono: str):
    result = await _ejecutar(
        "select activa, iniciada_en from conversacion_bot where telefono = :t", t=telefono
    )
    return result.first()


async def _mensajes(telefono: str):
    result = await _ejecutar(
        "select tipo, contenido, estado, meta_message_id, direccion from mensaje_whatsapp "
        "where telefono = :t order by id",
        t=telefono,
    )
    return result.all()


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


# --------------------------------------------------------------------------
# Caso a: payload de send_whatsapp_template (httpx parcheado)
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@contextmanager
def _fake_httpx(respuestas: list[FakeResponse], llamadas: list[dict]):
    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            llamadas.append({"url": url, "headers": headers, "json": json})
            return respuestas.pop(0)

    original = whatsapp_client.httpx.AsyncClient
    whatsapp_client.httpx.AsyncClient = FakeAsyncClient
    try:
        yield
    finally:
        whatsapp_client.httpx.AsyncClient = original


async def caso_a(v: Verificador) -> None:
    llamadas: list[dict] = []
    exito = {"messaging_product": "whatsapp", "messages": [{"id": "wamid.payload-ok"}]}
    error = {"error": {"code": 132001, "message": "Template name does not exist in the translation"}}
    respuestas = [FakeResponse(200, exito), FakeResponse(200, exito), FakeResponse(400, error)]

    with _fake_httpx(respuestas, llamadas):
        r1 = await whatsapp_client.send_whatsapp_template(
            to=TEL_PAYLOAD,
            template_name="mi_plantilla",
            language_code="es_CL",
            body_params=["Ana", "Mañana"],
            texto_registro="texto renderizado 1",
        )
        r2 = await whatsapp_client.send_whatsapp_template(
            to=TEL_PAYLOAD,
            template_name="sin_parametros",
            language_code="es_CL",
            body_params=[],
            texto_registro="texto renderizado 2",
        )
        r3 = await whatsapp_client.send_whatsapp_template(
            to=TEL_PAYLOAD,
            template_name="no_existe",
            language_code="es_CL",
            body_params=["Ana"],
            texto_registro="texto renderizado 3",
        )

    esperado_1 = {
        "messaging_product": "whatsapp",
        "to": TEL_PAYLOAD,
        "type": "template",
        "template": {
            "name": "mi_plantilla",
            "language": {"code": "es_CL"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": "Ana"}, {"type": "text", "text": "Mañana"}],
                }
            ],
        },
    }
    esperado_2 = {
        "messaging_product": "whatsapp",
        "to": TEL_PAYLOAD,
        "type": "template",
        "template": {"name": "sin_parametros", "language": {"code": "es_CL"}},
    }
    print(f"    payload con parámetros: {json.dumps(llamadas[0]['json'], ensure_ascii=False)}")
    v.check(len(llamadas) == 3, "3 POST a Meta (fake)")
    v.check(llamadas[0]["json"] == esperado_1, "payload exacto con template/language/components")
    v.check(llamadas[1]["json"] == esperado_2, "sin body_params se omite components")
    v.check(llamadas[0]["url"].endswith("/messages"), "POST al endpoint /messages")
    v.check(r1 == exito and r2 == exito, "éxito (200) devuelve el body de Meta")
    v.check(r3 is None, "status != 200 devuelve None")

    mensajes = await _mensajes(TEL_PAYLOAD)
    v.check(len(mensajes) == 3, "3 filas en mensaje_whatsapp")
    if len(mensajes) == 3:
        v.check(
            tuple(mensajes[0]) == ("template", "texto renderizado 1", "enviado", "wamid.payload-ok", "saliente"),
            "éxito registrado: tipo template, texto renderizado, enviado, wamid",
        )
        v.check(
            tuple(mensajes[2]) == ("template", "texto renderizado 3", "fallido", None, "saliente"),
            "error registrado como fallido y sin wamid",
        )

    # Endpoint completo (send_whatsapp_template real, httpx falso) con el
    # idioma por defecto de Settings: lo que llega a Meta y lo que se registra.
    v.check(LANG_POR_DEFECTO == "es_CL", "idioma por defecto de la config = es_CL")
    llamadas.clear()
    exito_ana = {"messaging_product": "whatsapp", "messages": [{"id": "wamid.recordatorio-ana"}]}
    with _config_base(telefonos_prueba=TEL_JUEVES), _hora(MIERCOLES_15), _fake_envio(FakeEnvio()):
        internal.send_whatsapp_template = whatsapp_client.send_whatsapp_template
        with _fake_httpx([FakeResponse(200, exito_ana)], llamadas):
            _, r = await _llamar()
    v.check(r["telefonos_enviados"] == [TEL_JUEVES] and len(llamadas) == 1, "endpoint envía 1 plantilla a Meta")
    if llamadas:
        template = llamadas[0]["json"]["template"]
        v.check(
            template["name"] == PLANTILLA and template["language"] == {"code": "es_CL"},
            "language.code enviado a Meta = es_CL",
        )
        v.check(
            template["components"][0]["parameters"] == [{"type": "text", "text": "Ana"}],
            "parámetro {{1}} = nombre del cliente",
        )
    registrado = await _mensajes(TEL_JUEVES)
    v.check(
        len(registrado) == 1
        and registrado[0].contenido == _texto("Ana")
        and registrado[0].contenido
        == "Buenas tardes, Ana. Mañana corresponde el reparto programado en su sector. "
        "Por favor, indíquenos si requiere recarga para esta visita. Gracias."
        and registrado[0].estado == "enviado",
        "mensaje_whatsapp registra el texto nuevo renderizado",
    )


# --------------------------------------------------------------------------
# Casos del endpoint
# --------------------------------------------------------------------------


async def caso_b(v: Verificador) -> None:
    fake = FakeEnvio()
    with _config_base(), _hora(MIERCOLES_15), _fake_envio(fake):
        status, r = await _llamar()
    print(f"    respuesta (telefonos de prueba): enviados={[t for t in r['telefonos_enviados'] if _es_de_prueba(t)]}")
    v.check(status == 200 and r["modo_prueba"] is False and r["forzar"] is False, "modo_prueba false, forzar false")
    v.check(r["dia_manana"] == "Jueves", "dia_manana = Jueves")
    envio = fake.a(TEL_JUEVES)
    v.check(
        len(envio) == 1
        and envio[0]["template_name"] == PLANTILLA
        and envio[0]["language_code"] == "es_CL"
        and envio[0]["body_params"] == ["Ana"]
        and envio[0]["texto_registro"] == _texto("Ana"),
        "cliente con reparto mañana recibe la plantilla con su nombre",
    )
    v.check(TEL_JUEVES in r["telefonos_enviados"], "aparece en telefonos_enviados")
    v.check(not fake.a(TEL_LUNES), "cliente con otro día no recibe")
    v.check(not fake.a(TEL_OPT_OUT), "cliente con opt_out no recibe")
    v.check(not fake.a(TEL_INACTIVO), "cliente inactivo no recibe")
    v.check(not fake.a(TEL_SIN_DIA), "cliente sin día de reparto no recibe")
    conv = await _conversacion(TEL_JUEVES)
    v.check(conv is not None and conv.activa, "conversación marcada activa tras envío exitoso")
    for clave in ("clientes_evaluados", "mensajes_enviados", "ya_enviados_hoy", "telefonos_ya_enviados_hoy", "errores"):
        v.check(clave in r, f"respuesta mantiene '{clave}'")


async def caso_c(v: Verificador) -> None:
    for hora in (MIERCOLES_14, MIERCOLES_16):
        fake = FakeEnvio()
        with _config_base(), _hora(hora), _fake_envio(fake):
            status, r = await _llamar()
        v.check(
            status == 200 and r.get("status") == "skipped" and not fake.envios,
            f"{hora.strftime('%H:%M')} -> skipped, sin envíos",
        )


async def caso_d(v: Verificador) -> None:
    fake = FakeEnvio()
    with _config_base(), _hora(MIERCOLES_15), _fake_envio(fake):
        _, r1 = await _llamar()
        with _hora(MIERCOLES_15.replace(minute=45)):
            _, r2 = await _llamar()
    v.check(TEL_JUEVES in r1["telefonos_enviados"], "primera ejecución envía")
    v.check(len(fake.a(TEL_JUEVES)) == 1, "un solo envío en el día")
    v.check(
        TEL_JUEVES in r2["telefonos_ya_enviados_hoy"] and TEL_JUEVES not in r2["telefonos_enviados"],
        "segunda ejecución lo cuenta en ya_enviados_hoy",
    )
    v.check(r2["ya_enviados_hoy"] >= 1, "ya_enviados_hoy >= 1")


async def caso_e(v: Verificador) -> None:
    fake = FakeEnvio(fallan={TEL_FALLA})
    with _config_base(), _hora(MIERCOLES_15), _fake_envio(fake):
        _, r1 = await _llamar()
    errores = [e for e in r1["errores"] if e.get("telefono") == TEL_FALLA]
    v.check(len(errores) == 1 and errores[0].get("error"), "teléfono fallido va a errores con motivo")
    v.check(TEL_FALLA not in r1["telefonos_enviados"], "no se cuenta como enviado")
    v.check(await _conversacion(TEL_FALLA) is None, "conversación NO marcada")

    fake.fallan = set()
    with _config_base(), _hora(MIERCOLES_15.replace(minute=30)), _fake_envio(fake):
        _, r2 = await _llamar()
    v.check(len(fake.a(TEL_FALLA)) == 2, "la segunda ejecución lo reintenta")
    v.check(TEL_FALLA in r2["telefonos_enviados"], "reintento exitoso queda en telefonos_enviados")
    conv = await _conversacion(TEL_FALLA)
    v.check(conv is not None and conv.activa, "tras el reintento exitoso la conversación queda activa")


async def caso_f(v: Verificador) -> None:
    lista = f"933300302, {TEL_SIN_DIA_GUARDADO}, {TEL_OPT_OUT}, {TEL_INACTIVO}"
    for ahora, dia in ((VIERNES_15, "Sabado"), (SABADO_15, "Domingo")):
        await _limpiar_estado()
        fake = FakeEnvio()
        with _config_base(telefonos_prueba=lista), _hora(ahora), _fake_envio(fake):
            _, r = await _llamar()
        print(f"    {dia}: telefonos_enviados={r['telefonos_enviados']}")
        v.check(r["modo_prueba"] is True, f"mañana {dia}: modo_prueba true")
        v.check(r["dia_manana"] == dia, f"dia_manana = {dia}")
        v.check(
            sorted(e["to"] for e in fake.envios) == sorted([TEL_LUNES, TEL_SIN_DIA_GUARDADO]),
            f"mañana {dia}: solo los de la lista (día de reparto ignorado, formato normalizado)",
        )
        v.check(r["clientes_evaluados"] == 2, "opt_out e inactivo de la lista se excluyen")
        v.check(not fake.a(TEL_JUEVES), "cliente fuera de la lista no recibe")


async def caso_g(v: Verificador) -> None:
    lista = TEL_LUNES
    fake = FakeEnvio()
    with _config_base(telefonos_prueba=lista), _fake_envio(fake):
        with _hora(VIERNES_10):
            _, r0 = await _llamar()
            _, r1 = await _llamar(forzar=True)
            _, r2 = await _llamar(forzar=True)
    v.check(r0.get("status") == "skipped", "sin forzar, a las 10:00 -> skipped")
    v.check(r1["forzar"] is True and TEL_LUNES in r1["telefonos_enviados"], "forzar salta la ventana horaria")
    v.check(
        TEL_LUNES in r2["telefonos_enviados"] and len(fake.a(TEL_LUNES)) == 2,
        "forzar salta la idempotencia (reenvía el mismo día)",
    )

    fake = FakeEnvio()
    with _config_base(), _hora(MIERCOLES_15), _fake_envio(fake):
        try:
            await _llamar(forzar=True)
            v.check(False, "sin modo prueba forzar responde 403")
        except HTTPException as exc:
            print(f"    403: {exc.detail}")
            v.check(exc.status_code == 403, "sin modo prueba forzar responde 403")
    v.check(not fake.envios, "sin modo prueba con forzar no envía nada")


async def caso_h(v: Verificador) -> None:
    llamadas: list[dict] = []
    fake = FakeEnvio()
    for lista in ("", TEL_LUNES):
        with _config_base(telefonos_prueba=lista, plantilla="  "), _hora(MIERCOLES_15), _fake_envio(fake):
            with _fake_httpx([], llamadas):
                status, r = await _llamar()
        print(f"    respuesta: {status} {r}")
        v.check(r.get("error") == "plantilla_no_configurada", f"error plantilla_no_configurada (lista='{lista}')")
        v.check(status == 500, "HTTP 500 para que el job del cron falle")
    v.check(not fake.envios and not llamadas, "no se llama a Meta")
    v.check(await _conversacion(TEL_JUEVES) is None, "no se marca ninguna conversación")


async def caso_i(v: Verificador) -> None:
    v.check(internal._nombre_para_plantilla(None) == "cliente", "None -> 'cliente'")
    v.check(internal._nombre_para_plantilla("  \n ") == "cliente", "solo espacios -> 'cliente'")
    fake = FakeEnvio()
    with _config_base(), _hora(MIERCOLES_15), _fake_envio(fake):
        await _llamar()
    vacio = fake.a(TEL_NOMBRE_VACIO)
    v.check(
        len(vacio) == 1 and vacio[0]["body_params"] == ["cliente"] and vacio[0]["texto_registro"] == _texto("cliente"),
        "nombre vacío en BD -> parámetro 'cliente'",
    )
    multi = fake.a(TEL_NOMBRE_MULTILINEA)
    v.check(
        len(multi) == 1 and multi[0]["body_params"] == ["Juan Pérez"],
        "nombre con saltos de línea queda en una sola línea ('Juan Pérez')",
    )
    v.check(
        bool(multi) and multi[0]["texto_registro"] == "Buenas tardes, Juan Pérez. Mañana corresponde el "
        "reparto programado en su sector. Por favor, indíquenos si requiere recarga para esta visita. Gracias.",
        "texto_registro = cuerpo de la plantilla renderizado",
    )


CASOS = [
    ("a", "Payload de send_whatsapp_template y registro", caso_a),
    ("b", "Producción: filtro por día de reparto, activo y opt-out", caso_b),
    ("c", "Fuera de la ventana de las 15:00 -> skipped", caso_c),
    ("d", "Idempotencia: segunda ejecución del día no reenvía", caso_d),
    ("e", "Fallo de Meta: va a errores, no se marca, se reintenta", caso_e),
    ("f", "Modo prueba: solo la lista, ignora día (viernes y sábado)", caso_f),
    ("g", "forzar: con modo prueba salta hora e idempotencia; sin él 403", caso_g),
    ("h", "Plantilla sin configurar -> plantilla_no_configurada", caso_h),
    ("i", "Nombre vacío/None -> 'cliente'; multilínea -> una línea", caso_i),
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
            await _limpiar_estado()
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
    print(f"  {sum(ok for *_, ok in resultados)} de {len(resultados)} OK")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
