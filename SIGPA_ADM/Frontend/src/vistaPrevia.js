// Historia #87: la ruta que propone n8n se revisa antes de guardarla. La ejecutiva puede cambiar el
// orden (horarios, un accidente, una calle cortada) o sacar una parada para la próxima ruta; recién al
// confirmar se guarda con POST /rutas/confirmar. Nada de esto toca la base mientras se revisa.
// EP-04: la ruta viene repartida en viajes por peso. Se puede reordenar dentro de un viaje y pasar una
// parada a otro viaje; el peso de cada viaje se recalcula aquí y el backend lo vuelve a validar al confirmar.
import { moverA } from './route.js';
import { agruparPorViaje, ordenViaje, problemasViajes, reemplazarViaje, terminaEn } from './viajes.js';

const viajeDe = p => p.viaje ?? null;

// respuesta: POST /rutas/planificar con vista_previa (ruta con coordenadas, version y viaje; viajes; sin_resolver).
// pesos: peso_kg de cada pedido por id (viene en /rutas/pedidos-pendientes; la ruta no lo trae).
// Las paradas quedan en una sola lista ordenada por viaje y, dentro de cada viaje, por orden_entrega.
export function estadoVistaPrevia(respuesta, pesos = {}) {
  const propuesta = [...(respuesta.ruta || [])].sort(ordenViaje).map(p => ({ ...p, peso_kg: p.peso_kg ?? pesos[p.pedido_id] ?? null }));
  return { propuesta, paradas: propuesta, fuera: [], sinResolver: respuesta.sin_resolver || [], viajes: respuesta.viajes || [] };
}

// Arrastrar cambia el orden dentro de un viaje (desde y hasta son posiciones dentro de ese viaje).
export function moverParada(estado, viaje, desde, hasta) {
  const delViaje = estado.paradas.filter(p => viajeDe(p) === viaje);
  return { ...estado, paradas: reemplazarViaje(estado.paradas, viaje, moverA(delViaje, desde, hasta)) };
}

// Pasa una parada al final de otro viaje (sort es estable: las demás no se mueven).
export function cambiarViaje(estado, pedidoId, viaje) {
  const parada = estado.paradas.find(p => p.pedido_id === pedidoId);
  if (!parada || viajeDe(parada) === viaje) return estado;
  const resto = estado.paradas.filter(p => p !== parada);
  return { ...estado, paradas: [...resto, { ...parada, viaje }].sort((a, b) => (viajeDe(a) ?? Infinity) - (viajeDe(b) ?? Infinity)) };
}

export function sacar(estado, pedidoId) {
  const parada = estado.paradas.find(p => p.pedido_id === pedidoId);
  if (!parada) return estado;
  return { ...estado, paradas: estado.paradas.filter(p => p !== parada), fuera: [...estado.fuera, parada] };
}

// Vuelve a la ruta como última parada de su viaje.
export function reponer(estado, pedidoId) {
  const parada = estado.fuera.find(p => p.pedido_id === pedidoId);
  if (!parada) return estado;
  const viaje = p => viajeDe(p) ?? Infinity;
  return { ...estado, fuera: estado.fuera.filter(p => p !== parada), paradas: [...estado.paradas, parada].sort((a, b) => viaje(a) - viaje(b)) };
}

export const restablecer = estado => ({ ...estado, paradas: estado.propuesta, fuera: [] });

export const cambiada = estado => estado.fuera.length > 0 || estado.paradas.some((p, i) => p.pedido_id !== estado.propuesta[i]?.pedido_id || viajeDe(p) !== viajeDe(estado.propuesta[i]));

// Viajes para mostrar y confirmar. Si un viaje quedó vacío, los siguientes se renumeran (el backend exige
// viajes 1..V seguidos): "numero" es el que se muestra y se envía; "viaje" sigue siendo la clave del grupo.
// Con el peso de todas las paradas se recalculan peso, destino y problemas; si falta alguno y la ruta no
// cambió, se usa lo que informó el backend.
export function viajesVista(estado) {
  const grupos = agruparPorViaje(estado.paradas, estado.viajes).map((g, i) => ({ ...g, numero: g.viaje == null ? null : i + 1 }));
  const conPeso = estado.paradas.every(p => Number.isFinite(p.peso_kg));
  if (!conPeso) {
    const intacta = !cambiada(estado);
    return { grupos: grupos.map(g => ({ ...g, peso_kg: intacta ? g.peso_kg : null, termina_en: intacta ? g.termina_en : null })), problemas: [] };
  }
  const pesos = grupos.map(g => g.paradas.reduce((suma, p) => suma + p.peso_kg, 0));
  const destinos = terminaEn(pesos);
  return {
    grupos: grupos.map((g, i) => ({ ...g, peso_kg: pesos[i], termina_en: destinos[i] })),
    problemas: grupos.some(g => g.viaje == null) ? [] : problemasViajes(pesos),
  };
}

// Los sacados no se envían: siguen pendientes y vuelven a "Preparar ruta". Los sin resolver se confirman
// con su motivo para que queden como "Dirección por revisar" (igual que al planificar directo).
// Cada parada va con su viaje; su posición en la lista es su orden dentro del viaje.
export function datosConfirmar(estado) {
  const numero = new Map(viajesVista(estado).grupos.map(g => [g.viaje, g.numero]));
  return {
    paradas: estado.paradas.map(({ pedido_id, viaje = null, latitud, longitud, version }) => ({ pedido_id, ...(viaje != null && { viaje: numero.get(viaje) }), latitud, longitud, version })),
    sin_resolver: estado.sinResolver.map(({ pedido_id, motivo, version }) => ({ pedido_id, motivo, version })),
  };
}

export const puedeConfirmar = estado => estado.paradas.length + estado.sinResolver.length > 0 && !viajesVista(estado).problemas.length;

// Para revisar en el mapa dónde ubicó n8n cada parada.
export const enlacePunto = p => `https://www.google.com/maps/search/?api=1&query=${p.latitud},${p.longitud}`;
