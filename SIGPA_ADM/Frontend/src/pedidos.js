// Pedido ingresado a mano (por teléfono o en persona) para POST /pedidos.
// El backend calcula el total con el precio actual de cada producto y lo deja "pendiente".
export const lineaVacia = () => ({ producto_id: '', cantidad: '1' });
export const pedidoForm = () => ({ cliente_id: '', direccion_despacho: '', lineas: [lineaVacia()] });

const cantidadValida = texto => /^\d+$/.test(String(texto).trim()) && Number(texto) > 0 && Number(texto) <= 999;

export function totalEstimado(lineas, productos) {
  const precio = new Map(productos.map(p => [String(p.id), Number(p.precio_unitario)]));
  return lineas.reduce((suma, l) => suma + (precio.has(l.producto_id) && cantidadValida(l.cantidad) ? precio.get(l.producto_id) * Number(l.cantidad) : 0), 0);
}

export function datosPedido(form, productos) {
  if (!form.cliente_id) return { error: 'Elige el cliente.' };
  const usadas = form.lineas.filter(l => l.producto_id !== '');
  if (!usadas.length) return { error: 'Agrega al menos un producto.' };
  const nombre = id => productos.find(p => String(p.id) === id)?.nombre || `producto ${id}`;
  const vistos = new Set();
  for (const l of usadas) {
    if (vistos.has(l.producto_id)) return { error: `${nombre(l.producto_id)} está dos veces: deja una sola línea con la cantidad total.` };
    vistos.add(l.producto_id);
    if (!cantidadValida(l.cantidad)) return { error: `La cantidad de ${nombre(l.producto_id)} debe ser un número entero entre 1 y 999.` };
  }
  return { datos: {
    cliente_id: Number(form.cliente_id),
    // Vacía = la dirección del cliente (el backend la usa al planificar la ruta).
    direccion_despacho: form.direccion_despacho.trim() || null,
    lineas: usadas.map(l => ({ producto_id: Number(l.producto_id), cantidad_solicitada: Number(l.cantidad) })),
  } };
}
