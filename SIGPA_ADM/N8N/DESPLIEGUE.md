# Despliegue de n8n (workflow de rutas) en un host público

Guía para desplegar el n8n de SIGPA en Render (u otro host con Docker) sin tocar la lógica del
workflow. El workflow `workflows/optimizacion-rutas.json` expone `POST /webhook/sigpa-ruta`: lo
llama el backend FastAPI desde `POST /rutas/planificar`, geocodifica con OpenRouteService (ORS),
optimiza el orden y responde. n8n no escribe en la base de datos de SIGPA.

El contrato con el backend está cerrado (ver `README.md` de esta carpeta): no hay que cambiar
nodos, payload ni respuesta. Todo lo que varía por entorno se carga como variable de entorno.

## 1. Imagen

El servicio se construye con el [`Dockerfile`](Dockerfile) de esta carpeta (`FROM n8nio/n8n:<versión>`).

- **Fijar la versión antes del primer deploy:** el Dockerfile trae `ARG N8N_VERSION=REEMPLAZAR_VERSION_N8N`.
  Reemplazar ese placeholder por la versión de n8n con que se probó el workflow (la misma que
  `N8N_VERSION` del `.env` local) y commitear. Con el placeholder el build falla a propósito.
  No usar `latest`: una actualización de n8n puede cambiar el comportamiento de los nodos.
- En Render: servicio tipo **Web Service**, runtime **Docker**, *Root Directory* `SIGPA_ADM/N8N`,
  *Dockerfile Path* `./Dockerfile`.
- n8n escucha en el puerto `5678`. Definir `PORT=5678` en el servicio para que Render enrute a
  ese puerto.
- *Health Check Path*: `/healthz`.

## 2. Variables de entorno

### Obligatorias

| Variable | Valor | Notas |
|---|---|---|
| `DEPOT_LAT` | Latitud del depósito (número, ej. `-33.0xxxxx`) | Si falta, no es número o el par es 0/0, el webhook responde 500 `configuracion_incompleta`. Pedir el valor al equipo; no está en el repo. |
| `DEPOT_LON` | Longitud del depósito (número) | Igual que `DEPOT_LAT`. |
| `OPENROUTESERVICE_API_KEY` | API key de <https://openrouteservice.org> | La usan los nodos **Geocodificar (ORS)** y **Optimizar (ORS)** en el header `Authorization`. Si falta: 500 `configuracion_incompleta`. |
| `N8N_ENCRYPTION_KEY` | Cadena aleatoria larga (ej. `openssl rand -hex 32`) | **Fija y guardada** en un gestor de contraseñas. n8n cifra las credenciales con ella: si cambia (o se pierde) la credencial `SIGPA X-Route-Secret` deja de poder leerse y hay que recrearla. |
| `WEBHOOK_URL` | URL pública HTTPS del servicio, ej. `https://<host>/` | n8n la usa para armar las URLs de los webhooks detrás del proxy de Render. |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE` | `false` | Sin esto el workflow no puede leer `$env.*` y responde 500 `configuracion_incompleta`. Ya viene en el Dockerfile. |
| `GENERIC_TIMEZONE` / `TZ` | `America/Santiago` | Hora de Chile, como el resto del proyecto. Ya vienen en el Dockerfile. |
| `EXECUTIONS_TIMEOUT` | `40` | Corta cada ejecución a los 40 s, antes del timeout de 45 s del backend. Ya viene en el Dockerfile (el workflow además trae su propio corte de 40 s). |
| `N8N_DIAGNOSTICS_ENABLED` | `false` | Sin telemetría a n8n. Ya viene en el Dockerfile. |
| `PORT` | `5678` | Solo en Render (ver punto 1). |

### Opcionales

| Variable | Default | Notas |
|---|---|---|
| `CONFIDENCE_MIN` | `0.6` | Confianza mínima de ORS para aceptar una dirección (además de exigir layer `address`). Un valor no numérico se ignora y se usa 0.6. |
| `PAIS` | `CL` | País al que se limita la geocodificación. |
| `N8N_PROXY_HOPS` | — | `1` detrás del proxy de Render, para que n8n tome bien la IP del cliente. |
| `EXECUTIONS_DATA_PRUNE` / `EXECUTIONS_DATA_MAX_AGE` | — | Ej. `true` / `168` (horas): borra ejecuciones antiguas. Las ejecuciones guardan las direcciones de los pedidos. |
| `DB_TYPE` y `DB_POSTGRESDB_*` | SQLite en disco | Ver punto 3. |

Nunca escribir estos valores en el repo (Dockerfile, README, JSON del workflow): solo en el panel
de variables del servicio.

## 3. Persistencia (decidir antes de configurar)

n8n guarda la cuenta owner, el workflow importado, su estado activo y la credencial
`SIGPA X-Route-Secret` en su base de datos. Por defecto es un SQLite en `/home/node/.n8n`, dentro
del contenedor. **Sin disco persistente ni base de datos externa, todo eso se pierde en cada
reinicio o deploy** (y en Render el servicio se reinicia en cada deploy y puede reiniciarse solo):
habría que crear la cuenta, importar, recrear la credencial y activar de nuevo cada vez.

Hay dos opciones; elegir una según plan y costos:

**A. Base de datos Postgres externa**

```
DB_TYPE=postgresdb
DB_POSTGRESDB_HOST=<host>
DB_POSTGRESDB_PORT=5432
DB_POSTGRESDB_DATABASE=<base>
DB_POSTGRESDB_USER=<usuario>
DB_POSTGRESDB_PASSWORD=<password>
DB_POSTGRESDB_SCHEMA=<schema>          # opcional, default public
DB_POSTGRESDB_SSL_ENABLED=true         # si el proveedor exige SSL
```

- n8n crea sus propias tablas. Usar una base o al menos un schema dedicado a n8n, separado de las
  tablas de SIGPA.
- No necesita disco: el contenedor puede reiniciarse sin perder nada (siempre que
  `N8N_ENCRYPTION_KEY` no cambie).

**B. Disco persistente**

- Montar un disco en `/home/node/.n8n` (en Render: *Disks* del servicio, requiere un plan pago).
- Se sigue usando SQLite. Con disco, Render no hace deploys sin corte y el servicio queda en una
  sola instancia.

En cualquiera de las dos, `N8N_ENCRYPTION_KEY` debe estar definida desde el primer arranque.

## 4. Puesta en marcha

1. Desplegar con las variables del punto 2 (y las de persistencia del punto 3).
2. Abrir `https://<host>/` y crear la cuenta **owner** (email + password fuerte; el editor queda
   expuesto en internet). Guardarla en un gestor de contraseñas.
