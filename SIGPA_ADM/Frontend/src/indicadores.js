import { diaChile, sumarDias } from './fechas.js';

export const SIN_COMUNA = 'Sin comuna asignada';
export const PERIODOS = [['7', 'Últimos 7 días'], ['30', 'Últimos 30 días'], ['mes', 'Este mes'], ['todo', 'Todo el historial'], ['otro', 'Personalizado']];

// Rango { desde, hasta } ('AAAA-MM-DD', incluidos) de un período predefinido; '' = sin límite.
export function rangoPeriodo(periodo, hoy) {
  if (periodo === '7') return { desde: sumarDias(hoy, -6), hasta: hoy };
  if (periodo === '30') return { desde: sumarDias(hoy, -29), hasta: hoy };
  if (periodo === 'mes') return { desde: `${hoy.slice(0, 8)}01`, hasta: hoy };
  return { desde: '', hasta: '' };
}

// Historia #73: pedidos agrupados por la comuna del cliente, de mayor a menor demanda.
// Los cancelados no cuentan como demanda. El día del pedido se toma en hora de Chile.
export function pedidosPorComuna(pedidos, { desde = '', hasta = '' } = {}) {
  const grupos = new Map();
  let total = 0;
  for (const p of pedidos) {
    if (p.estado === 'cancelado') continue;
    const dia = diaChile(p.creado_en);
    if ((desde && dia < desde) || (hasta && dia > hasta)) continue;
    const comuna = p.comuna_nombre || SIN_COMUNA;
    if (!grupos.has(comuna)) grupos.set(comuna, { comuna, pedidos: 0, clientes: new Set() });
    const grupo = grupos.get(comuna);
    grupo.pedidos += 1;
    grupo.clientes.add(p.cliente_id);
    total += 1;
  }
  const filas = [...grupos.values()]
    .map(g => ({ comuna: g.comuna, pedidos: g.pedidos, clientes: g.clientes.size, porcentaje: g.pedidos / total }))
    // "Sin comuna" siempre al final: no es una zona de reparto.
    .sort((a, b) => (a.comuna === SIN_COMUNA) - (b.comuna === SIN_COMUNA) || b.pedidos - a.pedidos || a.comuna.localeCompare(b.comuna, 'es'));
  return { total, filas };
}
