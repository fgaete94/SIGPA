-- Pedidos grandes del bot de WhatsApp: se crean igual, pero quedan marcados
-- para que la ejecutiva los revise (ver order_flow.umbrales_superados). El
-- enum estado_pedido no tiene un valor de revisión, así que la marca va en
-- columnas nuevas, sin tocar las existentes. La ejecutiva la limpia con
-- PATCH /pedidos/{id}.
-- Aditiva e idempotente: se puede aplicar más de una vez sin error.

alter table public.pedido
  add column if not exists requiere_revision boolean not null default false;

alter table public.pedido
  add column if not exists motivo_revision text null;

comment on column public.pedido.requiere_revision is
  'true si la ejecutiva debe revisar el pedido antes de despacharlo (pedido grande tomado por el bot).';

comment on column public.pedido.motivo_revision is
  'Por qué requiere revisión, ej. "Bidón 20L: 13 unidades, umbral 10". Null si no requiere.';
