-- Historia #136: la comuna se guarda en el cliente y el sector queda como dato
-- opcional dentro de esa comuna. Hasta ahora la comuna se obtenía solo a través
-- del sector (cliente.sector_id -> sector.comuna_id), por lo que un cliente sin
-- sector quedaba sin comuna.

alter table public.cliente
  add column comuna_id integer references public.comuna(id);

comment on column public.cliente.comuna_id is
  'Comuna del cliente. Si tiene sector, debe ser la comuna de ese sector. NULL si aún no se asigna.';

-- Los clientes que ya tienen sector quedan con la comuna de su sector.
update public.cliente c
set comuna_id = s.comuna_id
from public.sector s
where c.sector_id = s.id;

create index idx_cliente_comuna_id on public.cliente using btree (comuna_id);