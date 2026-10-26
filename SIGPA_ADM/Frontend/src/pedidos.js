// Pedido ingresado a mano (por teléfono o en persona) para POST /pedidos.
// El backend calcula el total con el precio actual de cada producto y lo deja "pendiente".
export const lineaVacia = () => ({ producto_id: '', cantidad: '1' });
export const pedidoForm = () => ({ cliente_id: '', direccion_despacho: '', lineas: [lineaVacia()] });

const cantidadValida = texto => /^\d+$/.test(String(texto).trim()) && Number(texto) > 0 && Number(texto) <= 999;

export function totalEstimado(lineas, productos) {
  const precio = new Map(productos.map(p => [String(p.id), Number(p.precio_unitario)]));
  return lineas.reduce((suma, l) => suma + (precio.has(l.producto_id) && cantidadValida(l.cantidad) ? precio.get(l.producto_id) * Number(l.cantidad) : 0), 0);
}

// Valida las líneas del formulario (las que tienen producto) y las deja como espera el backend.
function validarLineas(lineas, productos) {
  const usadas = lineas.filter(l => l.producto_id !== '');
  if (!usadas.length) return { error: 'Agrega al menos un producto.' };
  const nombre = id => productos.find(p => String(p.id) === id)?.nombre || `producto ${id}`;
  const vistos = new Set();
  for (const l of usadas) {
    if (vistos.has(l.producto_id)) return { error: `${nombre(l.producto_id)} está dos veces: deja una sola línea con la cantidad total.` };
    vistos.add(l.producto_id);
    if (!cantidadValida(l.cantidad)) return { error: `La cantidad de ${nombre(l.producto_id)} debe ser un número entero entre 1 y 999.` };
  }
  return { lineas: usadas.map(l => ({ producto_id: Number(l.producto_id), cantidad_solicitada: Number(l.cantidad) })) };
}

export function datosPedido(form, productos) {
  if (!form.cliente_id) return { error: 'Elige el cliente.' };
  const { lineas, error } = validarLineas(form.lineas, productos);
  if (error) return { error };
  return { datos: {
    cliente_id: Number(form.cliente_id),
    // Vacía = la dirección del cliente (el backend la usa al planificar la ruta).
    direccion_despacho: form.direccion_despacho.trim() || null,
    lineas,
  } };
}

// Historia #68: editar un pedido ya ingresado (PATCH /pedidos/{id}). Entregados y cancelados no se editan.
export const ESTADOS_EDITABLES = ['pendiente', 'confirmado', 'en_despacho'];
export const puedeEditarse = estado => ESTADOS_EDITABLES.includes(estado);

// pedido: GET /pedidos/{id} (con sus líneas).
export const formEdicion = pedido => ({
  direccion_despacho: pedido.direccion_despacho || '',
  lineas: pedido.lineas.map(l => ({ producto_id: String(l.producto_id), cantidad: String(l.cantidad_solicitada) })),
});

// Igual que el backend: los productos que ya estaban conservan su precio; los nuevos, el vigente.
export function totalEdicion(lineas, productos, pedido) {
  const pactado = new Map(pedido.lineas.map(l => [String(l.producto_id), Number(l.precio_unitario)]));
  const vigente = new Map(productos.map(p => [String(p.id), Number(p.precio_unitario)]));
  return lineas.reduce((suma, l) => {
    const precio = pactado.get(l.producto_id) ?? vigente.get(l.producto_id);
    return suma + (precio != null && cantidadValida(l.cantidad) ? precio * Number(l.cantidad) : 0);
  }, 0);
}

const mismaDireccion = (a, b) => (a || '').trim().replace(/\s+/g, ' ').toLowerCase() === (b || '').trim().replace(/\s+/g, ' ').toLowerCase();
const claveLineas = lineas => lineas.map(l => `${l.producto_id}x${l.cantidad_solicitada}`).sort().join(',');

// Solo lo que cambió. { datos, direccionCambia } o { error }.
export function datosEdicion(form, pedido, productos) {
  const { lineas, error } = validarLineas(form.lineas, productos);
  if (error) return { error };
  const datos = {};
  if (claveLineas(lineas) !== claveLineas(pedido.lineas)) datos.lineas = lineas;
  const direccion = form.direccion_despacho.trim() || null;
  const direccionCambia = !mismaDireccion(direccion, pedido.direccion_despacho);
  if (direccionCambia) datos.direccion_despacho = direccion;
  if (!Object.keys(datos).length) return { error: 'No hay cambios para guardar.' };
  return { datos, direccionCambia };
}
