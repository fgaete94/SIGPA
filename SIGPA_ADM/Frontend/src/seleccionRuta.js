// Historia #81: armar la ruta por día de reparto y comuna. GET /rutas/pedidos-pendientes no trae el día
// ni la comuna del cliente: se cruzan con /clientes que el panel ya cargó.
import { SIN_COMUNA, SIN_DIA } from './agrupacionDespacho.js';

const DIAS = ['Lunes', 'Martes', 'Miercoles', 'Jueves', 'Viernes'];
const textos = new Intl.Collator('es', { sensitivity: 'base' });
const posicionDia = d => (d === SIN_DIA ? DIAS.length : DIAS.indexOf(d));

// Sin coordenadas, n8n tiene que buscar la dirección al calcular (puede no encontrarla y tarda más).
export function enriquecerPendientes(pendientes, clientes = []) {
  const porId = new Map(clientes.map(c => [c.id, c]));
  return pendientes.map(p => {
    const cliente = porId.get(p.cliente_id);
    return {
      ...p,
      dia: DIAS.includes(cliente?.dia_reparto) ? cliente.dia_reparto : SIN_DIA,
      comuna: cliente?.comuna_nombre || SIN_COMUNA,
      sinUbicacion: p.latitud == null || p.longitud == null,
    };
  });
}

// Días con su cantidad; comunas con su cantidad dentro del día elegido.
export function opcionesSeleccion(lista, dia = '') {
  const contar = (filas, clave) => [...filas.reduce((m, p) => m.set(clave(p), (m.get(clave(p)) || 0) + 1), new Map())];
  const dias = contar(lista, p => p.dia).sort(([a], [b]) => posicionDia(a) - posicionDia(b));
  const comunas = contar(lista.filter(p => !dia || p.dia === dia), p => p.comuna)
    .sort(([a], [b]) => (a === SIN_COMUNA) - (b === SIN_COMUNA) || textos.compare(a, b));
  return { dias, comunas };
}

export const filtrarPendientes = (lista, { dia = '', comuna = '' } = {}) =>
  lista.filter(p => (!dia || p.dia === dia) && (!comuna || p.comuna === comuna));

// Marca los visibles sin pasar del máximo. Los que tienen la dirección por revisar se dejan fuera:
// sin corregir su ubicación volverían a quedar sin resolver. Tampoco los sin peso calculable
// (peso_kg null): el backend rechaza la ruta entera si va uno.
export function marcarVisibles(seleccion, visibles, maximo) {
  const ids = [...seleccion];
  let porRevisar = 0;
  let sinCupo = 0;
  let sinPeso = 0;
  for (const p of visibles) {
    if (ids.includes(p.pedido_id)) continue;
    if (p.motivo_revision_direccion && p.sinUbicacion) { porRevisar += 1; continue; }
    if (p.peso_kg === null) { sinPeso += 1; continue; }
    if (ids.length >= maximo) { sinCupo += 1; continue; }
    ids.push(p.pedido_id);
  }
  return { ids, agregados: ids.length - seleccion.length, porRevisar, sinCupo, sinPeso };
}

export const desmarcarVisibles = (seleccion, visibles) => seleccion.filter(id => !visibles.some(p => p.pedido_id === id));

// n8n busca cada dirección sin coordenadas de a una (~1,5 s) y se corta a los 40 s.
export const LIMITE_SIN_UBICACION = 20;
export const sinUbicacionEn = (lista, ids) => lista.filter(p => ids.includes(p.pedido_id) && p.sinUbicacion).length;
