SET local check_function_bodies = off;

CREATE SEQUENCE "public"."cliente_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."comuna_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."detalle_pedido_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."pedido_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."producto_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."sector_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."tipo_cliente_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE TABLE "public"."cliente_producto_habitual" (
  "cliente_id"  integer NOT NULL,
  "producto_id" integer NOT NULL,
  CONSTRAINT "cliente_producto_habitual_pkey" PRIMARY KEY (cliente_id, producto_id)
);

ALTER TABLE "public"."cliente_producto_habitual"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."cliente" (
  "id"              integer                     NOT NULL DEFAULT nextval('public.cliente_id_seq'::regclass),
  "cod_legado"      character varying(10),
  "nombre"          character varying(150)      NOT NULL,
  "telefono"        character varying(30),
  "direccion"       character varying(255),
  "sector_id"       integer,
  "tipo_cliente_id" integer,
  "activo"          boolean                     NOT NULL DEFAULT true,
  "notas"           text,
  "creado_en"       timestamp without time zone NOT NULL DEFAULT now(),
  CONSTRAINT "cliente_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."cliente"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."comuna" (
  "id"     integer                NOT NULL DEFAULT nextval('public.comuna_id_seq'::regclass),
  "nombre" character varying(100) NOT NULL,
  CONSTRAINT "comuna_nombre_key" UNIQUE (nombre),
  CONSTRAINT "comuna_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."comuna"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."detalle_pedido" (
  "id"              integer       NOT NULL DEFAULT nextval('public.detalle_pedido_id_seq'::regclass),
  "pedido_id"       integer       NOT NULL,
  "producto_id"     integer       NOT NULL,
  "cantidad"        integer       NOT NULL DEFAULT 1,
  "precio_unitario" numeric(10,2) NOT NULL,
  CONSTRAINT "detalle_pedido_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."detalle_pedido"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."pedido" (
  "id"                 integer                     NOT NULL DEFAULT nextval('public.pedido_id_seq'::regclass),
  "cliente_id"         integer                     NOT NULL,
  "direccion_despacho" character varying(255),
  "total"              numeric(10,2)               NOT NULL DEFAULT 0,
  "creado_en"          timestamp without time zone NOT NULL DEFAULT now(),
  "actualizado_en"     timestamp without time zone NOT NULL DEFAULT now(),
  CONSTRAINT "pedido_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."pedido"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."producto" (
  "id"              integer               NOT NULL DEFAULT nextval('public.producto_id_seq'::regclass),
  "nombre"          character varying(50) NOT NULL,
  "precio_unitario" numeric(10,2)         NOT NULL DEFAULT 0,
  "stock"           integer               NOT NULL DEFAULT 0,
  CONSTRAINT "producto_nombre_key" UNIQUE (nombre),
  CONSTRAINT "producto_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."producto"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."sector" (
  "id"        integer                NOT NULL DEFAULT nextval('public.sector_id_seq'::regclass),
  "comuna_id" integer                NOT NULL,
  "nombre"    character varying(100) NOT NULL,
  CONSTRAINT "sector_comuna_id_nombre_key" UNIQUE (comuna_id, nombre),
  CONSTRAINT "sector_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."sector"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."tipo_cliente" (
  "id"     integer               NOT NULL DEFAULT nextval('public.tipo_cliente_id_seq'::regclass),
  "nombre" character varying(50) NOT NULL,
  CONSTRAINT "tipo_cliente_nombre_key" UNIQUE (nombre),
  CONSTRAINT "tipo_cliente_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."tipo_cliente"
  ENABLE ROW LEVEL SECURITY;

ALTER SEQUENCE "public"."cliente_id_seq" OWNED BY "public"."cliente"."id";

ALTER SEQUENCE "public"."comuna_id_seq" OWNED BY "public"."comuna"."id";

ALTER SEQUENCE "public"."detalle_pedido_id_seq" OWNED BY "public"."detalle_pedido"."id";

ALTER SEQUENCE "public"."pedido_id_seq" OWNED BY "public"."pedido"."id";

ALTER SEQUENCE "public"."producto_id_seq" OWNED BY "public"."producto"."id";

ALTER SEQUENCE "public"."sector_id_seq" OWNED BY "public"."sector"."id";

ALTER SEQUENCE "public"."tipo_cliente_id_seq" OWNED BY "public"."tipo_cliente"."id";

CREATE TYPE "public"."dia_semana" AS ENUM (
  'Lunes',
  'Martes',
  'Miercoles',
  'Jueves',
  'Viernes'
);

ALTER TABLE "public"."cliente"
  ADD COLUMN "dia_reparto" public.dia_semana;

CREATE TYPE "public"."estado_pedido" AS ENUM (
  'pendiente',
  'confirmado',
  'en_despacho',
  'entregado',
  'cancelado'
);

ALTER TABLE "public"."pedido"
  ADD COLUMN "estado" public.estado_pedido NOT NULL DEFAULT 'pendiente'::public.estado_pedido;

CREATE OR REPLACE FUNCTION public.rls_auto_enable()
  RETURNS event_trigger
  LANGUAGE plpgsql
  SECURITY DEFINER
  SET search_path TO 'pg_catalog'
  AS $function$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$function$;

ALTER TABLE "public"."cliente_producto_habitual"
  ADD CONSTRAINT "cliente_producto_habitual_cliente_id_fkey" FOREIGN KEY (cliente_id) REFERENCES public.cliente(id) ON DELETE CASCADE;

ALTER TABLE "public"."pedido"
  ADD CONSTRAINT "pedido_cliente_id_fkey" FOREIGN KEY (cliente_id) REFERENCES public.cliente(id);

ALTER TABLE "public"."detalle_pedido"
  ADD CONSTRAINT "detalle_pedido_pedido_id_fkey" FOREIGN KEY (pedido_id) REFERENCES public.pedido(id) ON DELETE CASCADE;

ALTER TABLE "public"."cliente_producto_habitual"
  ADD CONSTRAINT "cliente_producto_habitual_producto_id_fkey" FOREIGN KEY (producto_id) REFERENCES public.producto(id) ON DELETE CASCADE;

ALTER TABLE "public"."detalle_pedido"
  ADD CONSTRAINT "detalle_pedido_producto_id_fkey" FOREIGN KEY (producto_id) REFERENCES public.producto(id);

ALTER TABLE "public"."sector"
  ADD CONSTRAINT "sector_comuna_id_fkey" FOREIGN KEY (comuna_id) REFERENCES public.comuna(id);

ALTER TABLE "public"."cliente"
  ADD CONSTRAINT "cliente_sector_id_fkey" FOREIGN KEY (sector_id) REFERENCES public.sector(id);

ALTER TABLE "public"."cliente"
  ADD CONSTRAINT "cliente_tipo_cliente_id_fkey" FOREIGN KEY (tipo_cliente_id) REFERENCES public.tipo_cliente(id);

CREATE EVENT TRIGGER "ensure_rls"
  ON ddl_command_end
  WHEN TAG IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
  EXECUTE FUNCTION "public"."rls_auto_enable"();

GRANT EXECUTE ON FUNCTION "public"."rls_auto_enable"() TO PUBLIC, "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."cliente_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."comuna_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."detalle_pedido_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."pedido_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."producto_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."sector_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."tipo_cliente_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."cliente" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."cliente_producto_habitual" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."comuna" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."detalle_pedido" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."pedido" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."producto" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."sector" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."tipo_cliente" TO "anon", "authenticated", "postgres", "service_role";

GRANT USAGE ON TYPE "public"."dia_semana" TO "postgres";

GRANT USAGE ON TYPE "public"."estado_pedido" TO "postgres";

