import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models.cliente import Cliente
from app.models.conversacion_bot import ConversacionBot
from app.models.enums import DiaSemana
from app.services.cliente_lookup import normalizar_telefono
from app.services.conversacion_bot_service import marcar_activa
from app.services.whatsapp_client import send_whatsapp_template

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

ZONA_HORARIA_CHILE = ZoneInfo("America/Santiago")
UTC = ZoneInfo("UTC")

# Clave del advisory lock de Postgres que serializa las ejecuciones del
# recordatorio (ver _candado_recordatorio). Valor arbitrario pero fijo.
CLAVE_CANDADO_RECORDATORIO = 7_150_615

# Cuerpo de la plantilla Utility aprobada por Meta
# (confirmacion_reparto_programado, es_CL; {{1}} = nombre del cliente). Solo
# se usa para el historial de mensaje_whatsapp: a Meta se le envía el nombre
# de la plantilla y el parámetro, no este texto. Ver _texto_recordatorio.
TEXTO_RECORDATORIO = (
    "Buenas tardes, {{1}}. Mañana corresponde el reparto programado en su sector. "
    "Por favor, indíquenos si requiere recarga para esta visita. Gracias."
)
NOMBRE_POR_DEFECTO = "cliente"

# Índice = date.weekday() (0=Lunes...6=Domingo). Los dos últimos no existen
# como valor del enum DiaSemana (solo hay reparto Lunes-Viernes), se usan
# solo para mostrar el nombre del día en la respuesta.
_NOMBRES_DIA_SEMANA = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]


def _ahora_chile() -> datetime:
    """Hora actual en Chile. Función aparte para que los tests la parcheen."""
    return datetime.now(ZONA_HORARIA_CHILE)


@asynccontextmanager
async def _candado_recordatorio():
    """Candado de Postgres (advisory lock de sesión) para que dos ejecuciones
    del recordatorio no corran a la vez: sin él, ambas leerían "no enviado
    hoy" para el mismo teléfono antes de que la otra llame a marcar_activa y
    le mandarían la plantilla dos veces. Entrega True si lo obtuvo y False si
    otra ejecución lo tiene (la que llega después no espera: se salta y el
    cron siguiente la reemplaza). Se pide en una conexión en AUTOCOMMIT que se
    mantiene abierta toda la ejecución. Si la conexión muere, Postgres suelta
    el candado solo."""
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        obtenido = bool(
            (await conn.execute(select(func.pg_try_advisory_lock(CLAVE_CANDADO_RECORDATORIO)))).scalar()
        )
        try:
            yield obtenido
        finally:
            if obtenido:
                try:
                    await conn.execute(select(func.pg_advisory_unlock(CLAVE_CANDADO_RECORDATORIO)))
                except Exception:
                    # Que la conexión no vuelva al pool con el candado puesto.
                    logger.exception("[recordatorio-diario] No se pudo liberar el candado")
                    await conn.invalidate()


def _dia_semana_manana(hoy_chile: date) -> tuple[str, DiaSemana | None]:
    manana = hoy_chile + timedelta(days=1)
    nombre = _NOMBRES_DIA_SEMANA[manana.weekday()]
    try:
        return nombre, DiaSemana(nombre)
    except ValueError:
        return nombre, None


def _fecha_chile(fecha: datetime) -> date:
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return fecha.astimezone(ZONA_HORARIA_CHILE).date()


def _nombre_para_plantilla(nombre: str | None) -> str:
    # Meta rechaza parámetros con saltos de línea, tabs o más de 4 espacios
    # seguidos: se deja todo en una sola línea con espacios simples.
    return " ".join((nombre or "").split()) or NOMBRE_POR_DEFECTO


def _texto_recordatorio(nombre: str) -> str:
    return TEXTO_RECORDATORIO.replace("{{1}}", nombre)


