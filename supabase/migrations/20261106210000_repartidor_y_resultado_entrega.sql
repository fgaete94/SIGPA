-- Vista del repartidor en el celular: asignar la ruta a un repartidor, registrar
-- el resultado de cada parada (entregado con su método de pago y cantidades, o
-- no entregado), reagendar o cancelar lo no entregado, y el combustible del día.
-- RLS activado SIN policies, como el resto del esquema: el backend se conecta
-- con un rol que la omite y la API pública (anon key) no puede leer estas tablas.
-- Solo agrega: el backend actual sigue funcionando con esta migración aplicada.
-- Idempotente: se puede aplicar más de una vez sin error.

alter type public.estado_pedido add value if not exists 'no_entregado';

-- Un repartidor por usuario de Supabase Auth. El permiso lo da app_metadata.rol
-- del token; esta fila lo identifica para saber qué ruta le corresponde.
create table if not exists public.repartidor (
  id           serial primary key,
  auth_user_id uuid         not null unique,
  nombre       varchar(100) not null,
  activo       boolean      not null default true,
  creado_en    timestamp    not null default now()
);

alter table public.repartidor enable row level security;

comment on table public.repartidor is
  'Repartidores que pueden recibir una ruta. auth_user_id es el usuario de Supabase Auth (sub del token).';

alter table public.pedido
  add column if not exists repartidor_id integer references public.repartidor (id),
  add column if not exists medio_pago varchar(20),
  add column if not exists resultado_en timestamp,
  add column if not exists reagendado_para date,
  add column if not exists entrega_ajustada boolean not null default false;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'pedido_medio_pago_chk'
      and conrelid = 'public.pedido'::regclass
  ) then
    alter table public.pedido
      add constraint pedido_medio_pago_chk
        check (medio_pago is null or medio_pago in ('efectivo', 'transferencia'));
  end if;
end
$$;

create index if not exists pedido_repartidor_id_idx
  on public.pedido (repartidor_id)
  where repartidor_id is not null;

comment on column public.pedido.repartidor_id is
  'Repartidor al que se asignó la parada. NULL si la ruta no está asignada.';
comment on column public.pedido.medio_pago is
  'Cómo pagó el cliente al recibir el pedido: efectivo o transferencia. NULL si no se informó.';
comment on column public.pedido.resultado_en is
  'Cuándo se marcó el resultado de la parada (entregado o no entregado), en UTC.';
comment on column public.pedido.reagendado_para is
  'Día para el que se reagendó un pedido no entregado.';
comment on column public.pedido.entrega_ajustada is
  'true si lo entregado difiere de lo pedido; el total del pedido ya considera lo entregado.';

-- Las entregas ya registradas con otras cantidades (#99) quedan marcadas igual.
update public.pedido p
set entrega_ajustada = true
where p.estado = 'entregado'
  and not p.entrega_ajustada
  and exists (
    select 1
    from public.detalle_pedido d
    where d.pedido_id = p.id
      and d.cantidad_entregada is distinct from d.cantidad_solicitada
  );

-- Reagendar un pedido lo devuelve a pendiente y borra su lugar en la ruta. Esta
-- tabla conserva cada intento fallido para Reportes y Despachos.
create table if not exists public.intento_no_entregado (
  id            serial primary key,
  pedido_id     integer       not null references public.pedido (id) on delete cascade,
  repartidor_id integer       references public.repartidor (id),
  viaje         smallint,
  orden_entrega integer,
  total         numeric(10, 2) not null,
  creado_en     timestamp     not null default now()
);

create index if not exists intento_no_entregado_pedido_id_idx
  on public.intento_no_entregado (pedido_id);

alter table public.intento_no_entregado enable row level security;

comment on table public.intento_no_entregado is
  'Un registro por cada vez que una parada se marcó como no entregada, con su lugar en la ruta de ese día.';

create table if not exists public.carga_combustible (
  id            serial primary key,
  repartidor_id integer       not null references public.repartidor (id),
  cargo         boolean       not null,
  monto         numeric(10, 2),
  medio_pago    varchar(20),
  creado_en     timestamp     not null default now(),
  constraint carga_combustible_datos_chk check (
    (cargo and monto > 0 and medio_pago in ('efectivo', 'tarjeta'))
    or (not cargo and monto is null and medio_pago is null)
  )
);

create index if not exists carga_combustible_repartidor_creado_idx
  on public.carga_combustible (repartidor_id, creado_en);

alter table public.carga_combustible enable row level security;

comment on table public.carga_combustible is
  'Lo que informa el repartidor sobre el combustible: si cargó, cuánto y cómo pagó (efectivo o tarjeta), o que no cargó.';
