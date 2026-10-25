begin;

-- En producción estas columnas se agregaron directo en Supabase antes de
-- esta migración y ninguna migración anterior las crea. Se agregan aquí
-- (sin efecto si ya existen) para que el historial funcione en una base
-- nueva, por ejemplo un proyecto de pruebas.
alter table public.cliente add column if not exists apellido_paterno varchar(100);
alter table public.cliente add column if not exists apellido_materno varchar(100);

alter table public.cliente_producto_habitual drop constraint cliente_producto_habitual_cliente_id_fkey;
alter table public.mensaje_whatsapp drop constraint mensaje_whatsapp_cliente_id_fkey;
alter table public.pedido drop constraint pedido_cliente_id_fkey;

alter table public.cliente rename to cliente_old;

create table public.cliente (
  id integer primary key default nextval('cliente_id_seq'::regclass),
  cod_legado varchar(10),
  nombre varchar(150) not null,
  apellido_paterno varchar(100),
  apellido_materno varchar(100),
  telefono varchar(30),
  direccion varchar(255),
  sector_id integer references public.sector(id),
  tipo_cliente_id integer references public.tipo_cliente(id),
  dia_reparto dia_semana,
  activo boolean not null default true,
  notas text,
  creado_en timestamp not null default now(),
  latitud numeric(9,6),
  longitud numeric(9,6),
  opt_out_whatsapp boolean not null default false
);

comment on column public.cliente.opt_out_whatsapp is 'true si el cliente pidió no recibir más mensajes automáticos de WhatsApp (recordatorio proactivo). Independiente de "activo" (baja comercial).';

insert into public.cliente (
  id, cod_legado, nombre, apellido_paterno, apellido_materno, telefono,
  direccion, sector_id, tipo_cliente_id, dia_reparto, activo, notas,
  creado_en, latitud, longitud, opt_out_whatsapp
)
select
  id, cod_legado, nombre, apellido_paterno, apellido_materno, telefono,
  direccion, sector_id, tipo_cliente_id, dia_reparto, activo, notas,
  creado_en, latitud, longitud, opt_out_whatsapp
from public.cliente_old;

alter sequence public.cliente_id_seq owned by public.cliente.id;

alter table public.cliente_producto_habitual
  add constraint cliente_producto_habitual_cliente_id_fkey
  foreign key (cliente_id) references public.cliente(id) on delete cascade;

alter table public.mensaje_whatsapp
  add constraint mensaje_whatsapp_cliente_id_fkey
  foreign key (cliente_id) references public.cliente(id);

alter table public.pedido
  add constraint pedido_cliente_id_fkey
  foreign key (cliente_id) references public.cliente(id);

alter table public.cliente enable row level security;

drop table public.cliente_old;

commit;