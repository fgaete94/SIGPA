import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class Settings(BaseSettings):
    PROJECT_NAME: str = "SIGPA"

    META_WHATSAPP_TOKEN: str = ""
    META_PHONE_NUMBER_ID: str = ""
    META_WABA_ID: str = ""
    META_VERIFY_TOKEN: str = ""
    META_API_URL: str = ""

    DATABASE_URL: str = ""
    # Historia #78: conexiones máximas del backend a la BD (pool_size +
    # max_overflow), por debajo del límite de 15 del pooler de Supabase en
    # modo sesión, que se comparte con n8n. Ver app/core/database.py.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 0
    DB_POOL_TIMEOUT: float = 30

    OPENAI_API_KEY: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""

    CORS_ORIGINS: str = "http://localhost:5173"

    # Teléfono (WhatsApp) de la ejecutiva que recibe las derivaciones. Sin
    # default: se define en el .env / entorno de Render.
    EJECUTIVA_PHONE: str = ""
    # Historia #152: plantilla Utility aprobada por Meta para avisar a la
    # ejecutiva aunque no haya escrito al número del bot en las últimas 24 h
    # (ver notificacion_ejecutiva). Sin nombre se envía el texto libre, que
    # Meta solo entrega dentro de esa ventana.
    WHATSAPP_TEMPLATE_EJECUTIVA_NAME: str = ""
    WHATSAPP_TEMPLATE_EJECUTIVA_LANG: str = "es_CL"

    INTERNAL_CRON_SECRET: str = ""

    # Usuario de Supabase Auth que usan los scripts de prueba locales
    # (scripts/test_auth.py, test_api_*.py, test_cantidad_solicitada.py). Los
    # define cada desarrollador en su .env local, que no se versiona. NO deben
    # definirse en Render: el backend en producción no los usa.
    TEST_ADMIN_EMAIL: str = ""
    TEST_ADMIN_PASSWORD: str = ""

    # Recordatorio diario (POST /internal/recordatorio-diario): plantilla
    # Utility aprobada por Meta. Sin nombre de plantilla no se envía nada.
    WHATSAPP_TEMPLATE_RECORDATORIO_NAME: str = ""
    WHATSAPP_TEMPLATE_RECORDATORIO_LANG: str = "es_CL"
    # MODO PRUEBA del recordatorio: teléfonos separados por coma. Si no está
    # vacío, solo se envía a esos teléfonos y se ignora el día de reparto.
    RECORDATORIO_TELEFONOS_PRUEBA: str = ""
    # Solo con modo prueba: si es true, a los teléfonos de prueba también se
    # les exige dia_reparto == mañana (como en producción).
    RECORDATORIO_PRUEBA_RESPETAR_DIA: bool = False
    # Ventana horaria (hora de Chile) en que el recordatorio puede enviarse:
    # desde HORA_INICIO:00 hasta antes de HORA_FIN:00 (por defecto horas 15 a
    # 19). Es ancha porque GitHub Actions retrasa y se salta ejecuciones
    # programadas; el envío es idempotente por fecha, así que no duplica.
    RECORDATORIO_HORA_INICIO: int = 15
    RECORDATORIO_HORA_FIN: int = 20

    # Historia #150: horas que un borrador de pedido guardado en la BD
    # (borrador_pedido) sigue vigente; pasado ese tiempo no se retoma.
    BORRADOR_TTL_HORAS: int = 24

    # Asistente virtual y horario de atención de las ejecutivas (hora de
    # Chile), ver app/services/horario_atencion.py. DIAS_ATENCION: días
    # separados por coma o un rango, ej. "lunes-viernes".
    NOMBRE_ASISTENTE: str = "MarIA"
    HORARIO_ATENCION_INICIO: str = "09:00"
    HORARIO_ATENCION_FIN: str = "18:00"
    DIAS_ATENCION: str = "lunes,martes,miercoles,jueves,viernes,sabado,domingo"

    # Webhook de n8n que geocodifica y optimiza la ruta de reparto (EP-04).
    # El backend lo llama desde POST /rutas/planificar (ver ruta_service).
    N8N_ROUTE_WEBHOOK_URL: str = ""
    N8N_ROUTE_WEBHOOK_SECRET: str = ""
    N8N_ROUTE_TIMEOUT_SECONDS: float = 45
    # Máximo de pedidos por POST /rutas/planificar.
    RUTA_MAX_PEDIDOS: int = 30
    # Restricción de peso por viaje (ver ruta_service): capacidad del camión,
    # peso desde el cual el último viaje ya no termina en la casa del
    # despachador y máximo de viajes por planificación.
    RUTA_CAPACIDAD_KG: int = 1100
    RUTA_UMBRAL_CASA_KG: int = 800
    RUTA_MAX_VIAJES: int = 3

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins_list(self) -> list[str]:
        # Convierte el string de la variable de entorno en una lista
        return [origen.strip() for origen in self.CORS_ORIGINS.split(",") if origen.strip()]

    @property
    def recordatorio_telefonos_prueba_list(self) -> list[str]:
        # Import diferido: cliente_lookup importa los modelos, que dependen
        # de este módulo.
        from app.services.cliente_lookup import normalizar_telefono

        telefonos = (normalizar_telefono(t) for t in self.RECORDATORIO_TELEFONOS_PRUEBA.split(","))
        return [t for t in telefonos if t]


settings = Settings()

if not settings.EJECUTIVA_PHONE:
    logging.getLogger(__name__).warning(
        "EJECUTIVA_PHONE está vacío: las derivaciones a la ejecutiva no se podrán enviar"
    )

def validar_ventana_recordatorio(config: Settings) -> None:
    if 0 <= config.RECORDATORIO_HORA_INICIO < config.RECORDATORIO_HORA_FIN <= 24:
        return
    inicio = Settings.model_fields["RECORDATORIO_HORA_INICIO"].default
    fin = Settings.model_fields["RECORDATORIO_HORA_FIN"].default
    logging.getLogger(__name__).warning(
        "Ventana del recordatorio inválida (RECORDATORIO_HORA_INICIO=%s, RECORDATORIO_HORA_FIN=%s): "
        "debe cumplir 0 <= inicio < fin <= 24. Se usan los valores por defecto (%s-%s).",
        config.RECORDATORIO_HORA_INICIO,
        config.RECORDATORIO_HORA_FIN,
        inicio,
        fin,
    )
    config.RECORDATORIO_HORA_INICIO = inicio
    config.RECORDATORIO_HORA_FIN = fin


validar_ventana_recordatorio(settings)

if settings.RECORDATORIO_TELEFONOS_PRUEBA.strip():
    if settings.RECORDATORIO_PRUEBA_RESPETAR_DIA:
        logging.getLogger(__name__).warning(
            "Recordatorio en MODO PRUEBA: solo se envía a los teléfonos de prueba cuyo día de reparto sea mañana"
        )
    else:
        logging.getLogger(__name__).warning(
            "Recordatorio en MODO PRUEBA: solo se envía a los teléfonos de prueba y se ignora el día de reparto"
        )
