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

export const AGRUPACIONES = [['dia', 'Día'], ['semana', 'Semana'], ['mes', 'Mes']];
export const SIN_TIPO = 'sin-tipo';
export const SIN_SECTOR = 'sin_sector';
const EN_CURSO = ['pendiente', 'confirmado', 'en_despacho'];

const aUTC = clave => { const [a, m, d = 1] = clave.split('-').map(Number); return new Date(Date.UTC(a, m - 1, d)); };
const formatoDia = new Intl.DateTimeFormat('es-CL', { timeZone: 'UTC', day: 'numeric', month: 'short' });
const formatoMes = new Intl.DateTimeFormat('es-CL', { timeZone: 'UTC', month: 'short', year: 'numeric' });

// Clave del grupo de un día 'AAAA-MM-DD': el mismo día, el lunes de su semana o 'AAAA-MM'.
export function claveGrupo(dia, agrupar) {
  if (agrupar === 'semana') return sumarDias(dia, -((aUTC(dia).getUTCDay() + 6) % 7));
  if (agrupar === 'mes') return dia.slice(0, 7);
  return dia;
}

export function etiquetaGrupo(clave, agrupar) {
  if (agrupar === 'mes') return formatoMes.format(aUTC(clave));
  return `${agrupar === 'semana' ? 'Sem. ' : ''}${formatoDia.format(aUTC(clave))}`;
}

// Sectores de los clientes, para el filtro de ventas: solo los que tienen clientes y, si hay comuna elegida, los de esa comuna.
export function opcionesSector(clientes, sectores, comuna = '') {
  const usados = new Set(clientes.map(c => c.sector_id).filter(id => id != null));
  const lista = sectores
    .filter(s => usados.has(s.id) && (!comuna || s.comuna_nombre === comuna))
    .sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'))
    .map(s => [String(s.id), s.nombre]);
  const sinSector = clientes.some(c => c.sector_id == null && (!comuna || (c.comuna_nombre || SIN_COMUNA) === comuna));
  return sinSector ? [...lista, [SIN_SECTOR, 'Sin sector']] : lista;
}

// Opciones de los filtros de ventas: comunas presentes en los pedidos y tipos de cliente.
export function opcionesVentas(pedidos, clientes) {
  const comunas = [...new Set(pedidos.map(p => p.comuna_nombre || SIN_COMUNA))]
    .sort((a, b) => (a === SIN_COMUNA) - (b === SIN_COMUNA) || a.localeCompare(b, 'es'));
  const tipos = new Map();
  let sinTipo = false;
  for (const c of clientes) {
    if (c.tipo_cliente_id == null) sinTipo = true;
    else tipos.set(String(c.tipo_cliente_id), c.tipo_cliente_nombre || `Tipo ${c.tipo_cliente_id}`);
  }
  const listaTipos = [...tipos].sort((a, b) => a[1].localeCompare(b[1], 'es'));
  if (sinTipo) listaTipos.push([SIN_TIPO, 'Sin tipo asignado']);
  return { comunas, tipos: listaTipos, tipoPorCliente: new Map(clientes.map(c => [c.id, c.tipo_cliente_id])), sectorPorCliente: new Map(clientes.map(c => [c.id, c.sector_id])) };
}

// Historia #71. Venta = pedido entregado: al registrar la entrega el backend recalcula el total con lo
// realmente entregado. Se agrupa por la fecha del pedido (el backend no guarda la fecha de entrega).
// "En curso" = pendientes, confirmados y en despacho del mismo filtro (monto solicitado, aún no vendido).
export function ventasPorPeriodo(pedidos, { desde = '', hasta = '', agrupar = 'dia', comuna = '', tipo = '', sector = '', tipoPorCliente = new Map(), sectorPorCliente = new Map() } = {}) {
  const grupos = new Map();
  const resumen = { monto: 0, pedidos: 0, enCurso: { pedidos: 0, monto: 0 } };
  let primero = '', ultimo = '';
  for (const p of pedidos) {
    if (comuna && (p.comuna_nombre || SIN_COMUNA) !== comuna) continue;
    if (tipo) {
      const t = tipoPorCliente.get(p.cliente_id);
      if (tipo === SIN_TIPO ? t != null : String(t) !== tipo) continue;
    }
    if (sector) {
      const s = sectorPorCliente.get(p.cliente_id);
      if (sector === SIN_SECTOR ? s != null : String(s) !== sector) continue;
    }
    const dia = diaChile(p.creado_en);
    if ((desde && dia < desde) || (hasta && dia > hasta)) continue;
    const total = Number(p.total) || 0;
    if (EN_CURSO.includes(p.estado)) { resumen.enCurso.pedidos += 1; resumen.enCurso.monto += total; continue; }
    if (p.estado !== 'entregado') continue;
    const clave = claveGrupo(dia, agrupar);
    const grupo = grupos.get(clave) || { monto: 0, pedidos: 0 };
    grupo.monto += total;
    grupo.pedidos += 1;
    grupos.set(clave, grupo);
    resumen.monto += total;
    resumen.pedidos += 1;
    if (!primero || dia < primero) primero = dia;
    if (!ultimo || dia > ultimo) ultimo = dia;
  }
  // Serie continua, con ceros, para que el gráfico muestre también los períodos sin ventas.
  const serie = [];
  const inicio = desde || primero, fin = hasta || ultimo;
  if (inicio && fin) {
    for (let dia = inicio; dia <= fin; dia = sumarDias(dia, 1)) {
      const clave = claveGrupo(dia, agrupar);
      if (serie.at(-1)?.clave !== clave) serie.push({ clave, etiqueta: etiquetaGrupo(clave, agrupar), ...(grupos.get(clave) || { monto: 0, pedidos: 0 }) });
    }
  }
  return { ...resumen, ticket: resumen.pedidos ? resumen.monto / resumen.pedidos : 0, serie };
}
