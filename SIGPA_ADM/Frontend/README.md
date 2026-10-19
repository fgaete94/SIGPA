# SIGPA · Frontend inicial

React + Vite. Base preparada contra el backend de `fgaete94/SIGPA`, commit `06345ae46bb1ca5f488d4a50d889b9aa3d78fcdf`.

## Iniciar en el computador

1. Instalar Node.js 22.12 o superior compatible con Vite 7.
2. Abrir esta carpeta (`SIGPA_ADM/Frontend`) en Visual Studio Code.
3. Ejecutar `npm ci` en la terminal.
4. Copiar `.env.example` a `.env.local` e indicar la URL del backend FastAPI:

```dotenv
SIGPA_API_TARGET=https://sigpa-34sy.onrender.com
```

El frontend no usa Supabase ni necesita sus claves: la autenticación pasa por el backend (`/auth`), que actúa como proxy a Supabase Auth. El acceso se hace en la pantalla de inicio con una cuenta existente; no se crean usuarios desde este panel. Nunca poner claves, contraseñas de base de datos ni tokens de Meta en el frontend.

5. Ejecutar `npm run dev` y abrir la dirección local indicada en la terminal. Reiniciar Vite al cambiar variables.

## Conexiones implementadas

- Autenticación vía backend: POST `/api/auth/login`, `/api/auth/refresh` y `/api/auth/logout`. El frontend ya no usa Supabase.
- El refresh token se guarda en `sessionStorage` (sobrevive a recargar la página y se borra al cerrar la pestaña); el access token solo en memoria. El token se renueva antes de expirar y, si el backend responde 401, se renueva una vez y se reintenta; si la renovación es rechazada, se vuelve al login.
- Cierre por inactividad (#134, `src/inactividad.js`): tras `MINUTOS_INACTIVIDAD` (15 minutos, configurable en esa constante) sin clics, teclado ni desplazamiento, se cierra la sesión también en el backend y se vuelve al login con un aviso. La renovación automática del token no cuenta como actividad. Se revisa cada 15 s contra la hora de la última actividad y al volver a la pestaña, para que funcione aunque el navegador congele los temporizadores en segundo plano.
- Todas las llamadas FastAPI llevan el JWT de la sesión.
- GET `/pedidos`, GET `/pedidos/{id}`, GET `/clientes`, GET `/productos`.
- Pestaña Despacho (#67, `src/Despacho.jsx`, lógica en `src/agrupacionDespacho.js`): pedidos confirmados, en despacho y entregados (la misma regla que GET `/pedidos/despacho`), agrupados por el día de reparto del cliente (lunes a viernes, y al final los clientes sin día) y, dentro de cada día, por comuna, con cantidad y total por grupo. Filtros de estado (por entregar, entregados o todos), día (marca el de hoy) y comuna, cada opción con su cantidad. Se arma con los pedidos que el panel ya cargó para que los contadores y las acciones (salió a reparto, entregado, ver detalle) se vean al instante.
- Estado de despacho (#70, lógica en `src/estados.js`): en el listado de pedidos, "Salió a reparto" (confirmado → en despacho) y "Volver a confirmado" con un clic, vía POST `/pedidos/{id}/estado`; el backend solo permite esas dos transiciones y responde 409 con el motivo en cualquier otro caso. En "Ruta en curso", "Salió a reparto (N)" pasa todos los confirmados de la ruta de una vez e informa los que no se pudieron cambiar. El detalle del pedido muestra el historial de estados (GET `/pedidos/{id}/historial`, fechas en hora de Chile). El paso a entregado no se hace aquí (#99). El filtro por estado muestra cuántos pedidos hay en cada uno.
- Registro de entrega (#99, `src/RegistrarEntrega.jsx`, lógica en `src/entregas.js`): botón "Entregado" en los pedidos en despacho del listado, en cada parada de "Ruta en curso" y "Registrar entrega" en el detalle de un pedido confirmado o en despacho. Muestra cada producto con lo pedido y un campo con lo entregado (parte con lo pedido); recalcula el total y avisa si la entrega es parcial o si se entregó más de lo pedido. No deja registrar cantidades inválidas ni una entrega sin unidades. Llama a POST `/pedidos/{id}/entrega` con `{lineas: [{detalle_id, cantidad_entregada}]}` (el `id` de cada línea viene en GET `/pedidos/{id}`); el pedido queda "entregado" con el total de lo entregado, que es lo que suma en Ventas, y el cambio aparece en el historial.
- Altas y ediciones manuales desde el panel (el bot de WhatsApp solo atiende a clientes ya registrados):
  - Clientes (`src/FormCliente.jsx`, lógica en `src/clientes.js`): POST `/clientes` y PATCH `/clientes/{id}`, con comuna obligatoria al crear (GET `/comunas`) y sector opcional de esa comuna (GET `/sectores`, #136), día de reparto y tipo (GET `/tipos-cliente`). El teléfono se guarda como 56 + 9 dígitos, igual que lo normaliza el backend para reconocer al cliente, y se rechaza si otro cliente ya lo tiene aunque esté escrito con otro formato. Un número que no es celular se acepta con aviso (no recibirá WhatsApp).
  - Ficha del cliente (#102, `src/FichaDelCliente.jsx`, lógica en `src/fichaCliente.js`): "Ver ficha" en cada fila de Clientes (que ya se busca por nombre o teléfono) abre GET `/clientes/{id}`: datos (teléfono, dirección, comuna y sector, día de reparto, tipo con su equivalente B2C/B2B, si recibe recordatorios), resumen (pedidos, entregados, comprado, promedio por entrega, último pedido, en curso y cancelados; "comprado" suma solo lo entregado, igual que Ventas) e historial completo con "Ver detalle" de cada pedido y acceso a "Editar cliente".
  - Pedidos (`src/NuevoPedido.jsx`, lógica en `src/pedidos.js`): POST `/pedidos` con cliente, dirección de despacho opcional (vacía = la del cliente) y productos con cantidad. Queda `pendiente`; el backend calcula el total con el precio vigente.
  - Productos (pestaña Productos, `src/Productos.jsx`, lógica en `src/inventario.js`): POST `/productos` y PATCH `/productos/{id}` con nombre, precio (CLP), stock y capacidad en litros. Se avisa al renombrar uno existente, porque la landing busca los precios por nombre exacto.
- Landing pública: GET `/productos` (solo lectura, sin sesión) para nombres y precios, a través del backend; nunca se conecta a Supabase. Solo se usan nombre y precio. Mientras Render despierta se muestran los últimos precios conocidos (`src/catalogo.js`, `PRECIOS_RESPALDO` en `src/marca.js`). Cada tarjeta se asocia a un producto por su nombre exacto en la tabla `producto`.
- GET `/rutas/pedidos-pendientes`, POST `/rutas/planificar` con `{pedido_ids:[...], vista_previa}` y POST `/rutas/confirmar` (#87).
  - El GET lista los pedidos planificables, ordenados por `creado_en` ascendente: todos los `pendiente` y además los `confirmado` con `orden_entrega` null o `motivo_revision_direccion` no null (por ejemplo, los que quedaron en `sin_resolver` en una ruta anterior, antes o después de corregir sus coordenadas con POST `/pedidos/{id}/coordenadas`).
  - Cada item: `pedido_id`, `cliente_id`, `cliente_nombre`, `cliente_telefono`, `direccion_texto`, `latitud`, `longitud`, `creado_en`, `estado` (`pendiente` o `confirmado`) y `motivo_revision_direccion` (texto o `null`).
  - `creado_en` viene en ISO 8601 con zona horaria UTC (ej. `2026-10-08T14:03:12.123456Z`); `new Date(...)` lo interpreta bien y se muestra en hora de Chile con `timeZone: 'America/Santiago'`.
  - POST `/rutas/planificar` acepta pedidos `pendiente` y `confirmado`, hasta `RUTA_MAX_PEDIDOS` por solicitud (default 30). Los que quedan en `sin_resolver` guardan el motivo en `motivo_revision_direccion`; los que entran a la ruta lo dejan en `null`.
  - Errores de POST `/rutas/planificar` (siempre con `detail.mensaje` para mostrar):
    - 422 sobre el tope: `{mensaje, maximo, recibidos}`; 422 con IDs repetidos: `{mensaje, pedido_ids}`.
    - 409: pedidos que ya no se pueden incluir (`{mensaje, pedido_ids, inexistentes, estado_invalido}`), otra planificación en curso, o un pedido que cambió mientras se calculaba la ruta (`"El pedido <id> cambió durante la planificación, reintenta"`). En todos los casos no se guardó nada: refrescar la lista y reintentar.
    - 503: servicio de rutas no configurado en el backend (falta la URL o el secreto de n8n).
    - 502: n8n rechazó la solicitud o está mal configurado (`{mensaje, codigo}`, donde `codigo` es el código de error de n8n o el status HTTP), no respondió bien, devolvió algo que no es JSON o una ruta inválida (`{mensaje, problemas}`). 504: n8n no respondió a tiempo. Nada se guarda en ninguno de estos casos.
- GET `/rutas/actual`: la ruta guardada, para volver a verla después de recargar (POST `/rutas/planificar` solo la devuelve en su respuesta). Requiere JWT como el resto de `/rutas` (sin token: 401 `{"detail": "No autenticado"}`; token inválido: 401 `{"detail": "Token inválido o expirado"}`). El panel todavía no lo usa: "Ruta en curso" arma la ruta desde GET `/pedidos`.
  - Respuesta: `{"ruta": [...], "total": N, "advertencias": [...]}`, con `total` = cantidad de paradas.
  - Incluye solo pedidos `confirmado` con `orden_entrega` no null. No aparecen los `pendiente`, los confirmados sin orden (los `sin_resolver`), ni los que pasaron a `en_despacho`, `entregado` o `cancelado`. Ojo: "Ruta en curso" sí muestra los `en_despacho`.
  - Orden: `orden_entrega` ascendente; a igual número, primero el pedido con `actualizado_en` más reciente (normalmente el de la planificación más nueva) y luego el `pedido_id` menor.
  - Cada item: `pedido_id`, `orden_entrega`, `cliente_id`, `cliente_nombre`, `cliente_telefono`, `direccion_texto` (la `direccion_despacho` del pedido o, si no tiene, la dirección del cliente, igual que en los pendientes), `latitud`, `longitud` (`null` si no hay) y `estado` (siempre `confirmado`).
  - `advertencias`: una por cada `orden_entrega` repetido, `{"tipo": "orden_entrega_duplicado", "mensaje", "orden_entrega", "pedido_ids": [...]}`. Cada planificación numera desde 1 y no toca los confirmados que quedaron fuera de la solicitud, así que un número repetido indica que la lista mezcla rutas: el panel debe mostrar el `mensaje`. Lista vacía si no hay repetidos.
  - Limitación: no hay filtro `?fecha=`. `pedido` no tiene fecha de entrega ni id de ruta (solo `creado_en` y `actualizado_en`, que cambian por otros motivos), así que el backend no puede saber a qué día o planificación pertenece cada parada.
- Vista previa de la ruta (#87, `src/VistaPreviaRuta.jsx`, lógica en `src/vistaPrevia.js`): "Calcular ruta" llama a POST `/rutas/planificar` con `vista_previa: true`, que consulta a n8n pero no guarda nada, y abre la ruta propuesta. Ahí se pueden subir o bajar paradas, sacar alguna para la próxima ruta (vuelve a "Preparar ruta" sin tocarse), revisar en el mapa dónde quedó cada una y ver las direcciones que no se ubicaron. "Confirmar ruta" la guarda con POST `/rutas/confirmar` (`{paradas: [{pedido_id, latitud, longitud, version}], sin_resolver: [{pedido_id, motivo, version}]}`, en el orden final), con las mismas reglas que planificar y sin volver a llamar a n8n; si un pedido cambió desde que se calculó, responde 409 y se ofrece volver a calcular. Si no se ubicó ninguna dirección, "Confirmar y marcar para revisión" las deja como "Dirección por revisar". Cancelar no guarda nada.
- Pestaña Rutas (`src/Rutas.jsx`, lógica en `src/planificacion.js`):
  - Cada pedido muestra su estado y, si n8n no pudo ubicarlo, la marca "Dirección por revisar" con el motivo (`motivo_revision_direccion`).
  - "Corregir ubicación" (#113) abre la dirección en Google Maps y recibe las coordenadas tal como las copia Maps (`-33.0458, -71.6197`); valida que estén en Chile y llama a POST `/pedidos/{id}/coordenadas`.
  - La selección se limita a 30 pedidos (`RUTA_MAX_PEDIDOS`, igual al valor por defecto del backend) con un contador.
  - Los errores de POST `/rutas/planificar` se muestran con su detalle (`api()` deja `error.status` y `error.detalle`): pedidos que cambiaron de estado, problemas de la respuesta de n8n, tope y aviso de arranque en frío en el 504.
  - "Ruta en curso" (#85, `src/RutaEnCurso.jsx`, lógica en `src/reparto.js`): pedidos confirmados o en despacho con `orden_entrega`, en ese orden, con dirección (la del pedido o la del cliente), comuna y teléfono. Se puede enviar al repartidor por WhatsApp (`wa.me` sin número: se elige el contacto) o copiar; cada parada tiene "Cómo llegar" en Google Maps. Como cada planificación numera desde 1, un número repetido avisa que hay pedidos de más de una ruta. No hay hora estimada: el backend no la guarda.
  - "Imprimir hoja de ruta" (`src/HojaDeRuta.jsx`, lógica en `src/hojaRuta.js`) reemplaza la planilla Excel del conductor: abre una vista previa a pantalla completa con fecha de reparto y conductor (opcional, se recuerda en el navegador) y se imprime en horizontal, solo la planilla. Trae los productos con GET `/pedidos/{id}` de cada parada y el sector con GET `/sectores`; si falla el detalle de algún pedido no deja imprimir y ofrece reintentar.
  - Contenido: carga del camión por producto (a entregar, cargado y vuelve), rendición (por cobrar, efectivo, transferencia, tarjeta), y por parada cliente, teléfono, dirección, comuna y sector, lo que pide (cantidad y precio) con una fila para anotar lo entregado, monto, medio de pago, observaciones y firmas. Las columnas de productos siguen el orden de la planilla de ADM (Recarga 20L, Recarga 12L, Nuevo 20L, Nuevo 12L, Dispensador Básico, Dispensador USB) y van siempre; las promos se agregan solo si están en la ruta. Usa una copia de las paradas al abrirla (respeta el orden cambiado a mano).
- Pestaña Reportes (`src/Reportes.jsx`, lógica en `src/indicadores.js`), con un período común (7 días, 30 días, mes, todo o fechas a elección):
  - "Ventas" (#71): suma de los pedidos entregados (su total ya es lo entregado), por día, semana o mes, con filtro de comuna y tipo de cliente (cruzando con `/clientes`). Muestra ventas, pedidos entregados, ticket promedio y el monto en curso (pendientes, confirmados y en despacho). Se agrupa por la fecha del pedido porque el backend no guarda la fecha de entrega. Gráfico de barras propio (sin librerías), con tabla equivalente. Faltan las unidades: `/pedidos` no trae las cantidades por producto.
  - "Pedidos por comuna" (#73) agrupa por la comuna del cliente, sin cancelados.
  - Con la pestaña abierta los datos se recargan cada minuto. Las fechas de `/pedidos` vienen sin zona (UTC) y se pasan al día de Chile con `src/fechas.js`.
  - Orden de las tablas (#137, lógica en `src/ordenar.js`, encabezados en `src/Ordenable.jsx`): en Resumen, Pedidos, Clientes, Productos y Reportes, presionar el encabezado de una columna ordena la tabla y presionarlo de nuevo invierte el orden. Los textos se ordenan en español (sin distinguir mayúsculas ni tildes), los números como números y los vacíos quedan siempre al final. El estado sigue el ciclo del pedido, el día de reparto la semana y el período de ventas la fecha. La hoja de ruta y la ruta en curso no se reordenan, porque siguen el orden de entrega. En el celular las tablas se ven como tarjetas sin encabezados, por lo que el orden solo está disponible en pantallas anchas.
- Los errores no se sustituyen por datos ficticios. Los indicadores corresponden a todos los registros devueltos, no a una jornada.
- Los cambios manuales de orden (flechas de "Ruta en curso") solo afectan la impresión y el texto enviado. Se indica expresamente en pantalla; no hay endpoint existente para persistirlos. Se pierden al recargar/cerrar sesión.

## Límites de esta primera entrega

No se probó con credenciales ni datos reales. No se modificó el backend, no se enviaron mensajes y no se desplegó. La autorización actual del backend valida usuarios autenticados, sin distinguir administradores: antes de abrir el servicio a usuarios externos, el equipo debe definir y aplicar roles en el servidor.

Faltan: persistencia transaccional del orden manual, fecha de entrega e identidad de ruta, edición de clientes/pedidos, reportes históricos y pruebas de integración en el entorno del equipo. La impresión inicial contiene ID, cliente y dirección; todavía no constituye la hoja de reparto completa con cantidades/productos.

## Despliegue

`npm run build` genera `dist`. El proxy `/api` de Vite funciona SOLO con `npm run dev`; no basta subir `dist` para conectar la API. En producción (Render, Static Site) se usa una regla **Rewrite** `/api/*` → `https://sigpa-34sy.onrender.com/*`, que envía las llamadas al backend quitando `/api` desde el mismo origen. El sitio no necesita variables `VITE_SUPABASE_*`.

Esta regla todavía no se ha verificado en Render, en particular que reenvíe el header `Authorization` al backend. Si falla, el plan B es usar URL directa al backend y CORS explícito (solo el origen del sitio) en FastAPI.

`npm run preview` sirve únicamente para revisar la compilación, sin conexión API configurada.

No habilitar CORS universal ni exponer secretos para resolver esto. Las variables VITE son públicas y se incorporan al compilar.

## Verificación

`npm run build` verifica la compilación. `npm test` comprueba que el reordenamiento conserve todos los pedidos y respete los extremos de la lista, prueba el orden de las tablas (números, textos en español, vacíos al final y listas con orden propio) y prueba el módulo de sesión (decodificación del JWT, expiración con margen y un único refresh ante llamadas simultáneas) y el cierre por inactividad (expira al cumplirse el tiempo, cada actividad reinicia el conteo y revisa al volver a la pestaña). También prueba la pestaña Rutas (tope de selección, lectura y validación de coordenadas y textos de error), la ruta en curso (orden, rutas mezcladas, enlaces y texto para WhatsApp) y Reportes (días en hora de Chile, períodos, agrupación por comuna, ventas por día, semana o mes y sus filtros).
