// EP-04: la ruta se reparte en viajes según el peso que carga el camión. Cada parada trae "viaje" y su
// orden_entrega va de 1 a N dentro de ese viaje; las respuestas traen "viajes":
// [{ viaje, peso_kg, termina_en: 'planta' | 'casa', total_paradas }]. Los pedidos de rutas anteriores
// a los viajes traen viaje null.

// Iguales a RUTA_CAPACIDAD_KG, RUTA_UMBRAL_CASA_KG y RUTA_MAX_VIAJES del backend (app/core/config.py).
export const CAPACIDAD_KG = 1100;
export const UMBRAL_CASA_KG = 800;
export const MAX_VIAJES = 3;

const DESTINO = { planta: 'termina en planta', casa: 'termina en casa del despachador' };
export const kilos = n => `${new Intl.NumberFormat('es-CL', { maximumFractionDigits: 1 }).format(n)} kg`;

const posicion = v => (v == null ? Infinity : v);
export const ordenViaje = (a, b) => posicion(a.viaje) - posicion(b.viaje) || a.orden_entrega - b.orden_entrega;

// Grupos en orden de viaje, cada uno con sus paradas en el orden recibido y los datos de "viajes".
export function agruparPorViaje(paradas, viajes = []) {
  const datos = new Map(viajes.map(v => [v.viaje, v]));
  const grupos = new Map();
  for (const p of paradas) {
    const v = p.viaje ?? null;
    if (!grupos.has(v)) grupos.set(v, { ...datos.get(v), viaje: v, paradas: [] });
    grupos.get(v).paradas.push(p);
  }
  return [...grupos.values()].sort((a, b) => posicion(a.viaje) - posicion(b.viaje));
}

// "Viaje 2 · 1.240 kg · termina en planta". Sin número de viaje (ruta anterior a los viajes) no hay encabezado.
export function encabezadoViaje({ viaje, peso_kg, termina_en }) {
  if (viaje == null) return null;
  return [`Viaje ${viaje}`, Number.isFinite(peso_kg) && kilos(peso_kg), DESTINO[termina_en]].filter(Boolean).join(' · ');
}

// Reemplaza las paradas de un viaje por las mismas en otro orden, sin tocar las de los demás viajes.
export function reemplazarViaje(paradas, viaje, nuevas) {
  const siguiente = nuevas[Symbol.iterator]();
  return paradas.map(p => ((p.viaje ?? null) === viaje ? siguiente.next().value : p));
}

// Misma regla que el backend: solo el último viaje, si hay al menos 2 y pesa menos que el umbral, termina en casa.
export const terminaEn = pesos => pesos.map((peso, i) => (pesos.length >= 2 && i === pesos.length - 1 && peso < UMBRAL_CASA_KG ? 'casa' : 'planta'));

// Lo que el backend rechazaría al confirmar (pesos de cada viaje en orden): así se avisa antes de enviar.
export function problemasViajes(pesos) {
  const problemas = [];
  if (pesos.length > MAX_VIAJES) problemas.push(`Hay ${pesos.length} viajes y el máximo es ${MAX_VIAJES}.`);
  pesos.forEach((peso, i) => { if (peso > CAPACIDAD_KG) problemas.push(`El viaje ${i + 1} pesa ${kilos(peso)} y el camión carga hasta ${kilos(CAPACIDAD_KG)}.`); });
  const ultimo = pesos.at(-1);
  if (pesos.length >= 2 && pesos.slice(0, -1).some(peso => peso < ultimo)) problemas.push(`El último viaje (${pesos.length}) tiene que ser el más liviano: pasa paradas de ese viaje a otro o vuelve al orden propuesto.`);
  return problemas;
}

// Para "Preparar ruta": cuántos viajes harán falta como mínimo y si lo elegido se puede planificar.
export function resumenPeso(pedidos) {
  const sinPeso = pedidos.filter(p => p.peso_kg == null);
  const total = pedidos.reduce((suma, p) => suma + (p.peso_kg ?? 0), 0);
  return {
    total,
    sinPeso,
    viajes: Math.max(1, Math.ceil(total / CAPACIDAD_KG)),
    sobreCapacidad: pedidos.filter(p => p.peso_kg > CAPACIDAD_KG),
    excedido: total > CAPACIDAD_KG * MAX_VIAJES,
  };
}
