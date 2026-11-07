// Mapa de la ruta (vista previa y ruta en curso): lógica que no depende de Leaflet ni de React.
import { resuelta } from './estados.js';
import { textoResultado } from './reparto.js';

// Sin verde ni rojo, que marcan la primera y la última parada. MAX_VIAJES es 3.
export const COLORES_VIAJE = ['#2f6fd6', '#d9730d', '#8a3fc4'];

export const tieneCoordenadas = p => Number.isFinite(p.latitud) && Number.isFinite(p.longitud);

// La próxima parada por resolver: la primera de la lista sin resultado (null si ya están todas).
export const siguienteParada = grupos => grupos.flatMap(g => g.paradas).find(p => !resuelta(p)) ?? null;

// grupos: agruparPorViaje (en la vista previa traen además "numero"). Cada capa es un viaje con sus puntos.
// El número de cada punto es su posición en el viaje, igual que en la lista, aunque otra parada no tenga
// coordenadas. Los pedidos de la vista previa traen pedido_id y direccion_texto; los guardados, id y direccion.
// desfase: cuántos puntos anteriores caen en el mismo lugar (varios clientes en una dirección), para no taparlos.
// avance: marca lo entregado y la siguiente parada en vez de la primera y la última (ruta en curso).
export function capasMapa(grupos, { avance = false } = {}) {
  const enLugar = new Map();
  const siguiente = avance ? siguienteParada(grupos) : null;
  return grupos.map((g, i) => {
    const ultimo = g.paradas.length - 1;
    const puntos = g.paradas.flatMap((p, k) => {
      if (!tieneCoordenadas(p)) return [];
      const lugar = `${p.latitud.toFixed(5)},${p.longitud.toFixed(5)}`;
      const desfase = enLugar.get(lugar) ?? 0;
      enLugar.set(lugar, desfase + 1);
      const id = p.pedido_id ?? p.id;
      return [{
        id, numero: k + 1, latitud: p.latitud, longitud: p.longitud, desfase,
        tipo: avance ? (p.estado === 'entregado' ? 'entregada' : p.estado === 'no_entregado' ? 'no-entregada' : p === siguiente ? 'siguiente' : '') : k === 0 ? 'inicio' : k === ultimo ? 'fin' : '',
        nombre: p.cliente_nombre || `Pedido #${id}`,
        direccion: p.direccion_texto ?? p.direccion ?? null,
        resultado: textoResultado(p),
      }];
    });
    return { clave: g.viaje ?? 'sin-viaje', etiqueta: g.viaje == null ? 'Sin ruta' : `Ruta ${g.numero ?? g.viaje}`, color: COLORES_VIAJE[i % COLORES_VIAJE.length], total: g.paradas.length, puntos };
  });
}

// Cambia si entran o salen paradas, no si solo cambia su orden: reordenar no debe mover el encuadre del mapa.
export const claveEncuadre = capas => capas.flatMap(c => c.puntos.map(p => p.id)).sort((a, b) => a - b).join(',');

export const sinCoordenadas = capas => capas.reduce((suma, c) => suma + c.total - c.puntos.length, 0);

// Solo el viaje elegido, si sigue teniendo puntos (al sacar paradas un viaje puede quedar vacío).
export function capasVisibles(capas, solo) {
  const elegida = capas.filter(c => c.clave === solo && c.puntos.length);
  return elegida.length ? elegida : capas;
}
