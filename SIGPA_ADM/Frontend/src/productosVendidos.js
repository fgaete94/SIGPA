// Historia #72: ranking de productos más vendidos (GET /reportes/productos) con el período de Reportes.
export const MODOS = [
  ['entregados', 'Vendido (pedidos entregados)'],
  ['pedidos', 'Pedido (todos menos cancelados)'],
];

// Query string para el período elegido; sin fechas el backend toma todo el historial.
export function parametrosRanking({ desde = '', hasta = '' } = {}, contar = 'entregados') {
  const p = new URLSearchParams({ contar });
  if (desde) p.set('desde', desde);
  if (hasta) p.set('hasta', hasta);
  return p.toString();
}

// Agrega la posición y la participación en unidades; el backend ya entrega de mayor a menor.
export function rankingConParticipacion(filas = []) {
  const total = filas.reduce((s, f) => s + f.unidades, 0);
  const maximo = Math.max(0, ...filas.map(f => f.unidades));
  return {
    total,
    filas: filas.map((f, i) => ({ ...f, posicion: i + 1, porcentaje: total ? f.unidades / total : 0, relativo: maximo ? f.unidades / maximo : 0 })),
  };
}

const celda = v => (/[";\n]/.test(String(v)) ? `"${String(v).replaceAll('"', '""')}"` : String(v));

// CSV con punto y coma (lo abre bien Excel en español) y montos sin formato para poder sumarlos.
export function csvRanking(filas, { desde = '', hasta = '' } = {}, contar = 'entregados') {
  const periodo = desde || hasta ? `${desde || 'inicio'} a ${hasta || 'hoy'}` : 'todo el historial';
  const encabezado = [`Productos más vendidos (${MODOS.find(([m]) => m === contar)?.[1] || contar}), ${periodo}`];
  const columnas = ['Posición', 'Producto', 'Unidades', 'Monto (CLP)', 'Pedidos', 'Participación (%)'];
  const lineas = filas.map(f => [f.posicion, f.producto_nombre, f.unidades, Math.round(f.monto), f.pedidos, (f.porcentaje * 100).toFixed(1).replace('.', ',')]);
  return [encabezado, columnas, ...lineas].map(l => l.map(celda).join(';')).join('\r\n');
}

export const nombreArchivo = ({ desde = '', hasta = '' } = {}) => `productos-mas-vendidos${desde ? `_${desde}` : ''}${hasta ? `_${hasta}` : ''}.csv`;
