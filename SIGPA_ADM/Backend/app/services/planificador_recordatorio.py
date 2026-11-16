"""Planificador interno del recordatorio diario.

Con RECORDATORIO_SCHEDULER_ACTIVO=true, el backend llama solo a
ejecutar_recordatorio() cada RECORDATORIO_SCHEDULER_INTERVALO_MIN minutos,
sin depender de un cron externo. Es seguro que conviva con el Cron Job de
Render o con GitHub Actions: la ventana horaria, el candado de Postgres y la
idempotencia por fecha de ejecutar_recordatorio evitan envíos duplicados.

El estado (última revisión y resumen del resultado) se publica en /health,
que es público: por eso el resumen nunca incluye teléfonos.
"""

import asyncio
import logging

from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

INTERVALO_MINIMO_MIN = 1

estado: dict = {
    "activo": False,
    "intervalo_min": None,
    "ultima_revision": None,
    "ultimo_resultado": None,
}

_tarea: asyncio.Task | None = None


def intervalo_min() -> int:
    return max(INTERVALO_MINIMO_MIN, settings.RECORDATORIO_SCHEDULER_INTERVALO_MIN)


def resumir(resultado) -> dict:
    """Resumen del resultado de ejecutar_recordatorio sin datos personales."""
    if isinstance(resultado, JSONResponse):
        return {"status": "error", "codigo_http": resultado.status_code}
    if not isinstance(resultado, dict):
        return {"status": "error", "detalle": "resultado inesperado"}
    if "status" in resultado:
        return {k: resultado[k] for k in ("status", "razon") if k in resultado}
    return {
        "status": "ok",
        "clientes_evaluados": resultado.get("clientes_evaluados", 0),
        "mensajes_enviados": resultado.get("mensajes_enviados", 0),
        "ya_enviados_hoy": resultado.get("ya_enviados_hoy", 0),
        "errores": len(resultado.get("errores") or []),
        "dia_manana": resultado.get("dia_manana"),
        "modo_prueba": resultado.get("modo_prueba"),
    }


async def revisar_una_vez() -> dict:
    """Una revisión: llama al recordatorio y guarda el resumen en estado.
    Nunca lanza excepción, para que el ciclo no se detenga."""
    # Import diferido: internal importa la base de datos y los modelos.
    from app.api.routes.internal import _ahora_chile, ejecutar_recordatorio

    try:
        resumen = resumir(await ejecutar_recordatorio(forzar=False))
    except Exception as exc:
        logger.exception("[planificador-recordatorio] Falló la revisión")
        resumen = {"status": "error", "detalle": type(exc).__name__}
    estado["ultima_revision"] = _ahora_chile().isoformat(timespec="seconds")
    estado["ultimo_resultado"] = resumen
    if resumen.get("status") == "ok":
        logger.info("[planificador-recordatorio] Revisión: %s", resumen)
    return resumen


async def _ciclo(segundos: float) -> None:
    while True:
        await revisar_una_vez()
        await asyncio.sleep(segundos)


def iniciar() -> bool:
    """Arranca el ciclo si RECORDATORIO_SCHEDULER_ACTIVO está en true.
    Devuelve True si quedó corriendo."""
    global _tarea
    if not settings.RECORDATORIO_SCHEDULER_ACTIVO:
        estado["activo"] = False
        return False
    if _tarea is not None and not _tarea.done():
        return True
    minutos = intervalo_min()
    estado.update(activo=True, intervalo_min=minutos)
    modo_prueba = bool(settings.recordatorio_telefonos_prueba_list)
    logger.warning(
        "Planificador del recordatorio ACTIVO: revisa cada %s min, ventana %02d:00-%02d:00 hora Chile, %s",
        minutos,
        settings.RECORDATORIO_HORA_INICIO,
        settings.RECORDATORIO_HORA_FIN,
        "MODO PRUEBA (solo teléfonos de prueba)" if modo_prueba else "PRODUCCIÓN (todos los clientes)",
    )
    _tarea = asyncio.create_task(_ciclo(minutos * 60), name="planificador-recordatorio")
    return True


async def detener() -> None:
    global _tarea
    if _tarea is not None:
        _tarea.cancel()
        try:
            await _tarea
        except asyncio.CancelledError:
            pass
        _tarea = None
    estado["activo"] = False


def estado_publico() -> dict:
    return dict(estado)
