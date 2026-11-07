// Filtros de la pestaña Pedidos: cliente, comuna y día de reparto.
import { DIAS_REPARTO } from './clientes.js';
import { sumarDias } from './fechas.js';
import { SIN_COMUNA } from './indicadores.js';

const sinAcentos = texto => String(texto ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
export const comunaDe = p => p.comuna_nombre || SIN_COMUNA;
// La pestaña Pedidos abre con los pendientes y permite consultar los no entregados. Los cancelados se informan en Reportes.
export const VISTAS_PEDIDOS = [['pendiente', 'Pendientes'], ['no_entregado', 'No entregados']];
export const VISTA_INICIAL = VISTAS_PEDIDOS[0][0];
export const SIN_DIA = 'sin_dia';
export const diaDe = p => p.dia_reparto || SIN_DIA;
const posicionDia = dia => { const i = DIAS_REPARTO.findIndex(([v]) => v === dia); return i < 0 ? DIAS_REPARTO.length : i; };
export const nombreDia = dia => (dia === SIN_DIA ? 'Sin día de reparto' : DIAS_REPARTO.find(([v]) => v === dia)?.[1] || dia);

const cumple = (p, { texto = '', estado = '', comuna = '', dia = '' }) => {
  if (estado && p.estado !== estado) return false;
  if (comuna && comunaDe(p) !== comuna) return false;
  if (dia && diaDe(p) !== dia) return false;
  return sinAcentos(`${p.id} ${p.cliente_nombre || ''}`).includes(sinAcentos(texto).trim());
};

export const filtrarPedidos = (pedidos, filtros) => pedidos.filter(p => cumple(p, filtros));

// Un pedido no entregado se reagenda para el siguiente día con reparto (lunes a viernes).
export function siguienteDiaReparto(hoy) {
  let dia = sumarDias(hoy, 1);
  while ([0, 6].includes(new Date(`${dia}T12:00:00Z`).getUTCDay())) dia = sumarDias(dia, 1);
  return dia;
}

export const totalDe = pedidos => pedidos.reduce((suma, p) => suma + (Number(p.total) || 0), 0);

function contar(pedidos, clave) {
  const cuenta = new Map();
  for (const p of pedidos) cuenta.set(clave(p), (cuenta.get(clave(p)) ?? 0) + 1);
  return cuenta;
}

// Cuántos pedidos hay en cada vista con los demás filtros aplicados.
export const cuentaVistas = (pedidos, filtros) => contar(filtrarPedidos(pedidos, { ...filtros, estado: '' }), p => p.estado);

// Cuántos pedidos da cada opción con los demás filtros ya aplicados. La comuna y el día elegidos se mantienen aunque queden en 0.
export function opcionesFiltros(pedidos, filtros) {
  const comunas = contar(filtrarPedidos(pedidos, { ...filtros, comuna: '' }), comunaDe);
  if (filtros.comuna && !comunas.has(filtros.comuna)) comunas.set(filtros.comuna, 0);
  const dias = contar(filtrarPedidos(pedidos, { ...filtros, dia: '' }), diaDe);
  if (filtros.dia && !dias.has(filtros.dia)) dias.set(filtros.dia, 0);
  const orden = ([a], [b]) => (a === SIN_COMUNA) - (b === SIN_COMUNA) || a.localeCompare(b, 'es');
  return { comunas: [...comunas].sort(orden), dias: [...dias].sort(([a], [b]) => posicionDia(a) - posicionDia(b)) };
}
