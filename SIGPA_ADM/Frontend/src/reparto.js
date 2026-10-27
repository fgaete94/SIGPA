// Historia #85: la ruta en curso son los pedidos confirmados o en despacho que tienen orden_entrega
// (el backend no guarda una tabla de rutas: el orden y el viaje viven en cada pedido).
import { agruparPorViaje, encabezadoViaje, ordenViaje } from './viajes.js';

const EN_CURSO = ['confirmado', 'en_despacho'];

export function rutaEnCurso(pedidos, clientes = []) {
  const direccionCliente = new Map(clientes.map(c => [c.id, c.direccion]));
  const paradas = pedidos
    .filter(p => EN_CURSO.includes(p.estado) && p.orden_entrega != null)
    // Igual que el backend al planificar: la dirección del pedido y, si no tiene, la del cliente.
    .map(p => ({ ...p, direccion: p.direccion_despacho || direccionCliente.get(p.cliente_id) || null }))
    .sort((a, b) => ordenViaje(a, b) || a.id - b.id);
  // Cada planificación numera desde 1 en cada viaje: un número repetido dentro de un viaje indica pedidos de más de una ruta.
  const repetidos = agruparPorViaje(paradas).flatMap(({ viaje, paradas: delViaje }) => {
    const numeros = delViaje.map(p => p.orden_entrega);
    const r = [...new Set(numeros.filter((n, i) => numeros.indexOf(n) !== i))];
    return r.length ? [{ viaje, numeros: r }] : [];
  });
  const sinUbicacion = pedidos.filter(p => p.estado === 'confirmado' && p.orden_entrega == null && p.motivo_revision_direccion).length;
  return { paradas, repetidos, sinUbicacion };
}

const tieneCoordenadas = p => p.latitud != null && p.longitud != null;

// Indicaciones en Google Maps desde la ubicación actual del repartidor hasta la parada.
export function enlaceComoLlegar(parada) {
  const destino = tieneCoordenadas(parada) ? `${parada.latitud},${parada.longitud}` : parada.direccion ? `${parada.direccion}, Chile` : null;
  return destino && `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(destino)}`;
}

// Texto para enviar al repartidor por WhatsApp o copiar: una parada por bloque, con su enlace.
// Con viajes, cada uno lleva su encabezado y numera sus paradas desde 1.
export function textoRuta(paradas, fecha, viajes = []) {
  const bloques = agruparPorViaje(paradas, viajes).flatMap(v => [encabezadoViaje(v) && `*${encabezadoViaje(v)}*`, ...v.paradas.map((p, i) => [
    `${i + 1}. ${p.cliente_nombre || `Cliente ${p.cliente_id}`} (pedido #${p.id})`,
    p.direccion || 'Sin dirección registrada',
    p.cliente_telefono && `Tel: ${p.cliente_telefono}`,
    enlaceComoLlegar(p),
  ].filter(Boolean).join('\n'))]).filter(Boolean);
  return [`Ruta Agua DM · ${fecha} · ${paradas.length} ${paradas.length === 1 ? 'parada' : 'paradas'}`, ...bloques].join('\n\n');
}

export const enlaceWhatsApp = texto => `https://wa.me/?text=${encodeURIComponent(texto)}`;
