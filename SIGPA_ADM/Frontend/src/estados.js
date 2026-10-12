// Estados del pedido (enum estado_pedido) y cambios de despacho de la historia #70.
export const ETIQUETA_ESTADO = { pendiente: 'Pendiente', confirmado: 'Confirmado', en_despacho: 'En despacho', entregado: 'Entregado', cancelado: 'Cancelado' };
export const ESTADOS = Object.keys(ETIQUETA_ESTADO);

// Igual que TRANSICIONES_DESPACHO del backend (pedidos.py): solo confirmado ↔ en_despacho.
// "entregado" no se marca aquí: se registra con las cantidades entregadas (#99).
export const ACCION_DESPACHO = {
  confirmado: { destino: 'en_despacho', texto: 'Salió a reparto' },
  en_despacho: { destino: 'confirmado', texto: 'Volver a confirmado' },
};

const ACCION_HISTORIAL = { crear: 'Creación', cambiar_estado: 'Cambio desde el panel', planificar_ruta: 'Planificación de ruta', actualizar: 'Edición', corregir_coordenadas: 'Corrección de ubicación' };

// Una fila de GET /pedidos/{id}/historial en texto: "Confirmado → En despacho".
export function textoCambio({ estado_anterior, estado_nuevo }) {
  const nuevo = ETIQUETA_ESTADO[estado_nuevo] || estado_nuevo;
  return estado_anterior ? `${ETIQUETA_ESTADO[estado_anterior] || estado_anterior} → ${nuevo}` : `Creado como ${nuevo.toLowerCase()}`;
}
export const textoAccion = accion => ACCION_HISTORIAL[accion] || accion;

// Pedidos confirmados de la ruta que pueden salir a reparto juntos.
export const despachables = paradas => paradas.filter(p => p.estado === 'confirmado').map(p => p.id);
