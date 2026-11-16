"""
Script temporal para validar el planificador interno del recordatorio
(app/services/planificador_recordatorio.py), su arranque en el lifespan de
la app y el bloque "recordatorio" de /health.

No toca la BD ni envía mensajes: ejecutar_recordatorio se reemplaza por un
fake en cada caso. La lógica real del recordatorio (ventana, candado,
idempotencia) la cubre scripts/test_recordatorio.py.

Uso: python -m scripts.test_planificador_recordatorio
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi.responses import JSONResponse

import app.api.routes.internal as internal
from app.core.config import settings
from app.main import app
from app.services import planificador_recordatorio as planificador

TELEFONO_PRUEBA = "56933300999"
fallos = 0


def check(condicion: bool, descripcion: str) -> None:
    global fallos
    print(f"{'ok   ' if condicion else 'FALLO'} {descripcion}")
    if not condicion:
        fallos += 1


class Config:
    """Cambia settings durante un caso y lo restaura al salir."""

    def __init__(self, **cambios):
        self.cambios = cambios
        self.originales = {}

    def __enter__(self):
        for k, v in self.cambios.items():
            self.originales[k] = getattr(settings, k)
            setattr(settings, k, v)

    def __exit__(self, *_):
        for k, v in self.originales.items():
            setattr(settings, k, v)


RESULTADO_OK = {
    "clientes_evaluados": 2,
    "mensajes_enviados": 1,
    "ya_enviados_hoy": 1,
    "dia_manana": "Martes",
    "telefonos_enviados": [TELEFONO_PRUEBA],
    "telefonos_ya_enviados_hoy": ["56933300998"],
    "errores": [{"cliente_id": 1, "telefono": "56933300997", "error": "x"}],
    "modo_prueba": True,
    "prueba_respeta_dia": True,
    "forzar": False,
}


async def main() -> None:
    original_ejecutar = internal.ejecutar_recordatorio
    original_ahora = internal._ahora_chile
    llamadas: list[bool] = []

    async def fake_ok(forzar: bool = False):
        llamadas.append(forzar)
        return RESULTADO_OK

    async def fake_falla(forzar: bool = False):
        raise RuntimeError("BD caída")

    internal._ahora_chile = lambda: datetime(2026, 11, 16, 15, 5, tzinfo=ZoneInfo("America/Santiago"))
    try:
        print("-- Resumen sin datos personales")
        r = planificador.resumir(RESULTADO_OK)
        check(r == {"status": "ok", "clientes_evaluados": 2, "mensajes_enviados": 1, "ya_enviados_hoy": 1,
                    "errores": 1, "dia_manana": "Martes", "modo_prueba": True}, "resume conteos")
        check("569333" not in json.dumps(r), "el resumen no incluye teléfonos")
        check(planificador.resumir({"status": "skipped", "razon": "fuera de la ventana horaria", "hora_actual_chile": "x"})
              == {"status": "skipped", "razon": "fuera de la ventana horaria"}, "skipped conserva solo status y razón")
        check(planificador.resumir(JSONResponse(status_code=500, content={})) == {"status": "error", "codigo_http": 500},
              "un 500 (plantilla sin configurar) queda como error")

        print("-- Una revisión")
        internal.ejecutar_recordatorio = fake_ok
        await planificador.revisar_una_vez()
        check(llamadas == [False], "llama al recordatorio sin forzar")
        check(planificador.estado["ultima_revision"] == "2026-11-16T15:05:00-03:00", "guarda la hora (Chile) de la revisión")
        check(planificador.estado["ultimo_resultado"]["mensajes_enviados"] == 1, "guarda el resumen")
        internal.ejecutar_recordatorio = fake_falla
        r = await planificador.revisar_una_vez()
        check(r == {"status": "error", "detalle": "RuntimeError"}, "un error no detiene el ciclo y queda en el estado")

        print("-- Arranque")
        internal.ejecutar_recordatorio = fake_ok
        with Config(RECORDATORIO_SCHEDULER_ACTIVO=False):
            check(planificador.iniciar() is False and planificador._tarea is None, "apagado por defecto: no arranca")
            check(planificador.estado["activo"] is False, "estado activo=false")

        registros: list[str] = []
        manejador = logging.Handler()
        manejador.emit = lambda rec: registros.append(rec.getMessage())
        logging.getLogger(planificador.__name__).addHandler(manejador)
        with Config(RECORDATORIO_SCHEDULER_ACTIVO=True, RECORDATORIO_SCHEDULER_INTERVALO_MIN=0,
                    RECORDATORIO_TELEFONOS_PRUEBA=TELEFONO_PRUEBA):
            llamadas.clear()
            async with app.router.lifespan_context(app):
                await asyncio.sleep(0.2)
                check(planificador.estado["activo"] is True, "con RECORDATORIO_SCHEDULER_ACTIVO=true arranca con la app")
                check(planificador.estado["intervalo_min"] == 1, "intervalo menor a 1 min se sube a 1")
                check(llamadas == [False], "la primera revisión es inmediata")
                check(planificador.iniciar() is True and len(llamadas) == 1, "llamar iniciar() otra vez no crea un segundo ciclo")
                check(any("Planificador del recordatorio ACTIVO" in m and "MODO PRUEBA" in m for m in registros),
                      "avisa al arrancar, indicando el modo prueba")

                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                    salud = (await http.get("/health")).json()
                check(salud["status"] == "ok" and salud["recordatorio"]["activo"] is True, "/health muestra recordatorio.activo")
                check(set(salud["recordatorio"]) == {"activo", "intervalo_min", "ultima_revision", "ultimo_resultado"},
                      "/health trae activo, intervalo, ultima_revision y ultimo_resultado")
                check("569333" not in json.dumps(salud), "/health no expone teléfonos")
            check(planificador.estado["activo"] is False and planificador._tarea is None, "al apagar la app se detiene")
        logging.getLogger(planificador.__name__).removeHandler(manejador)

        print("-- Endpoint sin cambios")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            with Config(INTERNAL_CRON_SECRET="secreto-prueba"):
                check((await http.post("/internal/recordatorio-diario")).status_code == 401, "sin clave: 401")
                llamadas.clear()
                r = await http.post("/internal/recordatorio-diario", headers={"X-Cron-Secret": "secreto-prueba"})
                check(r.status_code == 200 and r.json()["mensajes_enviados"] == 1 and llamadas == [False],
                      "con clave: ejecuta el mismo recordatorio")
    finally:
        internal.ejecutar_recordatorio = original_ejecutar
        internal._ahora_chile = original_ahora
        await planificador.detener()

    print(f"\n{'Todo ok' if not fallos else f'{fallos} fallo(s)'}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    asyncio.run(main())
