// Dirección del backend. En Render el panel llama a /api y Render lo reenvía
// al backend; en otro hosting (aguadm.cl) se compila con VITE_API_URL apuntando
// directo al backend, que debe tener ese dominio en CORS_ORIGINS.
export const apiBase = url => String(url || '/api').trim().replace(/\/+$/, '');
export const API = apiBase(import.meta.env?.VITE_API_URL);
