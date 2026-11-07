// Índices y gráficos de la pestaña Resumen para un tramo de fechas, en hora de Chile. Un pedido cuenta el día en que se resolvió.
import { diaChile, sumarDias } from './fechas.js';

const CORTOS = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
// Con más días que esto, las barras por día serían ilegibles: se agrupa por semana.
export const MAX_DIAS_POR_DIA = 45;
// Sobre esta cantidad de barras el gráfico usa una separación menor.
export const MAX_BARRAS_CON_VALOR = 14;
const semanaDel = dia => (new Date(`${dia}T12:00:00Z`).getUTCDay() + 6) % 7;
const diaMes = dia => `${Number(dia.slice(8))} ${MESES[Number(dia.slice(5, 7)) - 1]}`;
const momento = p => p.resultado_en ?? p.actualizado_en;

// Un período sin límite en Reportes ("todo el historial"): parte con el primer pedido resuelto y llega hasta hoy.
// Pedidos cancelados en un período (sin límite si falta una fecha): cuántos y por cuánto.
export function canceladosEnPeriodo(pedidos, { desde = '', hasta = '' }) {
  const cancelados = pedidos.filter(p => {
    if (p.estado !== 'cancelado' || !momento(p)) return false;
    const dia = diaChile(momento(p));
    return (!desde || dia >= desde) && (!hasta || dia <= hasta);
  });
  return { cantidad: cancelados.length, monto: cancelados.reduce((suma, p) => suma + (Number(p.total) || 0), 0) };
}

export function tramoEfectivo(pedidos, { desde = '', hasta = '' }, hoy) {
  if (desde && hasta) return { desde, hasta };
  const resueltos = pedidos.filter(p => (p.estado === 'entregado' || p.estado === 'no_entregado') && momento(p)).map(p => diaChile(momento(p)));
  return { desde: desde || resueltos.reduce((min, dia) => (dia < min ? dia : min), hoy), hasta: hasta || hoy };
}

export function diasEntre(desde, hasta) {
  const dias = [];
  for (let dia = desde; dia <= hasta; dia = sumarDias(dia, 1)) dias.push(dia);
  return dias;
}

export const lunesDe = dia => sumarDias(dia, -semanaDel(dia));

function etiquetaDia(dia, conDiaSemana) {
  return conDiaSemana ? `${CORTOS[new Date(`${dia}T12:00:00Z`).getUTCDay()]} ${Number(dia.slice(8))}` : diaMes(dia);
}

// filas: una por día, o una por semana si el tramo es largo. Lo vendido es lo entregado.
export function resumenPeriodo(pedidos, { desde, hasta }) {
  const dias = diasEntre(desde, hasta);
  const porSemana = dias.length > MAX_DIAS_POR_DIA;
  const claveDe = dia => (porSemana ? lunesDe(dia) : dia);
  const grupos = new Map();
  for (const dia of dias) {
    const clave = claveDe(dia);
    if (!grupos.has(clave)) grupos.set(clave, { clave, etiqueta: porSemana ? `Sem. ${diaMes(dia)}` : etiquetaDia(dia, dias.length <= 14), monto: 0, entregados: 0, noEntregados: 0 });
  }
  const pagos = { efectivo: 0, transferencia: 0, sinDato: 0 };
  for (const p of pedidos) {
    if ((p.estado !== 'entregado' && p.estado !== 'no_entregado') || !momento(p)) continue;
    const dia = diaChile(momento(p));
    if (dia < desde || dia > hasta) continue;
    const fila = grupos.get(claveDe(dia));
    if (p.estado === 'no_entregado') { fila.noEntregados += 1; continue; }
    const total = Number(p.total) || 0;
    fila.entregados += 1;
    fila.monto += total;
    pagos[p.medio_pago === 'efectivo' || p.medio_pago === 'transferencia' ? p.medio_pago : 'sinDato'] += total;
  }
  const filas = [...grupos.values()];
  const sumar = campo => filas.reduce((suma, f) => suma + f[campo], 0);
  return { filas, porSemana, total: sumar('monto'), entregados: sumar('entregados'), noEntregados: sumar('noEntregados'), pagos };
}
