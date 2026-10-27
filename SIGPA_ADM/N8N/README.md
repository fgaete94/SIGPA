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
- **n8n geocodifica y optimiza:** recibe las paradas (cada una con su peso), geocodifica las que
  vienen sin coordenadas (OpenRouteService), las reparte en uno o más viajes según la capacidad del
  camión, calcula el orden de cada viaje desde el depósito y responde. Las coordenadas del depósito
  y de la casa del despachador viven solo en n8n, en las variables de entorno `DEPOT_LAT` /
  `DEPOT_LON` y `CASA_LAT` / `CASA_LON`; el backend nunca las conoce.
- **n8n no escribe en la base de datos** ni se conecta a Supabase.

### Contrato con el backend

Request que envía el backend (`POST` al webhook, header `X-Route-Secret`):

```json
{"paradas": [{"pedido_id": 12, "direccion_texto": "Santa Maria 793", "latitud": -33.1229, "longitud": -71.5709, "peso_kg": 400},
             {"pedido_id": 15, "direccion_texto": "Los Pinos 456, Quilpué", "latitud": null, "longitud": null, "peso_kg": 300},
             {"pedido_id": 18, "direccion_texto": "Pasaje sin número", "latitud": null, "longitud": null, "peso_kg": 200}],
 "reglas": {"capacidad_kg": 1100, "umbral_casa_kg": 800, "max_viajes": 3}}
```

- `peso_kg` es **obligatorio** en cada parada: entero >= 1. Si falta o es inválido en alguna, n8n
  responde 400 `entrada_invalida`.
- `reglas` es opcional. Si no viene (o falta un campo), n8n usa las variables de entorno
  `CAPACIDAD_KG`, `UMBRAL_CASA_KG` y `MAX_VIAJES` y, si tampoco existen, los defaults 1100 / 800 / 3.
  Una regla presente pero inválida (no entera o < 1) es un 400.

Respuesta que debe devolver n8n (200, JSON):

```json
{"ruta": [{"pedido_id": 15, "viaje": 1, "orden_entrega": 1, "latitud": -33.0478, "longitud": -71.4412},
          {"pedido_id": 12, "viaje": 1, "orden_entrega": 2, "latitud": -33.1229, "longitud": -71.5709}],
 "viajes": [{"viaje": 1, "peso_kg": 700, "termina_en": "planta", "total_paradas": 2}],
 "sin_resolver": [{"pedido_id": 18, "motivo": "No se pudo geocodificar la dirección"}]}
```

Reglas que el backend valida (si no se cumplen, no guarda nada y responde error al panel):

- Cada pedido enviado aparece **exactamente una vez**, en `ruta` o en `sin_resolver`; ningún
  `pedido_id` que no se haya enviado.
- `viajes` es obligatorio: números consecutivos 1..V con V <= `max_viajes`; todo viaje tiene al
  menos una parada y cada parada de `ruta` referencia un viaje existente.
- `orden_entrega`: enteros consecutivos 1..N **dentro de cada viaje**, sin repetir ni huecos
  (1 = primera parada del viaje).
- El peso de cada viaje (recalculado por el backend) es <= `capacidad_kg` y coincide con el
  `peso_kg` que informa n8n. El último viaje es el más liviano.
- `termina_en` es `planta` o `casa`. `casa` solo en el último viaje, solo con V >= 2 y solo si su
  peso es **estrictamente menor** que `umbral_casa_kg`; y si el último viaje (con V >= 2) pesa
  menos que el umbral, **debe** ser `casa`. Un único viaje termina siempre en la planta.
- `latitud` en [-90, 90] y `longitud` en [-180, 180].
- El webhook debe responder antes de `N8N_ROUTE_TIMEOUT_SECONDS` del backend (default 45 s; en el
  despliegue actual 120 s, ver *Tiempo* más abajo).

## Levantarlo

```bash
cp .env.example .env      # completar DEPOT_LAT, DEPOT_LON, OPENROUTESERVICE_API_KEY (y CASA_LAT/CASA_LON; N8N_VERSION ya trae 2.42.5)
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
  → Preparar optimización → ¿Carga válida? ─no→ 400 carga_excede_capacidad
                              └sí→ ¿Hay paradas resueltas? ─no───────────────────────────────────────┐
                                         └sí→ Optimizar (ORS) ─error→ Evaluar error de optimización  │
                                               │      └ ¿Apartar parada y reintentar? ─sí→ Optimizar (ORS) sin esa parada
                                               │                                      └no→ Armar respuesta → 502 (otro error)
                                               → Evaluar reparto → ¿Reintentar optimización?          │
                                                    ├sí (sobran paradas y k < max_viajes) → Optimizar (ORS) con k+1
                                                    └no→ ¿Error de configuración? ─sí→ 500 configuracion_incompleta (falta CASA_*)
                                                          └no→ ¿Terminar en casa? ─sí→ Preparar viaje a casa → Optimizar a casa (ORS) ─error→ 502
                                                                                └no──────────────┐        │ok
                                                                                                 → Armar respuesta → ¿Contrato válido?
                                                                                                     ─sí→ 200 {ruta, viajes, sin_resolver}
                                                                                                     └no→ 502 optimizacion_fallida
```

