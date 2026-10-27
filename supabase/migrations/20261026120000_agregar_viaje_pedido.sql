-- Viaje del pedido en la ruta de reparto generada (EP-04). Cuando el peso total
-- no cabe en un camión, la planificación se reparte en varios viajes;
-- orden_entrega pasa a ser la posición dentro del viaje.
-- Idempotente: se puede aplicar más de una vez sin error.

alter table public.pedido
  add column if not exists viaje smallint;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'pedido_viaje_chk'
      and conrelid = 'public.pedido'::regclass
  ) then
    alter table public.pedido
      add constraint pedido_viaje_chk
        check (viaje is null or viaje > 0);
  end if;
end
$$;

comment on column public.pedido.viaje is
  'Viaje de la ruta al que pertenece el pedido (1 = primero); orden_entrega es la posición dentro de ese viaje. NULL si no está en una ruta o no se pudo ubicar.';
