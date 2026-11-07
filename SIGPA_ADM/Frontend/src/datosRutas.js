// Pestaña Despachos: las rutas ya hechas, reconstruidas desde los pedidos, que conservan su viaje y su orden de entrega.
import { lunesDe } from './datosPeriodo.js';
import { resuelta } from './estados.js';
import { diaChile, sumarDias } from './fechas.js';
import { ordenViaje } from './viajes.js';

const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const diaMes = dia => `${Number(dia.slice(8))} ${MESES[Number(dia.slice(5, 7)) - 1]}`;
const formatoDia = new Intl.DateTimeFormat('es-CL', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });
const momento = p => p.resultado_en ?? p.actualizado_en;

// De lunes a viernes: sábado y domingo no se trabaja.
export const etiquetaSemana = lunes => `Semana del ${diaMes(lunes)} al ${diaMes(sumarDias(lunes, 4))}`;

export function etiquetaDia(dia) {
  const texto = formatoDia.format(new Date(`${dia}T12:00:00Z`));
  return texto[0].toUpperCase() + texto.slice(1);
}

// Calendario: las semanas que tienen algún día hábil en el mes ('AAAA-MM'), cada una con sus 5 días, de lunes a viernes.
export function semanasDelMes(mes) {
  const semanas = [];
  for (let lunes = lunesDe(`${mes}-01`); lunes.slice(0, 7) <= mes; lunes = sumarDias(lunes, 7)) {
    const dias = Array.from({ length: 5 }, (_, i) => sumarDias(lunes, i)).map(dia => ({ dia, numero: Number(dia.slice(8)), delMes: dia.startsWith(mes) }));
    if (dias.some(d => d.delMes)) semanas.push({ lunes, dias });
  }
  return semanas;
}

export function mesContiguo(mes, delta) {
  const fecha = new Date(Date.UTC(Number(mes.slice(0, 4)), Number(mes.slice(5, 7)) - 1 + delta, 1));
  return fecha.toISOString().slice(0, 7);
}

// Años del calendario: el actual y los anteriores, más el que se esté viendo si es más antiguo.
export function aniosCalendario(hoy, mes, cantidad = 6) {
  const actual = Number(hoy.slice(0, 4));
  const anios = Array.from({ length: cantidad }, (_, i) => String(actual - i));
  return anios.includes(mes.slice(0, 4)) ? anios : [...anios, mes.slice(0, 4)];
}

// El calendario no pasa del mes actual.
export const limitarMes = (mes, hoy) => (mes > hoy.slice(0, 7) ? hoy.slice(0, 7) : mes);

export function resueltoEnTramo(p, { desde, hasta }) {
  if (!momento(p)) return false;
  const dia = diaChile(momento(p));
  return dia >= desde && dia <= hasta;
}

// Una ruta por día con paradas resueltas, en orden de fecha y con las paradas en el orden planificado.
export function rutasDelTramo(pedidos, clientes, tramo) {
  const direccionCliente = new Map(clientes.map(c => [c.id, c.direccion]));
  const porDia = new Map();
  for (const p of pedidos) {
    if (!resuelta(p) || p.orden_entrega == null || !resueltoEnTramo(p, tramo)) continue;
    const dia = diaChile(momento(p));
    if (!porDia.has(dia)) porDia.set(dia, []);
    porDia.get(dia).push({ ...p, direccion: p.direccion_despacho || direccionCliente.get(p.cliente_id) || null });
  }
  return [...porDia].sort(([a], [b]) => a.localeCompare(b)).map(([dia, paradas]) => ({ dia, etiqueta: etiquetaDia(dia), paradas: paradas.sort((a, b) => ordenViaje(a, b) || a.id - b.id) }));
}

// La semana completa: siempre de lunes a viernes, aunque un día no tenga despachos. Si excepcionalmente
// hubo uno en fin de semana, también aparece, para que ninguna entrega quede fuera.
export function rutasDeLaSemana(pedidos, clientes, lunes) {
  const rutas = rutasDelTramo(pedidos, clientes, { desde: lunes, hasta: sumarDias(lunes, 6) });
  const habiles = Array.from({ length: 5 }, (_, i) => sumarDias(lunes, i));
  const delDia = dia => rutas.find(r => r.dia === dia) ?? { dia, etiqueta: etiquetaDia(dia), paradas: [] };
  return [...habiles.map(delDia), ...rutas.filter(r => !habiles.includes(r.dia))];
}