**Reparto en viajes** (después de geocodificar, sobre las paradas resueltas):

1. Una parada con `peso_kg` mayor que la capacidad va a `sin_resolver` con motivo `pedido excede la
   capacidad del camión`. Si el peso total del resto supera `capacidad_kg * max_viajes`, responde
   400 `carga_excede_capacidad` sin llamar a la optimización.
2. `k = ceil(peso total / capacidad_kg)`. Llama a ORS Optimization con `k` vehículos (ids 1..k,
   salida y llegada en el depósito, `capacity` = capacidad) y cada parada como job con
   `amount = [peso_kg]`. Si ORS deja paradas sin asignar y `k < max_viajes`, reintenta con `k + 1`
   (el nodo **Evaluar reparto** vuelve a **Optimizar (ORS)**; el número de intento sale de
   `$runIndex`). Lo que siga sin asignar al final va a `sin_resolver` con el motivo
   `no asignable en la optimización`.
3. Con 2 o más viajes se toma el más liviano (en empate, el de mayor número de vehículo). Si pesa
   **estrictamente menos** que `umbral_casa_kg`, se valida `CASA_LAT`/`CASA_LON` (si faltan o son
   inválidas: 500 `configuracion_incompleta` con detalle `Definir CASA_LAT y CASA_LON`, sin
   reoptimizar) y se reoptimizan **solo** las paradas de ese viaje con 1 vehículo (salida en el
   depósito, llegada en la casa): el reparto de pedidos entre viajes no cambia, solo su orden. Si
   hay un solo viaje, o el más liviano pesa >= umbral, no se necesita `CASA_*`.
4. **Punto sin ruta alcanzable.** Si la llamada de optimización falla con el error de ORS
   `code 3 "Unfound route(s) from location [lon,lat]"` (una parada en el mar, por ejemplo), el nodo
   **Evaluar error de optimización** extrae las coordenadas del mensaje, busca la parada cuyo punto
   coincide (tolerancia de 0,0001° ≈ 11 m, porque ORS puede informar menos decimales), la mueve a
   `sin_resolver` con motivo `ubicación sin ruta alcanzable` y **reintenta con las demás** (mismo
   número de vehículos; el peso se recalcula solo, porque el reparto se arma con las paradas que
   quedan). Máximo **2** paradas apartadas por este motivo por solicitud; con una tercera, con un
   error que no se pueda asociar a ninguna parada, o con cualquier otra falla de ORS (timeout, 429,
   5xx), responde 502 `optimizacion_fallida`. Estos reintentos no cuentan como los de `k + 1`. Si
   las apartadas eran todas las paradas, responde 200 sin viajes. La reoptimización hacia la casa no
   tiene este manejo: sus paradas ya se rutearon.
5. Numeración: los viajes que terminan en la planta se numeran de mayor a menor peso (1, 2, ...);
   el viaje que termina en la casa, si existe, es el último (y el más liviano). `orden_entrega`
   sale del orden de los steps de ORS (1..N por viaje).
6. **Armar respuesta** y **¿Contrato válido?** verifican el contrato completo (cada pedido una vez,
   viajes 1..V con V <= `max_viajes`, orden 1..N por viaje, peso por viaje <= capacidad, último
   viaje el más liviano, `casa` según la regla en ambos sentidos). Si algo no cuadra, 502
   `optimizacion_fallida`, como antes.

Nota: el chequeo de `CASA_*` ocurre **después** de la optimización de reparto (hasta entonces no
se sabe cuál es el viaje más liviano ni cuánto pesa), así que en ese error ya se hizo la llamada
de reparto a ORS; lo que no se hace es la reoptimización hacia la casa. Para no repetirlo en cada
planificación, definir `CASA_LAT`/`CASA_LON` en el despliegue.

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
  `error de geocodificación` (falla la llamada), `no asignable en la optimización` (ORS no la
  pudo asignar), `pedido excede la capacidad del camión` (pesa más que un viaje) o
  `ubicación sin ruta alcanzable` (ORS no encuentra ruta hasta su punto).
