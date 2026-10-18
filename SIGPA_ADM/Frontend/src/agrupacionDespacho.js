// Historia #67: pedidos para organizar el despacho, agrupados por día de reparto del cliente y comuna.
// Misma regla que GET /pedidos/despacho (confirmados, en despacho y entregados, por día y comuna), pero
// sobre los pedidos que el panel ya cargó: los filtros muestran cuántos hay y las acciones se ven al instante.
import { aFecha } from './fechas.js';

export const SIN_DIA = 'sin_dia';
export const SIN_COMUNA = 'Sin comuna';
const DIAS = ['Lunes', 'Martes', 'Miercoles', 'Jueves', 'Viernes'];
const ESTADOS_DESPACHO = ['confirmado', 'en_despacho', 'entregado'];

// Filtro de estado: por defecto lo que falta entregar (los entregados se acumulan sin fecha de entrega).
export const VISTAS = [
  ['por_entregar', 'Por entregar', ['confirmado', 'en_despacho']],
  ['entregados', 'Entregados', ['entregado']],
  ['todos', 'Todos', ESTADOS_DESPACHO],
];

const diaDe = p => (DIAS.includes(p.dia_reparto) ? p.dia_reparto : SIN_DIA);
const comunaDe = p => p.comuna_nombre || SIN_COMUNA;
const textos = new Intl.Collator('es', { sensitivity: 'base' });
const posicionDia = dia => (dia === SIN_DIA ? DIAS.length : DIAS.indexOf(dia));
const porComuna = (a, b) => (a === SIN_COMUNA) - (b === SIN_COMUNA) || textos.compare(a, b);

export const pedidosVista = (pedidos, vista) => {
  const estados = (VISTAS.find(([v]) => v === vista) || VISTAS[0])[2];
  return pedidos.filter(p => estados.includes(p.estado));
};

// Opciones de los filtros con su cantidad, contadas sobre la vista elegida.
export function opcionesDespacho(pedidos) {
  const contar = (clave, ordenar) => [...pedidos.reduce((m, p) => m.set(clave(p), (m.get(clave(p)) || 0) + 1), new Map())]
    .sort(([a], [b]) => ordenar(a, b));
  return { dias: contar(diaDe, (a, b) => posicionDia(a) - posicionDia(b)), comunas: contar(comunaDe, porComuna) };
}

// Día y comuna vacíos = todos. Orden: día de reparto, comuna y, dentro de cada grupo, el más reciente primero.
export function gruposDespacho(pedidos, { dia = '', comuna = '' } = {}) {
  const elegidos = pedidos.filter(p => (!dia || diaDe(p) === dia) && (!comuna || comunaDe(p) === comuna));
  const grupos = new Map();
  for (const p of elegidos) {
    const d = diaDe(p);
    if (!grupos.has(d)) grupos.set(d, new Map());
    const comunas = grupos.get(d);
    if (!comunas.has(comunaDe(p))) comunas.set(comunaDe(p), []);
    comunas.get(comunaDe(p)).push(p);
  }
  const reciente = (a, b) => aFecha(b.creado_en) - aFecha(a.creado_en) || b.id - a.id;
  return [...grupos]
    .sort(([a], [b]) => posicionDia(a) - posicionDia(b))
    .map(([d, comunas]) => {
      const lista = [...comunas].sort(([a], [b]) => porComuna(a, b))
        .map(([c, ps]) => ({ comuna: c, pedidos: ps.sort(reciente), total: ps.reduce((s, p) => s + Number(p.total || 0), 0) }));
      return { dia: d, comunas: lista, cantidad: lista.reduce((s, c) => s + c.pedidos.length, 0) };
    });
}

// Día de la semana en Chile ('Lunes'…'Viernes') o null el fin de semana: marca "hoy" en el filtro.
export function diaHoy(ahora = new Date()) {
  const nombre = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Santiago', weekday: 'long' }).format(ahora);
  return { Monday: 'Lunes', Tuesday: 'Martes', Wednesday: 'Miercoles', Thursday: 'Jueves', Friday: 'Viernes' }[nombre] || null;
}