3. Importar el workflow: menú **⋯ → Import from File** → `workflows/optimizacion-rutas.json`.
4. Crear la credencial **`SIGPA X-Route-Secret`**: *Credentials → Add credential → Header Auth*,
   Name `X-Route-Secret`, Value = el mismo valor que `N8N_ROUTE_WEBHOOK_SECRET` del backend.
   Debe llamarse exactamente así.
5. Abrir el nodo **Webhook** del workflow y asignarle esa credencial. Guardar.
6. **Publicar (activar)** el workflow. Solo activo responde en `/webhook/sigpa-ruta`.

No hay otras credenciales: la API key de ORS y el depósito vienen de las variables de entorno.

## 5. Probar con curl

Usar la URL de producción `https://<host>/webhook/sigpa-ruta`, **no** `/webhook-test/` (esa solo
funciona con el editor abierto escuchando un test). Reemplazar `<SECRETO>` por el valor de la
credencial.

**Ruta OK (200).** Dos paradas con coordenadas (datos de ejemplo): no geocodifica, pero sí llama
a la optimización de ORS.

```bash
curl -i -X POST "https://<host>/webhook/sigpa-ruta" \
  -H "Content-Type: application/json" \
  -H "X-Route-Secret: <SECRETO>" \
  -d '{"paradas": [
        {"pedido_id": 1, "direccion_texto": "Calle Ejemplo 123", "latitud": -33.0300, "longitud": -71.5500},
        {"pedido_id": 2, "direccion_texto": "Calle Ejemplo 456", "latitud": -33.0450, "longitud": -71.5200}
      ]}'
```

Respuesta esperada: `200` con

```json
{"ruta": [{"pedido_id": 2, "orden_entrega": 1, "latitud": -33.045, "longitud": -71.52},
          {"pedido_id": 1, "orden_entrega": 2, "latitud": -33.03, "longitud": -71.55}],
 "sin_resolver": []}
```

(el orden depende de la ubicación del depósito; cada pedido aparece una vez y `orden_entrega`
va de 1 a N).

**Secreto incorrecto (403).** El mismo comando con `-H "X-Route-Secret: incorrecto"` debe
responder `403` sin ejecutar el workflow.

**Cuerpo inválido (400).**

```bash
curl -i -X POST "https://<host>/webhook/sigpa-ruta" \
  -H "Content-Type: application/json" -H "X-Route-Secret: <SECRETO>" \
  -d '{"otra_cosa": 1}'
```

Respuesta esperada: `400` con `{"error": "entrada_invalida", "detalle": "..."}`.

**Configuración incompleta (500).** Con `DEPOT_LAT`/`DEPOT_LON` sin definir (o
`OPENROUTESERVICE_API_KEY` vacía), cualquier llamada con el secreto correcto responde `500` con
`{"error": "configuracion_incompleta", "detalle": "..."}`; el `detalle` dice qué variable falta.
Esta verificación va antes que la del cuerpo, así que con la configuración incompleta incluso un
cuerpo inválido da 500. Conviene probarlo una vez antes de cargar el depósito.

## 6. Arranque en frío

Si el plan del host duerme el servicio por inactividad (en Render, el plan gratuito lo hace tras
unos minutos sin tráfico), la primera llamada tiene que esperar a que n8n arranque. Eso puede
consumir los 45 s que espera el backend: el panel recibe un 504 ("no respondió a tiempo") aunque
n8n después termine bien. Nada se guarda en ese caso y la ejecutiva puede reintentar.

Recomendado: un plan que no duerma, o mantenerlo despierto con un ping periódico a
`https://<host>/healthz`, o al menos hacer un ping a `/healthz` antes de planificar.

## 7. Valores para el backend (Render)

En las variables del servicio del backend:

| Variable | Valor |
|---|---|
| `N8N_ROUTE_WEBHOOK_URL` | `https://<host>/webhook/sigpa-ruta` |
| `N8N_ROUTE_WEBHOOK_SECRET` | El mismo valor de la credencial `SIGPA X-Route-Secret` |

El backend espera la respuesta hasta `N8N_ROUTE_TIMEOUT_SECONDS` (default 45 s). Si el secreto
del backend queda vacío, el backend responde 503 sin llamar a n8n; si no coincide con el de n8n,
el panel ve un 502 "Servicio de rutas mal configurado o rechazó la solicitud" (código 403).
