-- Historia #150: respaldo en BD de los borradores de pedido del bot, para que
-- sobrevivan a un redeploy o a la suspensión del servicio en Render.
-- Un borrador por teléfono; "datos" es el borrador serializado por
-- app/services/draft_store.py. El backend descarta los vencidos (ver
-- BORRADOR_TTL_HORAS) al cargarlos.
-- RLS activado SIN policies, como el resto del esquema: el backend se conecta
-- con un rol que la omite y la API pública (anon key) no puede leer los
-- borradores, que contienen direcciones y teléfonos.
-- Idempotente: se puede aplicar más de una vez sin error.

create table if not exists public.borrador_pedido (
  telefono       varchar(30) primary key,
  datos          jsonb       not null,
  actualizado_en timestamptz not null default now()
);

alter table public.borrador_pedido enable row level security;

comment on table public.borrador_pedido is
  'Borrador de pedido en curso del bot de WhatsApp (uno por teléfono). Respaldo de la caché en memoria de draft_store.';