@router.post("/recordatorio-diario")
async def recordatorio_diario(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    forzar: bool = Query(default=False),
):
    if not settings.INTERNAL_CRON_SECRET or x_cron_secret != settings.INTERNAL_CRON_SECRET:
        raise HTTPException(status_code=401, detail="No autorizado")

    telefonos_prueba = settings.recordatorio_telefonos_prueba_list
    modo_prueba = bool(telefonos_prueba)
    # Solo tiene efecto en modo prueba; en producción el día siempre se respeta.
    prueba_respeta_dia = modo_prueba and settings.RECORDATORIO_PRUEBA_RESPETAR_DIA

    # forzar salta la ventana horaria y la idempotencia: en producción nunca
    # debe poder usarse (duplicaría envíos a clientes reales).
    if forzar and not modo_prueba:
        raise HTTPException(
            status_code=403,
            detail="forzar=true solo está permitido en modo prueba (RECORDATORIO_TELEFONOS_PRUEBA)",
        )

    ahora_chile = _ahora_chile()
    hora_inicio = settings.RECORDATORIO_HORA_INICIO
    hora_fin = settings.RECORDATORIO_HORA_FIN
    if not forzar and not hora_inicio <= ahora_chile.hour < hora_fin:
        # Ventana horaria en hora LOCAL de Chile: al comparar contra zoneinfo
        # (no contra UTC) el chequeo es inmune a los cambios de horario de
        # verano, sin ajuste manual del cron. Es una ventana y no una hora
        # exacta porque GitHub retrasa y se salta ejecuciones programadas.
        # Que se invoque varias veces dentro de ella no duplica envíos: la
        # idempotencia por fecha (ver telefonos_ya_enviados_hoy más abajo)
        # deja una sola plantilla por teléfono al día.
        return {
            "status": "skipped",
            "razon": "fuera de la ventana horaria",
            "ventana": f"{hora_inicio:02d}:00-{hora_fin:02d}:00",
            "hora_actual_chile": ahora_chile.isoformat(),
            "modo_prueba": modo_prueba,
            "prueba_respeta_dia": prueba_respeta_dia,
            "forzar": forzar,
        }

    nombre_plantilla = settings.WHATSAPP_TEMPLATE_RECORDATORIO_NAME.strip()
    idioma_plantilla = settings.WHATSAPP_TEMPLATE_RECORDATORIO_LANG.strip()
    if not nombre_plantilla:
        # Sin plantilla no hay envío posible fuera de la ventana de 24 h, y
        # no se hace fallback a texto libre. 500 para que el job del cron
        # falle y el problema se vea.
        logger.error("[recordatorio-diario] WHATSAPP_TEMPLATE_RECORDATORIO_NAME no está configurada")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": "plantilla_no_configurada",
                "detalle": "Defina WHATSAPP_TEMPLATE_RECORDATORIO_NAME para enviar el recordatorio",
                "modo_prueba": modo_prueba,
                "prueba_respeta_dia": prueba_respeta_dia,
                "forzar": forzar,
            },
        )

    async with _candado_recordatorio() as obtenido:
        if not obtenido:
            logger.warning("[recordatorio-diario] Otra ejecución está en curso; se omite esta")
            return {
                "status": "skipped",
                "razon": "otra ejecución en curso",
                "ventana": f"{hora_inicio:02d}:00-{hora_fin:02d}:00",
                "hora_actual_chile": ahora_chile.isoformat(),
                "modo_prueba": modo_prueba,
                "prueba_respeta_dia": prueba_respeta_dia,
                "forzar": forzar,
            }

        hoy_chile = ahora_chile.date()
        nombre_dia_manana, dia_enum_manana = _dia_semana_manana(hoy_chile)
        # Mismo reloj para marcar la conversación y para la idempotencia.
        ahora_utc = ahora_chile.astimezone(UTC).replace(tzinfo=None)

        clientes: list[Cliente] = []
        if modo_prueba and not (prueba_respeta_dia and dia_enum_manana is None):
            # Modo prueba: mantiene activo y opt-out; ignora dia_reparto salvo con
            # RECORDATORIO_PRUEBA_RESPETAR_DIA (mañana sábado/domingo: nadie).
            filtros = [
                Cliente.activo.is_(True),
                Cliente.opt_out_whatsapp.is_(False),
                Cliente.telefono.is_not(None),
            ]
            if prueba_respeta_dia:
                filtros.append(Cliente.dia_reparto == dia_enum_manana)
            async with SessionLocal() as session:
                result = await session.execute(
                    select(Cliente).where(*filtros).order_by(Cliente.id)
                )
                clientes = [
                    cliente
                    for cliente in result.scalars().all()
                    if normalizar_telefono(cliente.telefono) in telefonos_prueba
                ]
        elif not modo_prueba and dia_enum_manana is not None:
            async with SessionLocal() as session:
                result = await session.execute(
                    select(Cliente).where(
                        Cliente.dia_reparto == dia_enum_manana,
                        Cliente.activo.is_(True),
                        Cliente.opt_out_whatsapp.is_(False),
                    )
                )
                clientes = list(result.scalars().all())

        telefonos_enviados: list[str] = []
        telefonos_ya_enviados_hoy: list[str] = []
        errores: list[dict] = []

        for cliente in clientes:
            telefono = cliente.telefono
            if not telefono:
                errores.append({"cliente_id": cliente.id, "error": "Cliente sin teléfono registrado"})
                continue

            try:
                if not forzar:
                    async with SessionLocal() as session:
                        result = await session.execute(
                            select(ConversacionBot).where(ConversacionBot.telefono == telefono)
                        )
                        conversacion = result.scalar_one_or_none()

                    if (
                        conversacion is not None
                        and conversacion.iniciada_en is not None
                        and _fecha_chile(conversacion.iniciada_en) == hoy_chile
                    ):
                        telefonos_ya_enviados_hoy.append(telefono)
                        continue

                nombre = _nombre_para_plantilla(cliente.nombre)
                respuesta = await send_whatsapp_template(
                    to=telefono,
                    template_name=nombre_plantilla,
                    language_code=idioma_plantilla,
                    body_params=[nombre],
                    texto_registro=_texto_recordatorio(nombre),
                )
                if respuesta is None:
                    # No se marca la conversación: así el siguiente cron de la
                    # ventana lo reintenta (la idempotencia mira iniciada_en).
                    errores.append(
                        {
                            "cliente_id": cliente.id,
                            "telefono": telefono,
                            "error": "Meta rechazó el envío de la plantilla (ver logs)",
                        }
                    )
                    continue

                await marcar_activa(telefono, ahora_utc)
                telefonos_enviados.append(telefono)
            except Exception as exc:
                logger.exception("[recordatorio-diario] Falló el envío para %s", telefono)
                errores.append({"cliente_id": cliente.id, "telefono": telefono, "error": str(exc)})

        return {
            "clientes_evaluados": len(clientes),
            "mensajes_enviados": len(telefonos_enviados),
            "ya_enviados_hoy": len(telefonos_ya_enviados_hoy),
            "dia_manana": nombre_dia_manana,
            "telefonos_enviados": telefonos_enviados,
            "telefonos_ya_enviados_hoy": telefonos_ya_enviados_hoy,
            "errores": errores,
            "modo_prueba": modo_prueba,
            "prueba_respeta_dia": prueba_respeta_dia,
            "forzar": forzar,
        }
