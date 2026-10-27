-- Peso por unidad del producto, usado por la planificación de rutas (EP-04) para
-- calcular la carga de cada camión. La columna ya existe en producción (se creó
-- a mano); esta migración la registra en el repositorio para que una base nueva
-- construida desde cero la tenga. Es idempotente: en producción no cambia nada.
-- Sin CHECK ni default a propósito: hoy hay valores 0, 12 y 20, y NULL en promos.

alter table public.producto
  add column if not exists peso_kg smallint;

comment on column public.producto.peso_kg is
  'Peso en kg de una unidad llena; 0 es válido; NULL = sin peso calculable.';
