# n8n — entorno local de SIGPA

Entorno local (Docker) de [n8n](https://n8n.io) que va a correr el workflow de
optimización de rutas (EP-04). Para desplegarlo en un host público (Render) ver
[`DESPLIEGUE.md`](DESPLIEGUE.md) y el [`Dockerfile`](Dockerfile).

## Cómo encaja en la arquitectura

La ruta se genera a pedido de la ejecutiva desde el panel, no con un cron:

```
[Panel] --POST /rutas/planificar (JWT)--> [FastAPI] --POST + X-Route-Secret--> [n8n: webhook]
                                              ^                                     |
                                              |       geocodifica (OpenRouteService)
                                              |       y optimiza el orden de las paradas
                                              +---------- respuesta JSON <----------+
```

- **El backend FastAPI arma las paradas** desde la BD con los pedidos que eligió la ejecutiva,
  llama al webhook de n8n de forma síncrona, **valida** la respuesta y la **persiste** (pedidos a
  "confirmado" con su `orden_entrega`). Ver `Backend/app/services/ruta_service.py`.
- **n8n geocodifica y optimiza:** recibe las paradas, geocodifica las que vienen sin coordenadas
  (OpenRouteService), calcula el orden desde el depósito y responde. Las coordenadas del depósito
  viven solo en n8n, en las variables de entorno `DEPOT_LAT` / `DEPOT_LON` (las lee el nodo Config).
- **n8n no escribe en la base de datos** ni se conecta a Supabase.

### Contrato con el backend

Request que envía el backend (`POST` al webhook, header `X-Route-Secret`):

```json
{"paradas": [{"pedido_id": 12, "direccion_texto": "Santa Maria 793", "latitud": -33.1229, "longitud": -71.5709},
             {"pedido_id": 15, "direccion_texto": "Los Pinos 456, Quilpué", "latitud": null, "longitud": null},
             {"pedido_id": 18, "direccion_texto": "Pasaje sin número", "latitud": null, "longitud": null}]}
```

Respuesta que debe devolver n8n (200, JSON):

```json
{"ruta": [{"pedido_id": 15, "orden_entrega": 1, "latitud": -33.0478, "longitud": -71.4412},
          {"pedido_id": 12, "orden_entrega": 2, "latitud": -33.1229, "longitud": -71.5709}],
 "sin_resolver": [{"pedido_id": 18, "motivo": "No se pudo geocodificar la dirección"}]}
```

Reglas que el backend valida (si no se cumplen, no guarda nada y responde error al panel):

- Cada pedido enviado aparece **exactamente una vez**, en `ruta` o en `sin_resolver`; ningún
  `pedido_id` que no se haya enviado.
- `orden_entrega`: enteros consecutivos 1..N, sin repetir ni huecos (1 = primera parada).
- `latitud` en [-90, 90] y `longitud` en [-180, 180].
- El webhook debe responder antes de `N8N_ROUTE_TIMEOUT_SECONDS` del backend (default 45 s; en el
  despliegue actual 120 s, ver *Tiempo* más abajo).

## Levantarlo

```bash
cp .env.example .env      # completar DEPOT_LAT, DEPOT_LON, OPENROUTESERVICE_API_KEY (N8N_VERSION ya trae 2.42.5)
docker compose up -d
docker compose ps         # esperar a que el estado sea "healthy"
```

Detenerlo: `docker compose down` (los datos se conservan en el volumen `sigpa_n8n_data`).
Borrar todo, incluido el volumen: `docker compose down -v` (**se pierden credenciales y workflows no exportados**).

Huso horario: `GENERIC_TIMEZONE` y `TZ` están en `America/Santiago`, así los Cron de n8n
se interpretan en hora de Chile, igual que el resto del proyecto.

## Entrar al editor

1. Abrir <http://localhost:5678>.
2. La primera vez, n8n muestra un formulario para crear la cuenta **owner**: completar
   email y password.
3. Guardar esas credenciales en un gestor de contraseñas. **No** van en el `.env`: desde
   n8n 1.0 el login no se configura por variables de entorno.

La cuenta queda guardada en el volumen `sigpa_n8n_data`, así que sobrevive a
`docker compose down` / `up`. Con `docker compose down -v` se borra y hay que crearla de nuevo.

Para probar con el backend corriendo en local, el backend debe apuntar al webhook de este
contenedor: `N8N_ROUTE_WEBHOOK_URL=http://localhost:5678/webhook/sigpa-ruta` (o la URL de test
`/webhook-test/...` mientras el workflow no esté activo).

## Workflows: exportar e importar

El volumen de Docker no se versiona, así que cada workflow se exporta como JSON a
[`workflows/`](workflows/README.md) y se commitea. Ahí está el detalle de exportar/importar
desde el editor y desde la CLI.

## Workflow `optimizacion-rutas`

Archivo: [`workflows/optimizacion-rutas.json`](workflows/optimizacion-rutas.json). Webhook
`POST /webhook/sigpa-ruta`, protegido con el header `X-Route-Secret` (si no coincide, n8n
responde 403 sin ejecutar nada).

```
Webhook → Config → Normalizar → ¿Entrada válida? ─no→ 400 entrada_invalida / 500 configuracion_incompleta
  → ¿Hay que geocodificar? ─sí→ Separar pendientes → Geocodificar (ORS) → Evaluar geocodificación ─┐
                           └no──────────────────────────────────────────────────────────────────────┤
  → Preparar optimización → ¿Hay paradas resueltas? ─sí→ Optimizar (ORS) ─error→ 502 optimizacion_fallida
                                                    └no──────────┬──────────┘ok
                                                                 → Armar respuesta → ¿Contrato válido?
                                                                     ─sí→ 200 {ruta, sin_resolver}
                                                                     └no→ 502 optimizacion_fallida
```

- Las paradas que traen coordenadas se usan tal cual (y se devuelven iguales). Las demás se
  geocodifican de a una, con 1500 ms entre requests (cuota gratuita de ORS), sesgadas hacia el
  depósito y limitadas a `PAIS`.
- Sin depósito válido (`DEPOT_LAT`/`DEPOT_LON` ausentes, no numéricos o 0/0) o sin
  `OPENROUTESERVICE_API_KEY`, responde 500 `configuracion_incompleta` sin llamar a ORS.
- Una dirección geocodificada solo se acepta si el resultado tiene `layer` = `address` **y**
  `confidence` >= `CONFIDENCE_MIN`. El `confidence` de ORS no basta por sí solo: en pruebas
  reales, "Pasaje Zxqwyrt 98765, Villa Inexistente" volvió con `confidence` 1 y `match_type`
  `exact`, pero el punto era el centroide de una calle cualquiera de otra comuna (`layer`
  `street`, `accuracy` `centroid`). Cualquier otro layer (`street`, `locality`, `region`,
  `venue`, etc.) se descarta, sea cual sea su `confidence`.
- Van a `sin_resolver` con motivo `dirección no encontrada` (sin resultado o sin dirección),
  `dirección no encontrada con precisión suficiente` (el resultado no es de layer `address`),
  `dirección ambigua` (layer `address` pero `confidence` < `CONFIDENCE_MIN`),
  `error de geocodificación` (falla la llamada) o `no asignable en la optimización` (ORS no la
  pudo asignar).
- Antes de responder 200 el workflow verifica el mismo contrato que el backend; si algo no
  cuadra responde 502 en vez de una ruta parcial.
- Un `pedido_id` repetido en la entrada se toma una sola vez.

**Tiempo:** el backend espera `N8N_ROUTE_TIMEOUT_SECONDS` (default 45 s). Cada parada sin
coordenadas suma ~1,5 s más la latencia de ORS (timeout 8 s por llamada) y la optimización tiene
timeout de 20 s; el workflow se corta a los 40 s. Con más de ~15 paradas sin coordenadas en una
misma solicitud se arriesga el límite.

En el despliegue actual el backend usa `N8N_ROUTE_TIMEOUT_SECONDS=120` para cubrir el arranque en
frío de n8n en el plan gratuito de Render. Riesgos: el proxy del Static Site de Render o el panel
pueden cortar antes de 120 s, y el candado de planificación queda tomado ese tiempo (otra
planificación recibe 409). Recomendación: mantener n8n despierto y bajar el valor cuando ya no
duerma. Detalle en [DESPLIEGUE.md](DESPLIEGUE.md), puntos 6 y 7.

## Qué configurar antes de usarlo

**Variables de entorno** (en el `.env` local, o en el servicio al desplegar). El workflow las lee
con `{{ $env.X }}`, por eso `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` (ya está en el compose y el
Dockerfile):

| Variable | Obligatoria | Uso |
|---|---|---|
| `DEPOT_LAT`, `DEPOT_LON` | Sí | Coordenadas del depósito (inicio y fin de la ruta). |
| `OPENROUTESERVICE_API_KEY` | Sí | API key de <https://openrouteservice.org> (nivel gratuito). Va en el header `Authorization` de **Geocodificar (ORS)** y **Optimizar (ORS)**. |
| `CONFIDENCE_MIN` | No (0.6) | Confianza mínima para aceptar una geocodificación de layer `address`. Un valor no numérico se ignora y se usa 0.6. |
| `PAIS` | No (CL) | País al que se limita la geocodificación. |
| `N8N_VERSION` | Sí (compose) | Versión fija de la imagen `n8nio/n8n`: **2.42.5**, la misma del `Dockerfile` y de producción (el workflow se validó en local con 2.41.6). No bajar de versión una base ya migrada. |

Después de importar el workflow en el editor:

1. **Credencial `SIGPA X-Route-Secret`.** Crear la credencial: *Credentials → Add credential → Header Auth*. Tiene **dos campos de
   nombre distintos**, no confundirlos:
   - **Título de la credencial** (el nombre con que aparece en n8n): `SIGPA X-Route-Secret`.
   - Campo **Name** del Header Auth: es el nombre del header HTTP y debe ser **exactamente**
     `X-Route-Secret`. Con cualquier otro nombre el webhook responde 403 aunque el valor sea
     correcto.
   - Campo **Value**: el mismo valor que `N8N_ROUTE_WEBHOOK_SECRET` del backend.
   Asignarla en el nodo **Webhook**. Es la única credencial del workflow (n8n exige credencial
   para la autenticación del webhook).
2. **Publicar (Publish) el workflow** para que responda en `/webhook/sigpa-ruta`, y en el backend
   configurar `N8N_ROUTE_WEBHOOK_URL` con esa URL.
   En n8n 2.x cualquier cambio en el workflow o en la asignación de credenciales queda en
   **borrador** hasta pulsar **Publish** de nuevo: la versión publicada es la que responde en
   `/webhook/sigpa-ruta`. Después de corregir algo (por ejemplo, la credencial), volver a publicar.

Si venías de la versión anterior del workflow (API key en la credencial `OpenRouteService API
key` y depósito escrito en el nodo Config): copiar la API key a `OPENROUTESERVICE_API_KEY` en el
`.env`, reiniciar el contenedor (`docker compose up -d`), reimportar el workflow, asignar la
credencial del Webhook y publicar de nuevo. La credencial vieja ya no se usa y se puede borrar
desde el editor.

El nodo Webhook trae datos de prueba fijados (4 paradas: dos con coordenadas, una real de Viña
del Mar sin coordenadas y una inventada) para ejecutarlo desde el editor con **Test workflow**.
La inventada (pedido 104, "Pasaje Zxqwyrt 98765, Villa Inexistente") es el caso de regresión de
la regla de layer: debe terminar en `sin_resolver` con motivo `dirección no encontrada con
precisión suficiente`, no en la ruta.