- Antes de responder 200 el workflow verifica el mismo contrato que el backend; si algo no
  cuadra responde 502 en vez de una ruta parcial.
- Un `pedido_id` repetido en la entrada se toma una sola vez.

**Tiempo:** el backend espera `N8N_ROUTE_TIMEOUT_SECONDS` (default 45 s) y el workflow se corta a
los 40 s (`executionTimeout` y `EXECUTIONS_TIMEOUT`). Cada parada sin coordenadas suma ~1,5 s más la
latencia de ORS (timeout 8 s por llamada, sin cambios). Con más de ~15 paradas sin coordenadas en
una misma solicitud se arriesga el límite.

Cada planificación puede hacer hasta `max_viajes + 1 + 2` llamadas de optimización: los intentos
desde `k` hasta `max_viajes` (peor caso con `k = 1`: 3), una reoptimización hacia la casa y un
reintento por cada parada apartada por no tener ruta alcanzable (máximo 2); es decir 6 con los
defaults (más las geocodificaciones). Para que el peor caso termine antes del corte y
responda 502 `optimizacion_fallida` en vez de colgarse, el timeout de cada llamada de optimización
**se calcula**, no es fijo:

- `Normalizar` anota la hora de entrada (`inicio_ms`) y las llamadas de optimización deben terminar
  dentro de un plazo de **36 s** desde ahí (40 s de corte menos 4 s para armar y enviar la
  respuesta). Lo que gastó la geocodificación ya está descontado.
- Antes de cada llamada se reparte lo que queda entre las llamadas que aún podrían hacer falta:
  `timeout = min(10 s, restante / llamadas_pendientes)`, donde las pendientes son los intentos que
  faltan hasta `max_viajes`, la reoptimización a la casa y las paradas que aún se podrían apartar.
  Como cada llamada recibe como máximo su parte de lo que queda, la suma nunca supera el plazo.
  Peor caso con los defaults y sin geocodificar: 6 llamadas de 6 s = 36 s (antes de la parte de
  puntos sin ruta eran 4 de 9 s). Con 6 llamadas el timeout por llamada baja de 9 a 6 s; si ORS
  suele tardar más de 6 s, subir `PLAZO_MS` no es posible sin subir el corte global de 40 s.
- Si lo que queda no alcanza para 2 s por llamada, no se llama a ORS: se responde 502
  `optimizacion_fallida`. El timeout se aplica con la opción *Timeout* de los nodos **Optimizar
  (ORS)** y **Optimizar a casa (ORS)** (expresiones `$json.timeout_ms` / `$json.timeout_casa_ms`);
  una llamada que lo excede sale por la rama de error y responde 502.
- Los números (36 s, 10 s, 2 s) están al inicio de los nodos **Preparar optimización** y **Evaluar
  reparto** (`PLAZO_MS`, `TIMEOUT_MAX_MS`, `TIMEOUT_MIN_MS`). Si se cambia el corte global, ajustarlos.

**Límites de ORS.** Según una respuesta del equipo de openrouteservice en su foro (la página de
restricciones no lo documenta), el endpoint de optimización del plan estándar admite como máximo
**3 vehículos** (y 50 ubicaciones) por solicitud; con más, devuelve 413 ("Too many vehicles"). Por eso
`max_viajes` no debería pasar de 3 con el plan gratuito. **Verificarlo** en la documentación oficial
o contra su cuenta antes de subirlo. Además, cada planificación gasta hasta `max_viajes + 1` de las
llamadas de optimización de la cuota diaria
(más con paradas sin ruta alcanzable, que gastan una llamada fallida cada una).

En el despliegue actual el backend usa `N8N_ROUTE_TIMEOUT_SECONDS=120` para cubrir el arranque en
frío de n8n en el plan gratuito de Render. Riesgos: el proxy del Static Site de Render o el panel
pueden cortar antes de 120 s, y el candado de planificación queda tomado ese tiempo (otra
planificación recibe 409). Recomendación: mantener n8n despierto y bajar el valor cuando ya no
duerma. Detalle en [DESPLIEGUE.md](DESPLIEGUE.md), puntos 6 y 7.

### Cómo se validó el reparto

Lo que sigue se probó sin n8n ni ORS, con un arnés que recorre el **grafo real** del JSON
(conexiones, nodos IF y Set, y el JavaScript real de los nodos Code ejecutado en V8) con un ORS
simulado (empaquetado first-fit y el error `code 3`). El arnés es estricto con la lectura de nodos
por nombre: `$('Nodo')` falla si el nodo tiene varias salidas (sin indicar la salida) o se ejecutó
más de una vez, y con ese criterio reproduce el bug descrito abajo. Cubre peso, reparto en viajes,
reintento con `k + 1`, numeración, `casa`/`planta`, reglas del payload y del entorno, repartos de
tiempo (peor caso de 6 llamadas dentro de los 36 s), puntos sin ruta alcanzable y el contrato.

