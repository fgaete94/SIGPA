// Historia #99: registrar lo que realmente se entregó de cada línea del pedido, entregas parciales
// incluidas. El backend recalcula el total con lo entregado y deja el pedido como "entregado".
export const ESTADOS_ENTREGABLES = ['confirmado', 'en_despacho'];
export const puedeEntregarse = estado => ESTADOS_ENTREGABLES.includes(estado);

// Una fila por línea del pedido; parte con todo lo pedido (el caso más común).
export const formEntrega = lineas => lineas.map(l => ({
  id: l.id,
  producto: l.producto_nombre,
  solicitada: l.cantidad_solicitada,
  precio: Number(l.precio_unitario),
  entregada: String(l.cantidad_entregada ?? l.cantidad_solicitada),
}));

// Solo enteros desde 0; cualquier otra cosa es null.
export function leerCantidad(texto) {
  const limpio = String(texto ?? '').trim();
  return /^\d{1,4}$/.test(limpio) ? Number(limpio) : null;
}

export function resumenEntrega(form) {
  const cantidades = form.map(l => leerCantidad(l.entregada));
  const invalidas = form.filter((_, i) => cantidades[i] === null).map(l => l.producto);
  const unidades = cantidades.reduce((suma, n) => suma + (n ?? 0), 0);
  return {
    invalidas,
    unidades,
    total: form.reduce((suma, l, i) => suma + (cantidades[i] ?? 0) * l.precio, 0),
    totalPedido: form.reduce((suma, l) => suma + l.solicitada * l.precio, 0),
    parcial: form.some((l, i) => cantidades[i] !== null && cantidades[i] < l.solicitada),
    demas: form.filter((l, i) => cantidades[i] > l.solicitada).map(l => l.producto),
  };
}

// Mensaje que impide registrar; null si se puede.
export function errorEntrega(resumen) {
  if (resumen.invalidas.length) return `Revisa la cantidad entregada de ${resumen.invalidas.join(', ')}: debe ser un número entero desde 0.`;
  if (!resumen.unidades) return 'No se entregó ninguna unidad. Si el pedido no se pudo entregar, no registres la entrega: vuelve el pedido a "Confirmado" para la próxima ruta.';
  return null;
}

export const datosEntrega = form => ({ lineas: form.map(l => ({ detalle_id: l.id, cantidad_entregada: leerCantidad(l.entregada) })) });
