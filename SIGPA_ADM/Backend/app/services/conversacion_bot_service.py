"""Estado de "conversación activa" por teléfono: implementa la restricción de
que el agente solo procesa conversaciones que él mismo inició (ver
marcar_activa/marcar_inactiva y su uso en whatsapp.py y order_flow.py)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal
from app.models.conversacion_bot import ConversacionBot
from app.models.mensaje_whatsapp import MensajeWhatsApp

ZONA_HORARIA_CHILE = ZoneInfo("America/Santiago")

# Una conversación activa expira automáticamente a las 07:00 hora Chile del
# día siguiente a iniciada_en (ver esta_activa), para no dejarla abierta
# indefinidamente si el cliente nunca la cierra confirmando un pedido.
HORA_CORTE_CONVERSACION = 7

# Margen al comparar iniciada_en (reloj de la app) con mensaje_whatsapp.creado_en
# (reloj de la BD): si la BD va atrasada, un mensaje recibido justo después de
# activar la conversación quedaría "antes" de iniciada_en. Ver
# es_primer_mensaje_conversacion.
MARGEN_RELOJ_BD = timedelta(minutes=5)


def _a_hora_chile(fecha: datetime) -> datetime:
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=ZoneInfo("UTC"))
    return fecha.astimezone(ZONA_HORARIA_CHILE)


async def marcar_activa(phone: str) -> None:
    """Deja la conversación de este teléfono como activa. La usará el futuro
    cron del recordatorio (aún no existe) para "abrir" la conversación antes
    de que el cliente escriba."""
    ahora = datetime.utcnow()
    stmt = pg_insert(ConversacionBot).values(
        telefono=phone,
        activa=True,
        iniciada_en=ahora,
        actualizada_en=ahora,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ConversacionBot.telefono],
        set_={"activa": True, "iniciada_en": ahora, "actualizada_en": ahora},
    )
    async with SessionLocal() as session:
        await session.execute(stmt)
        await session.commit()


async def marcar_inactiva(phone: str) -> None:
    """Cierra la conversación de este teléfono. Funciona aunque no exista
    fila previa (upsert)."""
    ahora = datetime.utcnow()
    stmt = pg_insert(ConversacionBot).values(
        telefono=phone,
        activa=False,
        actualizada_en=ahora,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ConversacionBot.telefono],
        set_={"activa": False, "actualizada_en": ahora},
    )
    async with SessionLocal() as session:
        await session.execute(stmt)
        await session.commit()


async def esta_activa(phone: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ConversacionBot).where(ConversacionBot.telefono == phone)
        )
        conversacion = result.scalar_one_or_none()

    if conversacion is None or not conversacion.activa:
        return False

    if conversacion.iniciada_en is None:
        # No debería ocurrir (marcar_activa siempre setea iniciada_en), pero
        # sin ese dato no hay corte que calcular: se mantiene activa.
        return True

    iniciada_en_chile = _a_hora_chile(conversacion.iniciada_en)
    corte = datetime.combine(
        iniciada_en_chile.date() + timedelta(days=1),
        time(hour=HORA_CORTE_CONVERSACION),
        tzinfo=ZONA_HORARIA_CHILE,
    )

    if datetime.now(ZONA_HORARIA_CHILE) >= corte:
        await marcar_inactiva(phone)
        return False

    return True


async def es_primer_mensaje_conversacion(phone: str) -> bool:
    """True si el mensaje entrante que se está procesando (ya registrado en
    mensaje_whatsapp) es el primero de texto o ubicación del cliente desde
    que se inició la conversación activa, es decir, si la respuesta del bot
    es su primer mensaje de la conversación (ver la presentación del
    asistente en whatsapp.py). Se cuenta en la BD para que no dependa de
    memoria del proceso. Ambas fechas están en UTC (iniciada_en viene de
    datetime.utcnow() y creado_en del now() de la BD), pero de relojes
    distintos: por eso se cuenta desde MARGEN_RELOJ_BD antes de iniciada_en.
    Un mensaje espontáneo en ese margen ya recibió la presentación (en el
    mensaje de derivación), así que no repetirla es correcto."""
    async with SessionLocal() as session:
        iniciada_en = await session.scalar(
            select(ConversacionBot.iniciada_en).where(ConversacionBot.telefono == phone)
        )
        consulta = select(func.count()).select_from(MensajeWhatsApp).where(
            MensajeWhatsApp.telefono == phone,
            MensajeWhatsApp.direccion == "entrante",
            MensajeWhatsApp.tipo.in_(("text", "location")),
        )
        if iniciada_en is not None:
            consulta = consulta.where(MensajeWhatsApp.creado_en >= iniciada_en - MARGEN_RELOJ_BD)
        entrantes = await session.scalar(consulta)
    return (entrantes or 0) <= 1
