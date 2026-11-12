import { API } from './apiBase.js';
// Precios públicos de la landing: GET /productos (solo lectura, sin sesión) a través del backend.
// La landing nunca habla con Supabase directamente. Del producto solo se usa nombre y precio.
const CLAVE = 'sigpa.precios';

// { nombre: precio } a partir de la respuesta de /productos; ignora filas sin precio válido.
export function indexarPrecios(productos) {
  if (!Array.isArray(productos)) throw new Error('Respuesta inesperada de /productos.');
  return Object.fromEntries(productos
    .filter(p => typeof p?.nombre === 'string' && Number.isFinite(Number(p.precio_unitario)) && p.precio_unitario !== null)
    .map(p => [p.nombre, Number(p.precio_unitario)]));
}

// Precios de la última visita, para no mostrar los de respaldo si ya se conocían unos más nuevos.
export function preciosGuardados() {
  try { return JSON.parse(localStorage.getItem(CLAVE)) || null; } catch { return null; }
}

export async function cargarPrecios(signal) {
  const respuesta = await fetch(`${API}/productos`, { signal });
  if (!respuesta.ok) throw new Error(`No se pudieron cargar los precios (${respuesta.status}).`);
  const precios = indexarPrecios(await respuesta.json());
  try { localStorage.setItem(CLAVE, JSON.stringify(precios)); } catch { /* almacenamiento no disponible */ }
  return precios;
}
