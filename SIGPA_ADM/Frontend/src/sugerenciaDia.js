import { DIAS_REPARTO } from './clientes.js';

// Historia #93: sugerencia del día de reparto según el sector del cliente o, si no tiene sector, su comuna
// (GET /clientes/sugerencia-dia). Al editar, el propio cliente se excluye para que su día actual no cuente.
export function rutaSugerencia(form, clienteId) {
  const params = new URLSearchParams();
  if (form.sector_id) params.set('sector_id', form.sector_id);
  else if (form.comuna_id) params.set('comuna_id', form.comuna_id);
  else return null;
  if (clienteId != null) params.set('excluir_cliente_id', String(clienteId));
  return `/clientes/sugerencia-dia?${params}`;
}

const nombreDia = valor => DIAS_REPARTO.find(([v]) => v === valor)?.[1] || valor;
const clientes = n => `${n} ${n === 1 ? 'cliente' : 'clientes'}`;

// Texto que acompaña al selector de día. null si todavía no hay sugerencia que mostrar.
export function textoSugerencia(sugerencia) {
  if (!sugerencia) return null;
  const lugar = sugerencia.base === 'sector' ? `del sector ${sugerencia.base_nombre}` : `de ${sugerencia.base_nombre}`;
  if (sugerencia.dia_reparto) {
    return `Sugerido: ${nombreDia(sugerencia.dia_reparto)} (${sugerencia.clientes_con_ese_dia} de ${clientes(sugerencia.clientes_con_dia)} ${lugar}).`;
  }
  if (!sugerencia.clientes_con_dia) return `Sin sugerencia: ningún cliente ${lugar} tiene día asignado.`;
  return `Sin sugerencia: los clientes ${lugar} tienen días distintos.`;
}