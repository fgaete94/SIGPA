// Hoja de ruta imprimible: la planilla que el conductor lleva en el camión, con la carga, las paradas
// en orden y espacio para anotar lo entregado y lo cobrado (reemplaza la planilla Excel de ADM).

// Columnas fijas, en el orden de la planilla de ADM. Siempre se imprimen (aunque la ruta no lleve ese
// producto) para que el conductor anote lo que un cliente pida en el momento. Las promos se agregan solo si van en la ruta.
export const COLUMNAS_FIJAS = ['Bidón 20L Recarga', 'Bidón 12L Recarga', 'Bidón 20L Nuevo', 'Bidón 12L Nuevo', 'Dispensador Básico', 'Dispensador USB'];

// Etiqueta de la columna de cada producto: 'Bidón 20L Recarga' → 'Recarga 20L', como en la planilla.
export function etiquetaProducto(nombre) {
  const promo = nombre.match(/^Promo Dispensador (.+?) \+ (\d+) Bid/i);
  if (promo) return `Promo ${promo[1]} +${promo[2]}`;
  const bidon = nombre.match(/^Bid[oó]n (\d+)\s*L\s+(.+)$/i);
  if (bidon) return `${bidon[2]} ${bidon[1]}L`;
  return nombre;
}

// Cantidad y precio por producto de un pedido. Si un producto viene en varias líneas se suman;
// el precio se omite cuando las líneas no coinciden.
export function cantidadesPedido(lineas = []) {
  const porProducto = {};
  for (const { producto_nombre, cantidad_solicitada, precio_unitario } of lineas) {
    const previo = porProducto[producto_nombre];
    porProducto[producto_nombre] = previo
      ? { cantidad: previo.cantidad + cantidad_solicitada, precio: previo.precio === precio_unitario ? previo.precio : null }
      : { cantidad: cantidad_solicitada, precio: precio_unitario };
  }
  return porProducto;
}

// Columnas: las fijas y, después, los demás productos que lleve la ruta en el orden del catálogo
// (los que no estén en el catálogo, al final).
export function columnasProductos(filas, productos = []) {
  const orden = new Map(productos.map((p, i) => [p.nombre, i]));
  const extra = [...new Set(filas.flatMap(f => Object.keys(f.productos)))].filter(n => !COLUMNAS_FIJAS.includes(n));
  return [...COLUMNAS_FIJAS, ...extra.sort((a, b) => (orden.get(a) ?? Infinity) - (orden.get(b) ?? Infinity) || a.localeCompare(b, 'es'))];
}

// Una fila por parada, en el orden recibido (el de "Ruta en curso", incluido un orden cambiado a mano).
// Con viajes, el número de parada parte de 1 en cada viaje (como orden_entrega).
// detalles: GET /pedidos/{id} de cada parada, por id. El sector sale del cliente y /sectores.
export function filasHoja(paradas, detalles = {}, clientes = [], sectores = []) {
  const clientePorId = new Map(clientes.map(c => [c.id, c]));
  const sectorPorId = new Map(sectores.map(s => [s.id, s]));
  const contados = new Map();
  return paradas.map(p => {
    const viaje = p.viaje ?? null;
    contados.set(viaje, (contados.get(viaje) || 0) + 1);
    const sector = sectorPorId.get(clientePorId.get(p.cliente_id)?.sector_id);
    const comuna = p.comuna_nombre || sector?.comuna_nombre || null;
    const detalle = detalles[p.id];
    const sectorVisible = sector && sector.nombre !== comuna ? sector.nombre : null;
    return {
      numero: contados.get(viaje),
      viaje,
      pedidoId: p.id,
      clienteId: p.cliente_id,
      cliente: p.cliente_nombre || `Cliente ${p.cliente_id}`,
      telefono: formatoTelefono(p.cliente_telefono),
      direccion: p.direccion || null,
      comuna,
      // Un sector con el mismo nombre que la comuna no aporta nada en la hoja.
      sector: sectorVisible,
      // Bajo la dirección: la comuna si la dirección no la trae, y el sector.
      zona: [comuna && !p.direccion?.includes(comuna) ? comuna : null, sectorVisible].filter(Boolean).join(' · ') || null,
      productos: cantidadesPedido(detalle?.lineas),
      total: detalle?.total ?? p.total,
    };
  });
}

// Unidades por producto que deben subir al camión.
export function cargaRuta(filas, columnas) {
  return Object.fromEntries(columnas.map(nombre => [nombre, filas.reduce((suma, f) => suma + (f.productos[nombre]?.cantidad || 0), 0)]));
}

export const totalPorCobrar = filas => filas.reduce((suma, f) => suma + Number(f.total || 0), 0);

// 56912345678 → +56 9 1234 5678; cualquier otro formato se deja como viene.
export function formatoTelefono(telefono) {
  if (!telefono) return null;
  const digitos = String(telefono).replace(/\D/g, '');
  return /^569\d{8}$/.test(digitos) ? `+56 9 ${digitos.slice(3, 7)} ${digitos.slice(7)}` : telefono;
}

// 'AAAA-MM-DD' (valor de <input type="date">) → 'lunes 12-10-2026'. Se arma a mano porque la
// puntuación de toLocaleDateString cambia entre navegadores.
export function fechaHoja(dia) {
  const fecha = new Date(`${dia}T12:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dia) || Number.isNaN(fecha.getTime())) return '';
  const [anio, mes, diaMes] = dia.split('-');
  return `${fecha.toLocaleDateString('es-CL', { timeZone: 'UTC', weekday: 'long' })} ${diaMes}-${mes}-${anio}`;
}
