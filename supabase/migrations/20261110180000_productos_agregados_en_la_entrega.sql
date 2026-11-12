-- El repartidor puede entregar productos que el cliente no pidió (por ejemplo, un
-- bidón de 12 L o un dispensador). Se guardan como líneas del pedido con
-- cantidad_solicitada = 0 y lo entregado en cantidad_entregada, con el precio del
-- catálogo. Las líneas que vienen del pedido siguen exigiendo al menos 1 (lo valida
-- el backend al crear o editar un pedido).
-- Idempotente: se puede aplicar más de una vez sin error.

alter table public.detalle_pedido
  drop constraint if exists detalle_pedido_cantidad_solicitada_chk;

alter table public.detalle_pedido
  add constraint detalle_pedido_cantidad_solicitada_chk
    check (cantidad_solicitada >= 0);

comment on column public.detalle_pedido.cantidad_solicitada is
  'Lo que pidió el cliente. 0 en un producto que no estaba en el pedido y se agregó al entregarlo.';
