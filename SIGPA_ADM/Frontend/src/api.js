import { expireSession, getAccessToken, refresh } from './auth';
const SESSION_ENDED = 'La sesión terminó. Vuelve a ingresar.';
async function send(path, options, signal, token) {
  try {
    return await fetch(`/api${path}`, { ...options, signal, headers: { Authorization: `Bearer ${token}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}) } });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('No se pudo conectar con SIGPA. Revisa la conexión y la URL del backend.');
  }
}
export async function api(path, { signal, ...options } = {}) {
  let response = await send(path, options, signal, await getAccessToken());
  if (response.status === 401) {
    // Token rechazado: se intenta refrescar una sola vez y se reintenta la petición.
    // Solo un 401 del refresh cierra la sesión (lo hace refresh()); red o 5xx se relanzan sin cerrarla.
    let token;
    try { await refresh(); token = await getAccessToken(); }
    catch (error) { if (error.status === 401) throw new Error(SESSION_ENDED); throw error; }
    response = await send(path, options, signal, token);
    if (response.status === 401) { expireSession(); throw new Error(SESSION_ENDED); }
  }
  const text = await response.text().catch(() => '');
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!response.ok) {
    const detail = body?.detail;
    const error = new Error(typeof detail === 'string' ? detail : detail?.mensaje || `No se pudo completar la operación (${response.status}).`);
    // Para mostrar el detalle completo (ej. errores de rutas: {mensaje, problemas, pedido_ids, ...}).
    error.status = response.status;
    if (detail && typeof detail === 'object') error.detalle = detail;
    throw error;
  }
  if (response.status === 204 || !text) return null;
  if (body === null) throw new Error('El servidor devolvió una respuesta inesperada.');
  return body;
}
