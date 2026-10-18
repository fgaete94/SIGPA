-- Historia #136 (segunda parte): la base garantiza que el sector del cliente
-- pertenezca a su comuna. Se aplica después de desplegar el backend que guarda
-- comuna_id, para no rechazar los cambios que haga la versión anterior.

-- Completa la comuna de los clientes que recibieron sector entre ambas migraciones.
update public.cliente c
set comuna_id = s.comuna_id
from public.sector s
where c.sector_id = s.id
  and c.comuna_id is distinct from s.comuna_id;

-- Un cliente con sector siempre tiene comuna.
alter table public.cliente
  add constraint cliente_sector_requiere_comuna
  check (sector_id is null or comuna_id is not null);

-- El par (sector, comuna) del cliente debe existir en sector.
alter table public.sector
  add constraint sector_id_comuna_id_key unique (id, comuna_id);

alter table public.cliente
  add constraint cliente_sector_de_su_comuna_fkey
  foreign key (sector_id, comuna_id) references public.sector (id, comuna_id);