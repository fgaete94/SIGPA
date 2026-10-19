// Historia #102: resumen de la ficha del cliente a partir de su historial (GET /clientes/{id} → pedidos,
// del más reciente al más antiguo). "Comprado" es lo entregado, igual que Ventas en Reportes.
import { aFecha } from './fechas.js';

const EN_CURSO = ['pendiente', 'confirmado', 'en_despacho'];

export function resumenCliente(pedidos = []) {
  const ordenados = [...pedidos].sort((a, b) => aFecha(b.creado_en) - aFecha(a.creado_en) || b.id - a.id);
  const entregados = ordenados.filter(p => p.estado === 'entregado');
  const comprado = entregados.reduce((suma, p) => suma + Number(p.total || 0), 0);
  return {
    pedidos: ordenados,
    cantidad: ordenados.length,
    entregados: entregados.length,
    enCurso: ordenados.filter(p => EN_CURSO.includes(p.estado)).length,
    cancelados: ordenados.filter(p => p.estado === 'cancelado').length,
    comprado,
    promedio: entregados.length ? Math.round(comprado / entregados.length) : null,
    ultimo: ordenados[0] || null,
    ultimaEntrega: entregados[0] || null,
  };
}

// Tipo de cliente con su equivalente comercial (la tarjeta habla de B2C/B2B).
export function textoTipo(nombre) {
  if (!nombre) return 'Sin tipo';
  const equivalente = { particular: 'B2C', empresa: 'B2B' }[nombre.toLowerCase()];
  return equivalente ? `${nombre} (${equivalente})` : nombre;
}
