// Lógica de la pestaña Rutas que no depende de React (se prueba en planificacion.test.js).
import { kilos } from './viajes.js';

// Igual a RUTA_MAX_PEDIDOS del backend (en Render, 50; el valor por defecto de app/core/config.py es 30):
// sobre ese número POST /rutas/planificar responde 422.
export const RUTA_MAX_PEDIDOS = 50;

// Marca o desmarca un pedido sin pasar del máximo.
export function alternarSeleccion(ids, id, marcado, maximo = RUTA_MAX_PEDIDOS) {
  if (!marcado) return ids.filter(x => x !== id);
  if (ids.includes(id) || ids.length >= maximo) return ids;
  return [...ids, id];
}

// Continente chileno con margen: detecta signos olvidados y latitud/longitud invertidas.
const CHILE = { latMin: -56, latMax: -17, lonMin: -76, lonMax: -66 };
const EJEMPLO = '-33.0458, -71.6197';

// Acepta lo que copia Google Maps ("-33.0458, -71.6197") o los dos números separados por espacio o punto y coma.
export function leerCoordenadas(texto) {
  const partes = String(texto ?? '').trim().split(/\s*[,;]\s*|\s+/).filter(Boolean);
  const numeros = partes.map(Number);
  if (partes.length !== 2 || numeros.some(n => !Number.isFinite(n))) {
    return { error: `Escribe la latitud y la longitud separadas por una coma, con punto decimal. Ejemplo: ${EJEMPLO}` };
  }
  const [latitud, longitud] = numeros;
  if (latitud < CHILE.latMin || latitud > CHILE.latMax || longitud < CHILE.lonMin || longitud > CHILE.lonMax) {
    return { error: `Esa ubicación queda fuera de Chile. Revisa que vaya primero la latitud y después la longitud, ambas negativas. Ejemplo: ${EJEMPLO}` };
  }
  return { latitud, longitud };
}

export function enlaceMapa(direccion) {
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${direccion}, Chile`)}`;
}

const ids = lista => lista.map(id => `#${id}`).join(', ');

// Detalle de un error de POST /rutas/planificar para mostrar bajo el mensaje principal.
// api() deja en error.status el código HTTP y en error.detalle el objeto "detail" del backend.
export function detallesErrorRuta(error) {
  const d = error?.detalle && !Array.isArray(error.detalle) ? error.detalle : {};
  const lineas = [];
  if (d.maximo != null) lineas.push(`Seleccionaste ${d.recibidos} pedidos; el máximo por ruta es ${d.maximo}.`);
  if (d.inexistentes?.length) lineas.push(`Ya no existen: ${ids(d.inexistentes)}.`);
  for (const p of d.estado_invalido || []) lineas.push(`El pedido #${p.pedido_id} ahora está "${String(p.estado).replaceAll('_', ' ')}".`);
  if (d.pedido_ids?.length && !d.inexistentes && !d.estado_invalido) lineas.push(`Pedidos repetidos: ${ids(d.pedido_ids)}.`);
  // EP-04, 422 por peso (se valida antes de llamar a n8n).
  if (d.sin_peso?.length) {
    const productos = new Map((d.productos_sin_peso || []).map(p => [p.pedido_id, p.productos]));
    lineas.push(`Sin peso calculable: ${d.sin_peso.map(id => `#${id}${productos.get(id)?.length ? ` (${productos.get(id).join(', ')})` : ''}`).join(', ')}. Sácalos de la selección hasta que sus productos tengan peso registrado.`);
  }
  for (const p of d.sobre_capacidad || []) lineas.push(`El pedido #${p.pedido_id} pesa ${kilos(p.peso_kg)}: solo ya supera lo que carga el camión${d.capacidad_kg != null ? ` (${kilos(d.capacidad_kg)})` : ''}.`);
  if (d.peso_total_kg != null) lineas.push(`Lo seleccionado pesa ${kilos(d.peso_total_kg)} y el máximo por ruta es ${kilos(d.maximo_kg)}: saca pedidos y déjalos para otra ruta.`);
  for (const problema of d.problemas || []) lineas.push(problema);
  if (d.codigo != null) lineas.push(`Código del servicio de rutas: ${d.codigo}.`);
  // n8n reenvía el 429 de OpenRouteService cuando se acaba su cuota gratuita (por minuto o diaria).
  if (/\(429\)/.test(d.mensaje || error?.message || '')) lineas.push('Se alcanzó el límite de consultas del servicio de mapas: espera unos minutos y vuelve a intentar. Si sigue, se agotó la cuota del día.');
  if (error?.status === 503) lineas.push('El servicio de rutas no está configurado en el servidor: avisa al equipo técnico.');
  if (error?.status === 504) lineas.push('En el plan gratuito n8n se duerme y tarda en despertar: espera un minuto y vuelve a intentar.');
  if ([409, 422, 502, 503, 504].includes(error?.status)) lineas.push('No se guardó nada: los pedidos siguen como estaban.');
  else lineas.push('Si se cortó la conexión, la ruta pudo haberse guardado igual: actualiza los datos antes de reintentar.');
  return lineas;
}
