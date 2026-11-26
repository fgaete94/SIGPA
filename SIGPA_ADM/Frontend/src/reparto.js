// Historia #85: la ruta en curso son los pedidos confirmados o en despacho que tienen orden_entrega
// (el backend no guarda una tabla de rutas: el orden y el viaje viven en cada pedido).
// Para seguir el avance, los pedidos resueltos hoy (entregados o no) siguen en la ruta, en su lugar y marcados.
import { resuelta } from './estados.js';
import { aFecha, diaChile, horaCorta, hoyChile } from './fechas.js';
import { agruparPorViaje, ordenViaje } from './viajes.js';

const EN_CURSO = ['confirmado', 'en_despacho'];

export function rutaEnCurso(pedidos, clientes = [], hoy = hoyChile()) {
  const direccionCliente = new Map(clientes.map(c => [c.id, c.direccion]));
  const activos = pedidos.filter(p => EN_CURSO.includes(p.estado) && p.orden_entrega != null);
  // Sin id de ruta, lo resuelto hoy es lo que se considera de esta ruta. Si ya se planificó otra el mismo día, su parada con ese número manda.
  const lugar = p => `${p.viaje ?? ''}|${p.orden_entrega}`;
  const ocupados = new Set(activos.map(lugar));
  const momento = p => p.resultado_en ?? p.actualizado_en;
  const resueltosHoy = pedidos.filter(p => resuelta(p) && p.orden_entrega != null && momento(p) && diaChile(momento(p)) === hoy && !ocupados.has(lugar(p)));
  const paradas = [...activos, ...resueltosHoy]
    // Igual que el backend al planificar: la dirección del pedido y, si no tiene, la del cliente.
    .map(p => ({ ...p, direccion: p.direccion_despacho || direccionCliente.get(p.cliente_id) || null }))
    .sort((a, b) => ordenViaje(a, b) || a.id - b.id);
  // Cada planificación numera desde 1 en cada viaje: un número repetido dentro de un viaje indica pedidos de más de una ruta.
  const repetidos = agruparPorViaje(paradas.filter(p => !resuelta(p))).flatMap(({ viaje, paradas: delViaje }) => {
    const numeros = delViaje.map(p => p.orden_entrega);
    const r = [...new Set(numeros.filter((n, i) => numeros.indexOf(n) !== i))];
    return r.length ? [{ viaje, numeros: r }] : [];
  });
  const sinUbicacion = pedidos.filter(p => p.estado === 'confirmado' && p.orden_entrega == null && p.motivo_revision_direccion).length;
  return { paradas, repetidos, sinUbicacion };
}

// Avance de la ruta: cuántas paradas van en cada estado y los montos entregado y por entregar.
export function resumenEntregas(paradas) {
  const suma = lista => lista.reduce((t, p) => t + (Number(p.total) || 0), 0);
  const entregadas = paradas.filter(p => p.estado === 'entregado');
  const porEntregar = paradas.filter(p => !resuelta(p));
  return { total: paradas.length, entregadas: entregadas.length, noEntregadas: paradas.length - entregadas.length - porEntregar.length, porEntregar: porEntregar.length, montoEntregado: suma(entregadas), montoPorEntregar: suma(porEntregar) };
}

const METODO_PAGO = { efectivo: 'Efectivo', transferencia: 'Transferencia' };
const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// "✓ Entregado · Efectivo · 14:32", "✓ Entrega ajustada · $5.000 · Efectivo · 14:32" o "✗ No entregado · 15:10". null si la parada sigue pendiente.
export function textoResultado(p) {
  if (!resuelta(p)) return null;
  const momento = p.resultado_en ?? p.actualizado_en;
  const hora = momento && horaCorta(aFecha(momento));
  return (p.estado === 'entregado' ? [p.entrega_ajustada ? `✓ Entrega ajustada · ${dinero(p.total)}` : '✓ Entregado', METODO_PAGO[p.medio_pago], hora] : ['✗ No entregado', hora]).filter(Boolean).join(' · ');
}

const tieneCoordenadas = p => p.latitud != null && p.longitud != null;

// Indicaciones en Google Maps desde la ubicación actual del repartidor hasta la parada.
export function enlaceComoLlegar(parada) {
  const destino = tieneCoordenadas(parada) ? `${parada.latitud},${parada.longitud}` : parada.direccion ? `${parada.direccion}, Chile` : null;
  return destino && `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(destino)}`;
}

// "Cómo llegar" abre Google Maps y el repartidor sale del panel. Se recuerda a qué parada fue para
// mostrársela destacada al volver. sessionStorage: dura mientras la pestaña siga abierta.
const CLAVE_EN_CAMINO = 'sigpa.repartidor.enCamino';

export function leerEnCamino(almacen = globalThis.sessionStorage) {
  try { const id = Number(almacen?.getItem(CLAVE_EN_CAMINO)); return id > 0 ? id : null; } catch { return null; }
}

export function guardarEnCamino(id, almacen = globalThis.sessionStorage) {
  try { id ? almacen?.setItem(CLAVE_EN_CAMINO, String(id)) : almacen?.removeItem(CLAVE_EN_CAMINO); } catch { /* sin almacenamiento: solo no se recuerda */ }
}

// La parada a la que fue, mientras siga en la ruta y sin marcar. Ya marcada (o fuera de la ruta), null.
export function paradaEnCamino(paradas, id) {
  const p = id ? paradas.find(x => x.id === id) : null;
  return p && !resuelta(p) ? p : null;
}

// Estado de una ruta (viaje) en la vista del repartidor, para su encabezado: terminada, en curso (tiene
// la siguiente parada) o por hacer.
export function estadoRuta(paradas, siguiente) {
  const listas = paradas.filter(resuelta).length;
  const total = paradas.length;
  if (total && listas === total) return { listas, total, clase: 'terminada', texto: 'Terminada' };
  if (siguiente && paradas.some(p => p.id === siguiente.id)) return { listas, total, clase: 'en-curso', texto: 'En curso' };
  return { listas, total, clase: 'por-hacer', texto: 'Por hacer' };
}
