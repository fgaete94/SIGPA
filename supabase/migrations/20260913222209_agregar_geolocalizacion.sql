ALTER TABLE cliente
    ADD COLUMN latitud NUMERIC(9,6),
    ADD COLUMN longitud NUMERIC(9,6);

ALTER TABLE pedido
    ADD COLUMN latitud NUMERIC(9,6),
    ADD COLUMN longitud NUMERIC(9,6);