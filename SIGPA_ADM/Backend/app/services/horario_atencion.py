"""Horario de atención de las ejecutivas y mensaje de derivación al cliente.

Cuando el bot deriva al cliente a una ejecutiva (mensaje espontáneo sin
conversación activa, teléfono en más de un cliente) el texto depende de si
es horario de atención: dentro se le dice que lo contactarán a la brevedad,
fuera se le informa el horario. Todo se calcula en hora LOCAL de Chile
(zoneinfo America/Santiago), así el horario de verano no lo afecta.

La configuración (HORARIO_ATENCION_INICIO, HORARIO_ATENCION_FIN,
DIAS_ATENCION, NOMBRE_ASISTENTE) se lee de settings en cada llamada, y la
hora actual se recibe como parámetro para poder probarlo sin el reloj real.
La regla de horario no aplica a las conversaciones activas: el agente sigue
tomando pedidos a cualquier hora (ver whatsapp.py).
"""

import logging
import unicodedata
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.core.config import settings

logger = logging.getLogger(__name__)

ZONA_HORARIA_CHILE = ZoneInfo("America/Santiago")

# Índice = date.weekday() (0=lunes...6=domingo).
_NOMBRES_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_PREFIJOS_DIAS = {"lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4, "sab": 5, "dom": 6}

MARCA_FUERA_DE_HORARIO = "(fuera de horario)"


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def _parsear_hora(valor: str) -> time:
    horas, _, minutos = valor.strip().partition(":")
    return time(hour=int(horas), minute=int(minutos or 0))


def _indice_dia(nombre: str) -> int:
    return _PREFIJOS_DIAS[_sin_tildes(nombre.strip().lower())[:3]]


def dias_atencion() -> list[int]:
    """Días de atención (date.weekday()) según DIAS_ATENCION: nombres de día
    separados por coma ("lunes,martes,...") o rangos ("lunes-viernes");
    acepta abreviaturas de 3 letras y tildes opcionales. Un valor que no se
    entiende se ignora (con warning); si no queda ninguno, se atiende todos
    los días."""
    dias: set[int] = set()
    for parte in settings.DIAS_ATENCION.split(","):
        if not parte.strip():
            continue
        try:
            if "-" in parte:
                desde, hasta = (_indice_dia(extremo) for extremo in parte.split("-", 1))
                indice = desde
                dias.add(indice)
                while indice != hasta:
                    indice = (indice + 1) % 7
                    dias.add(indice)
            else:
                dias.add(_indice_dia(parte))
        except (KeyError, ValueError):
            logger.warning("[horario_atencion] Día de atención no reconocido: %r", parte)
    if not dias:
        return list(range(7))
    return sorted(dias)


def _hora_chile(ahora: datetime) -> datetime:
    # Una fecha sin zona horaria se asume en UTC (como datetime.utcnow()).
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=ZoneInfo("UTC"))
    return ahora.astimezone(ZONA_HORARIA_CHILE)


def en_horario_atencion(ahora: datetime | None = None) -> bool:
    """True si `ahora` cae en un día de atención, desde HORARIO_ATENCION_INICIO
    (inclusive) hasta HORARIO_ATENCION_FIN (exclusive), en hora de Chile."""
    ahora_chile = _hora_chile(ahora) if ahora is not None else datetime.now(ZONA_HORARIA_CHILE)
    if ahora_chile.weekday() not in dias_atencion():
        return False
    inicio = _parsear_hora(settings.HORARIO_ATENCION_INICIO)
    fin = _parsear_hora(settings.HORARIO_ATENCION_FIN)
    return inicio <= ahora_chile.time() < fin


def _texto_dias(dias: list[int]) -> str:
    """"de lunes a viernes" si son días seguidos (3 o más), si no la lista
    ("lunes, miércoles y viernes")."""
    nombres = [_NOMBRES_DIAS[dia] for dia in dias]
    if len(dias) >= 3 and dias == list(range(dias[0], dias[-1] + 1)):
        return f"de {nombres[0]} a {nombres[-1]}"
    if len(nombres) == 1:
        nombre = nombres[0]
        return f"los {nombre}s" if nombre in ("sábado", "domingo") else f"los {nombre}"
    return f"{', '.join(nombres[:-1])} y {nombres[-1]}"


def texto_horario_atencion() -> str:
    """Ej. "de 09:00 a 18:00 hrs" (todos los días) o "de lunes a viernes, de
    09:00 a 18:00 hrs"."""
    inicio = _parsear_hora(settings.HORARIO_ATENCION_INICIO).strftime("%H:%M")
    fin = _parsear_hora(settings.HORARIO_ATENCION_FIN).strftime("%H:%M")
    horas = f"de {inicio} a {fin} hrs"
    dias = dias_atencion()
    if len(dias) == 7:
        return horas
    return f"{_texto_dias(dias)}, {horas}"


def presentacion_asistente() -> str:
    return f"Soy {settings.NOMBRE_ASISTENTE}, tu asistente virtual."


def mensaje_derivacion_ejecutiva(ahora: datetime | None = None) -> str:
    """Texto que recibe el cliente cuando el bot lo deriva a una ejecutiva,
    según si `ahora` (por defecto, la hora actual) es horario de atención."""
    saludo = f"¡Hola! {presentacion_asistente()} Gracias por escribirnos."
    if en_horario_atencion(ahora):
        return f"{saludo} Una ejecutiva revisará tu mensaje y te contactará a la brevedad."
    return (
        f"{saludo} Nuestro horario de atención es {texto_horario_atencion()}. "
        "Una ejecutiva se pondrá en contacto contigo dentro del horario de atención."
    )
