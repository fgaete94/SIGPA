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
- Todas las llamadas FastAPI llevan el JWT de la sesión.
- GET `/pedidos`, GET `/pedidos/{id}`, GET `/clientes`, GET `/productos`.
- Estado de despacho (#70, lógica en `src/estados.js`): en el listado de pedidos, "Salió a reparto" (confirmado → en despacho) y "Volver a confirmado" con un clic, vía POST `/pedidos/{id}/estado`; el backend solo permite esas dos transiciones y responde 409 con el motivo en cualquier otro caso. En "Ruta en curso", "Salió a reparto (N)" pasa todos los confirmados de la ruta de una vez e informa los que no se pudieron cambiar. El detalle del pedido muestra el historial de estados (GET `/pedidos/{id}/historial`, fechas en hora de Chile). El paso a entregado no se hace aquí (#99). El filtro por estado muestra cuántos pedidos hay en cada uno.
- Altas y ediciones manuales desde el panel (el bot de WhatsApp solo atiende a clientes ya registrados):
  - Clientes (`src/FormCliente.jsx`, lógica en `src/clientes.js`): POST `/clientes` y PATCH `/clientes/{id}`, con sector (GET `/sectores`), día de reparto y tipo (GET `/tipos-cliente`). El teléfono se guarda como 56 + 9 dígitos, igual que lo normaliza el backend para reconocer al cliente, y se rechaza si otro cliente ya lo tiene aunque esté escrito con otro formato. Un número que no es celular se acepta con aviso (no recibirá WhatsApp).
  - Pedidos (`src/NuevoPedido.jsx`, lógica en `src/pedidos.js`): POST `/pedidos` con cliente, dirección de despacho opcional (vacía = la del cliente) y productos con cantidad. Queda `pendiente`; el backend calcula el total con el precio vigente.
  - Productos (pestaña Productos, `src/Productos.jsx`, lógica en `src/inventario.js`): POST `/productos` y PATCH `/productos/{id}` con nombre, precio (CLP), stock y capacidad en litros. Se avisa al renombrar uno existente, porque la landing busca los precios por nombre exacto.
- Landing pública: GET `/productos` (solo lectura, sin sesión) para nombres y precios, a través del backend; nunca se conecta a Supabase. Solo se usan nombre y precio. Mientras Render despierta se muestran los últimos precios conocidos (`src/catalogo.js`, `PRECIOS_RESPALDO` en `src/marca.js`). Cada tarjeta se asocia a un producto por su nombre exacto en la tabla `producto`.
- GET `/rutas/pedidos-pendientes` y POST `/rutas/planificar` con `{pedido_ids:[...]}`.
  - El GET lista los pedidos planificables, ordenados por `creado_en` ascendente: todos los `pendiente` y además los `confirmado` con `orden_entrega` null o `motivo_revision_direccion` no null (por ejemplo, los que quedaron en `sin_resolver` en una ruta anterior, antes o después de corregir sus coordenadas con POST `/pedidos/{id}/coordenadas`).
  - Cada item: `pedido_id`, `cliente_id`, `cliente_nombre`, `cliente_telefono`, `direccion_texto`, `latitud`, `longitud`, `creado_en`, `estado` (`pendiente` o `confirmado`) y `motivo_revision_direccion` (texto o `null`).
  - `creado_en` viene en ISO 8601 con zona horaria UTC (ej. `2026-10-08T14:03:12.123456Z`); `new Date(...)` lo interpreta bien y se muestra en hora de Chile con `timeZone: 'America/Santiago'`.
  - POST `/rutas/planificar` acepta pedidos `pendiente` y `confirmado`, hasta `RUTA_MAX_PEDIDOS` por solicitud (default 30). Los que quedan en `sin_resolver` guardan el motivo en `motivo_revision_direccion`; los que entran a la ruta lo dejan en `null`.
  - Errores de POST `/rutas/planificar` (siempre con `detail.mensaje` para mostrar):
    - 422 sobre el tope: `{mensaje, maximo, recibidos}`; 422 con IDs repetidos: `{mensaje, pedido_ids}`.
    - 409: pedidos que ya no se pueden incluir (`{mensaje, pedido_ids, inexistentes, estado_invalido}`), otra planificación en curso, o un pedido que cambió mientras se calculaba la ruta (`"El pedido <id> cambió durante la planificación, reintenta"`). En todos los casos no se guardó nada: refrescar la lista y reintentar.
    - 503: servicio de rutas no configurado en el backend (falta la URL o el secreto de n8n).
    - 502: n8n rechazó la solicitud o está mal configurado (`{mensaje, codigo}`, donde `codigo` es el código de error de n8n o el status HTTP), no respondió bien, devolvió algo que no es JSON o una ruta inválida (`{mensaje, problemas}`). 504: n8n no respondió a tiempo. Nada se guarda en ninguno de estos casos.
- El panel pide confirmación antes de planificar porque el endpoint también confirma pedidos y guarda el orden.
- Pestaña Rutas (`src/Rutas.jsx`, lógica en `src/planificacion.js`):
  - Cada pedido muestra su estado y, si n8n no pudo ubicarlo, la marca "Dirección por revisar" con el motivo (`motivo_revision_direccion`).
  - "Corregir ubicación" (#113) abre la dirección en Google Maps y recibe las coordenadas tal como las copia Maps (`-33.0458, -71.6197`); valida que estén en Chile y llama a POST `/pedidos/{id}/coordenadas`.
  - La selección se limita a 30 pedidos (`RUTA_MAX_PEDIDOS`, igual al valor por defecto del backend) con un contador.
  - Los errores de POST `/rutas/planificar` se muestran con su detalle (`api()` deja `error.status` y `error.detalle`): pedidos que cambiaron de estado, problemas de la respuesta de n8n, tope y aviso de arranque en frío en el 504.
  - "Ruta en curso" (#85, `src/RutaEnCurso.jsx`, lógica en `src/reparto.js`): pedidos confirmados o en despacho con `orden_entrega`, en ese orden, con dirección (la del pedido o la del cliente), comuna y teléfono. Se puede imprimir, enviar al repartidor por WhatsApp (`wa.me` sin número: se elige el contacto) o copiar; cada parada tiene "Cómo llegar" en Google Maps. Como cada planificación numera desde 1, un número repetido avisa que hay pedidos de más de una ruta. No hay hora estimada: el backend no la guarda.
- Pestaña Reportes (`src/Reportes.jsx`, lógica en `src/indicadores.js`), con un período común (7 días, 30 días, mes, todo o fechas a elección):
  - "Ventas" (#71): suma de los pedidos entregados (su total ya es lo entregado), por día, semana o mes, con filtro de comuna y tipo de cliente (cruzando con `/clientes`). Muestra ventas, pedidos entregados, ticket promedio y el monto en curso (pendientes, confirmados y en despacho). Se agrupa por la fecha del pedido porque el backend no guarda la fecha de entrega. Gráfico de barras propio (sin librerías), con tabla equivalente. Faltan las unidades: `/pedidos` no trae las cantidades por producto.
  - "Pedidos por comuna" (#73) agrupa por la comuna del cliente, sin cancelados.
  - Con la pestaña abierta los datos se recargan cada minuto. Las fechas de `/pedidos` vienen sin zona (UTC) y se pasan al día de Chile con `src/fechas.js`.
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

`npm run build` verifica la compilación. `npm test` comprueba que el reordenamiento conserve todos los pedidos y respete los extremos de la lista, y prueba el módulo de sesión (decodificación del JWT, expiración con margen y un único refresh ante llamadas simultáneas). También prueba la pestaña Rutas (tope de selección, lectura y validación de coordenadas y textos de error), la ruta en curso (orden, rutas mezcladas, enlaces y texto para WhatsApp) y Reportes (días en hora de Chile, períodos, agrupación por comuna, ventas por día, semana o mes y sus filtros).
