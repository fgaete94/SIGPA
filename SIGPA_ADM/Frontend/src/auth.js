// Sesión del panel: los tokens se obtienen del backend (/api/auth), que hace de proxy a Supabase Auth.
// refresh_token en sessionStorage (sobrevive a recargar, se borra al cerrar la pestaña); access_token solo en memoria.
const STORAGE_KEY = 'sigpa.refresh_token';
const EXPIRY_MARGIN_MS = 30_000;
const SESSION_ENDED = 'La sesión terminó. Vuelve a ingresar.';

function readStored() { try { return globalThis.sessionStorage?.getItem(STORAGE_KEY) || null; } catch { return null; } }
function writeStored(value) {
  try { if (value) globalThis.sessionStorage?.setItem(STORAGE_KEY, value); else globalThis.sessionStorage?.removeItem(STORAGE_KEY); } catch { /* almacenamiento no disponible: la sesión queda solo en memoria */ }
}

let accessToken = null;
let expiresAt = 0;
let refreshToken = readStored();
let refreshing = null;
const expiredListeners = new Set();

// Decodifica el payload de un JWT (base64url) sin verificar la firma: solo para mostrar datos; la verificación la hace el backend.
export function decodeJwtPayload(token) {
  const part = String(token || '').split('.')[1];
  if (!part) throw new Error('Token inválido.');
  const base64 = part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '=');
  const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}
export function userFromToken(token) {
  const payload = decodeJwtPayload(token);
  // El rol viene en app_metadata de Supabase: solo decide qué pantalla se muestra; lo permitido lo controla el backend.
  const rol = payload.app_metadata?.rol;
  return { id: payload.sub, email: payload.email, ...(rol && { rol }) };
}
export function isExpiring(expiry, now = Date.now(), margin = EXPIRY_MARGIN_MS) { return !expiry || now >= expiry - margin; }

function authError(status, body) {
  const detail = typeof body?.detail === 'string' ? body.detail : '';
  let message;
  if ((status === 401 || status === 429) && detail) message = detail;
  else if (status === 422) message = 'Revisa el correo y la contraseña ingresados.';
  else if (status >= 500) message = 'El servicio de autenticación no está disponible. Intenta más tarde.';
  else message = `No se pudo completar la autenticación (${status}).`;
  return Object.assign(new Error(message), { status });
}
async function postAuth(path, payload, headers = {}) {
  let response;
  try {
    response = await fetch(`/api/auth${path}`, { method: 'POST', headers: { ...(payload ? { 'Content-Type': 'application/json' } : {}), ...headers }, body: payload ? JSON.stringify(payload) : undefined });
  } catch {
    throw new Error('No se pudo conectar con SIGPA. Revisa la conexión.');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw authError(response.status, body);
  return body;
}
function saveTokens(data) {
  if (!data?.access_token || !data?.refresh_token) throw new Error('El servidor devolvió una respuesta inesperada.');
  accessToken = data.access_token;
  expiresAt = Date.now() + Number(data.expires_in || 0) * 1000;
  refreshToken = data.refresh_token;
  writeStored(refreshToken);
  return userFromToken(accessToken);
}
function clearSession() {
  accessToken = null; expiresAt = 0; refreshToken = null;
  writeStored(null);
}

// Registra un aviso para cuando la sesión termina (refresh rechazado). Devuelve la función para quitarlo.
export function onSessionExpired(callback) { expiredListeners.add(callback); return () => expiredListeners.delete(callback); }
export function expireSession() {
  clearSession();
  expiredListeners.forEach(callback => callback());
}

export async function login(email, password) { return saveTokens(await postAuth('/login', { email, password })); }

// El refresh token rota: las llamadas simultáneas comparten una sola petición.
export function refresh() {
  if (refreshing) return refreshing;
  if (!refreshToken) return Promise.reject(new Error(SESSION_ENDED));
  refreshing = postAuth('/refresh', { refresh_token: refreshToken })
    .then(saveTokens)
    .catch(error => { if (error.status === 401) expireSession(); throw error; })
    .finally(() => { refreshing = null; });
  return refreshing;
}

export async function logout() {
  const token = accessToken;
  clearSession();
  if (token) { try { await postAuth('/logout', null, { Authorization: `Bearer ${token}` }); } catch { /* la sesión local ya se cerró */ } }
}

export async function getAccessToken() {
  if (accessToken && !isExpiring(expiresAt)) return accessToken;
  await refresh();
  return accessToken;
}

// Solo un 401 invalida la sesión guardada. Ante errores de red o 5xx (p. ej. el backend de Render dormido)
// se conserva el refresh token para poder reintentar.
export async function restoreSession() {
  if (!refreshToken) return null;
  try { return await refresh(); }
  catch (error) { if (error.status === 401) clearSession(); return null; }
}
