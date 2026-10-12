// /pedidos devuelve las fechas sin zona (timestamp de Postgres, en UTC) y /rutas/pedidos-pendientes con zona.
// Sin zona se interpretan como UTC; si no, el navegador las toma como hora de Chile y corre el día en 3 o 4 horas.
const ZONA = 'America/Santiago';
const formatoDia = new Intl.DateTimeFormat('en-CA', { timeZone: ZONA, year: 'numeric', month: '2-digit', day: '2-digit' });

export const aFecha = iso => new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(iso) ? iso : `${iso}Z`);
// Día calendario en Chile como 'AAAA-MM-DD' (se compara directo con el valor de un <input type="date">).
export const diaChile = iso => formatoDia.format(aFecha(iso));
export const hoyChile = (ahora = new Date()) => formatoDia.format(ahora);
export const fechaCorta = iso => aFecha(iso).toLocaleDateString('es-CL', { timeZone: ZONA });
export const fechaHora = iso => aFecha(iso).toLocaleString('es-CL', { timeZone: ZONA, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
export const horaCorta = fecha => fecha.toLocaleTimeString('es-CL', { timeZone: ZONA, hour: '2-digit', minute: '2-digit' });

// Suma días a 'AAAA-MM-DD' sin pasar por la zona horaria del navegador.
export function sumarDias(dia, n) {
  const [a, m, d] = dia.split('-').map(Number);
  return new Date(Date.UTC(a, m - 1, d + n)).toISOString().slice(0, 10);
}
