// Cada ruta (viaje) se puede asignar a un repartidor distinto: con dos camiones, la Ruta 1 la lleva uno
// y la Ruta 2 otro. La clave de una ruta es su número; 'sin' para paradas sin viaje (rutas antiguas).
export const claveRuta = viaje => (viaje == null ? 'sin' : String(viaje));

// Pedidos a asignar agrupados por repartidor, a partir de las paradas ({ pedido_id, viaje }) y de la
// elección por ruta ({ [claveRuta]: repartidorId }). Las rutas sin repartidor elegido no se asignan.
// Devuelve [{ repartidor_id, rutas: [números], pedido_ids }], en el orden de las rutas.
export function asignacionesPorRepartidor(paradas, eleccion) {
  const porRepartidor = new Map();
  for (const p of paradas) {
    const clave = claveRuta(p.viaje);
    const id = Number(eleccion[clave]);
    if (!id) continue;
    if (!porRepartidor.has(id)) porRepartidor.set(id, { repartidor_id: id, rutas: [], pedido_ids: [] });
    const grupo = porRepartidor.get(id);
    if (p.viaje != null && !grupo.rutas.includes(p.viaje)) grupo.rutas.push(p.viaje);
    grupo.pedido_ids.push(p.pedido_id);
  }
  return [...porRepartidor.values()];
}

// Texto para el aviso: "Ruta 1 y Ruta 3" o "la ruta" (sin viajes).
export function nombreRutas(rutas) {
  if (!rutas.length) return 'la ruta';
  const nombres = rutas.map(n => `Ruta ${n}`);
  return nombres.length === 1 ? nombres[0] : `${nombres.slice(0, -1).join(', ')} y ${nombres.at(-1)}`;
}

// Quién lleva una ruta en curso: el de lo que falta por entregar; con todo resuelto, quién la llevó.
// Si las paradas pendientes están repartidas entre varios, devuelve '' (mixta: se elige de nuevo).
export function repartidorDeRuta(paradas, resuelta) {
  const pendientes = paradas.filter(p => !resuelta(p));
  const base = pendientes.length ? pendientes : paradas;
  const ids = [...new Set(base.map(p => p.repartidor_id ?? null))];
  return ids.length === 1 && ids[0] != null ? String(ids[0]) : '';
}
