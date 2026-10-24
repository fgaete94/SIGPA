// Historia #87: la ruta que propone n8n se revisa antes de guardarla. La ejecutiva puede cambiar el
// orden (horarios, un accidente, una calle cortada) o sacar una parada para la próxima ruta; recién al
// confirmar se guarda con POST /rutas/confirmar. Nada de esto toca la base mientras se revisa.
import { moveStop, moverA } from './route.js';

// respuesta: POST /rutas/planificar con vista_previa (ruta con coordenadas y version, sin_resolver).
export function estadoVistaPrevia(respuesta) {
  const propuesta = [...(respuesta.ruta || [])].sort((a, b) => a.orden_entrega - b.orden_entrega);
  return { propuesta, paradas: propuesta, fuera: [], sinResolver: respuesta.sin_resolver || [] };
}

export const mover = (estado, i, delta) => ({ ...estado, paradas: moveStop(estado.paradas, i, delta) });
export const moverParada = (estado, desde, hasta) => ({ ...estado, paradas: moverA(estado.paradas, desde, hasta) });

export function sacar(estado, pedidoId) {
  const parada = estado.paradas.find(p => p.pedido_id === pedidoId);
  if (!parada) return estado;
  return { ...estado, paradas: estado.paradas.filter(p => p !== parada), fuera: [...estado.fuera, parada] };
}

// Vuelve a la ruta como última parada.
export function reponer(estado, pedidoId) {
  const parada = estado.fuera.find(p => p.pedido_id === pedidoId);
  if (!parada) return estado;
  return { ...estado, fuera: estado.fuera.filter(p => p !== parada), paradas: [...estado.paradas, parada] };
}

export const restablecer = estado => ({ ...estado, paradas: estado.propuesta, fuera: [] });

export const cambiada = estado => estado.fuera.length > 0 || estado.paradas.some((p, i) => p.pedido_id !== estado.propuesta[i]?.pedido_id);

// Los sacados no se envían: siguen pendientes y vuelven a "Preparar ruta". Los sin resolver se confirman
// con su motivo para que queden como "Dirección por revisar" (igual que al planificar directo).
export const datosConfirmar = estado => ({
  paradas: estado.paradas.map(({ pedido_id, latitud, longitud, version }) => ({ pedido_id, latitud, longitud, version })),
  sin_resolver: estado.sinResolver.map(({ pedido_id, motivo, version }) => ({ pedido_id, motivo, version })),
});

export const puedeConfirmar = estado => estado.paradas.length + estado.sinResolver.length > 0;

// Para revisar en el mapa dónde ubicó n8n cada parada.
export const enlacePunto = p => `https://www.google.com/maps/search/?api=1&query=${p.latitud},${p.longitud}`;