Una batería real contra n8n 2.42.5 y ORS confirmó los casos `a`, `c`, `d`, `e`, `f` y `g`, y que las
dos llamadas del camino con viaje a la casa terminan bien. Quedan por verificar en n8n real, tras
reimportar y publicar esta versión: los casos `b`, `h` e `i`, el bucle de reintento
(**Evaluar reparto** → **Optimizar (ORS)**) y el de puntos sin ruta (`j`), el uso de `$runIndex`, y
el texto exacto del error `code 3` de ORS.

**Bug corregido (batería real).** Con viaje a la casa el workflow respondía 500 `Error in workflow`:
**Armar respuesta** leía el plan con `$('¿Terminar en casa?').first()`, pero ese IF tiene dos salidas
y **Armar respuesta** es alcanzable desde ambas, así que la lectura por nombre devolvía `undefined`
en el camino de la casa. Ahora lee `$('Preparar viaje a casa')`, un nodo Code con una sola salida y
una sola ejecución; en los demás caminos el plan viaja dentro del item. Regla para este workflow:
**no leer por nombre nodos con más de una salida ni nodos que se ejecuten más de una vez**; las
lecturas que quedan (`Config`, `Webhook`, `Normalizar`, `Separar pendientes`, `Preparar optimización`
y `Preparar viaje a casa`) son de nodos de una salida y una ejecución. Las llamadas HTTP reemplazan el
item por la respuesta de ORS, por eso **Evaluar reparto** y **Evaluar error de optimización**
reconstruyen su estado desde la respuesta y desde `Preparar optimización`.

## Qué configurar antes de usarlo

**Variables de entorno** (en el `.env` local, o en el servicio al desplegar). El workflow las lee
con `{{ $env.X }}`, por eso `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` (ya está en el compose y el
Dockerfile):

| Variable | Obligatoria | Uso |
|---|---|---|
| `DEPOT_LAT`, `DEPOT_LON` | Sí | Coordenadas del depósito (inicio y fin de la ruta). |
| `OPENROUTESERVICE_API_KEY` | Sí | API key de <https://openrouteservice.org> (nivel gratuito). Va en el header `Authorization` de **Geocodificar (ORS)**, **Optimizar (ORS)** y **Optimizar a casa (ORS)**. |
| `CASA_LAT`, `CASA_LON` | Solo si la regla exige terminar en la casa | Casa del despachador: destino del viaje más liviano cuando hay 2 o más viajes y pesa menos que `UMBRAL_CASA_KG`. Si faltan o no son válidas en ese caso: 500 `configuracion_incompleta` (`Definir CASA_LAT y CASA_LON`). No se necesitan con un solo viaje ni si el más liviano pesa >= umbral. Nunca en el repo, los logs ni las respuestas. |
| `CAPACIDAD_KG`, `UMBRAL_CASA_KG`, `MAX_VIAJES` | No (1100 / 800 / 3) | Respaldo de las `reglas` que envía el backend; solo se usan si la solicitud no las trae. |
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

**Sobre los datos de ejecución.** La casa solo se lee con `$env` (dentro de **Evaluar reparto**, que
la valida sin copiarla, y en el cuerpo del nodo **Optimizar a casa (ORS)**), así que no queda en los
datos de entrada de ningún nodo ni en las respuestas del webhook. Aun así, la respuesta de ORS a esa
llamada incluye el punto de llegada y n8n la guarda con la ejecución, igual que ya ocurre con el
depósito: mantener `EXECUTIONS_DATA_PRUNE=true` con un `EXECUTIONS_DATA_MAX_AGE` corto (ver
DESPLIEGUE.md) y, si se quiere evitar el rastro por completo, `EXECUTIONS_DATA_SAVE_ON_SUCCESS=none`.

El nodo Webhook trae datos de prueba fijados (4 paradas con `peso_kg` y las `reglas`: dos con
coordenadas, una real de Viña del Mar sin coordenadas y una inventada) para ejecutarlo desde el
editor con **Test workflow**. Para probar el contrato con viajes usar los payloads de
[`workflows/casos-prueba/`](workflows/casos-prueba/README.md).
La inventada (pedido 104, "Pasaje Zxqwyrt 98765, Villa Inexistente") es el caso de regresión de
la regla de layer: debe terminar en `sin_resolver` con motivo `dirección no encontrada con
precisión suficiente`, no en la ruta.
