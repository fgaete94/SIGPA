// Cierre de sesión por inactividad (#134): si pasan `limiteMs` sin clics, teclado ni desplazamiento,
// se llama a `alExpirar` una sola vez. Solo cuentan las acciones del usuario: la renovación automática
// del token no pasa por aquí. Se compara contra la hora de la última actividad, y no se usa un único
// setTimeout, porque el navegador congela los temporizadores en pestañas de fondo o con el equipo suspendido.
export const MINUTOS_INACTIVIDAD = 15; // Provisorio: el tiempo definitivo lo define el PO.
export const EVENTOS_ACTIVIDAD = ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll'];
const INTERVALO_REVISION_MS = 15_000;

export function vigilarInactividad({
  alExpirar,
  limiteMs = MINUTOS_INACTIVIDAD * 60_000,
  objetivo = globalThis.window,
  documento = globalThis.document,
  ahora = Date.now,
  intervaloMs = INTERVALO_REVISION_MS,
}) {
  let ultimaActividad = ahora();
  let terminado = false;

  const registrar = () => { ultimaActividad = ahora(); };
  const revisar = () => {
    if (terminado || ahora() - ultimaActividad < limiteMs) return;
    detener();
    alExpirar();
  };
  // Al volver a la pestaña se revisa de inmediato, sin esperar al siguiente intervalo.
  const alCambiarVisibilidad = () => { if (documento?.visibilityState === 'visible') revisar(); };

  // capture: true para recibir también el scroll de elementos internos (ese evento no burbujea).
  EVENTOS_ACTIVIDAD.forEach(evento => objetivo?.addEventListener(evento, registrar, { capture: true, passive: true }));
  documento?.addEventListener('visibilitychange', alCambiarVisibilidad);
  const intervalo = setInterval(revisar, intervaloMs);

  function detener() {
    terminado = true;
    clearInterval(intervalo);
    EVENTOS_ACTIVIDAD.forEach(evento => objetivo?.removeEventListener(evento, registrar, { capture: true }));
    documento?.removeEventListener('visibilitychange', alCambiarVisibilidad);
  }

  return { detener, revisar };
}