# casos-prueba/

Payloads de ejemplo para probar a mano el webhook `POST /webhook/sigpa-ruta` del workflow
`optimizacion-rutas` con la restricción de peso y los viajes. Las coordenadas son **ficticias**
(zona Valparaíso–Viña del Mar) y no corresponden al depósito ni a la casa del despachador; esos
valores viven solo en las variables de entorno de n8n.

Con las reglas por defecto (capacidad 1100 kg, umbral de casa 800 kg, máximo 3 viajes), contra la
URL de producción (`https://<host>/webhook/sigpa-ruta`) o la de test, con el secreto en el header:

```bash
curl -i -X POST "https://<host>/webhook/sigpa-ruta" \
  -H "Content-Type: application/json" -H "X-Route-Secret: <SECRETO>" \
  --data-binary "@a-un-viaje.json"
```

(En PowerShell usar `curl.exe`, no el alias.) Las pruebas con ORS real usan cuota. La lógica de
reparto y numeración se probó además con un ORS simulado (ver "Cómo se validó" en
[`../../README.md`](../../README.md)); lo de abajo es la verificación contra el servicio real.

| Archivo | Qué prueba | Resultado esperado |
|---|---|---|
| `a-un-viaje.json` | 3 paradas con coordenadas, 400 + 300 + 350 = 1050 kg (<= 1100). | `200`. `viajes` = un solo viaje con `peso_kg` 1050, `termina_en` `planta`, `total_paradas` 3. `orden_entrega` 1..3 y `viaje` 1 en todas. `sin_resolver` vacío. 1 llamada de optimización. |
| `b-dos-viajes-termina-en-casa.json` | 600 + 500 + 400 + 100 = 1600 kg: no cabe en un viaje. | `200` con 2 viajes. Cualquier reparto válido deja el más liviano por debajo de 800 kg (no hay subconjunto de 800), así que el viaje 1 es el más pesado (`termina_en` `planta`) y el viaje 2, el último y más liviano, `termina_en` `casa`. Pesos sumando 1600. `orden_entrega` 1..N en cada viaje. Requiere `CASA_LAT`/`CASA_LON` definidas. |
| `c-dos-viajes-termina-en-planta.json` | 1000 + 900 = 1900 kg: dos viajes pesados. | `200` con 2 viajes: viaje 1 de 1000 kg y viaje 2 de 900 kg, ambos `planta` (el último pesa >= 800: no va a la casa). No necesita `CASA_*`. |
| `d-parada-no-geocodificable.json` | Una parada sin coordenadas con una dirección inventada ("Pasaje Zxqwyrt 98765, Villa Inexistente"). | `200`. El pedido 2 va a `sin_resolver` con motivo `dirección no encontrada con precisión suficiente` (regla `layer = address`, sin cambios). El pedido 1 queda en un viaje de 400 kg (`planta`). Usa el geocodificador de ORS. |
| `e-peso-faltante.json` | La parada 2 no trae `peso_kg`. | `400` `{"error": "entrada_invalida", "detalle": "peso_kg debe ser un entero >= 1 (pedido_id 2)"}`. No llama a ORS. |
| `f-carga-sobre-capacidad.json` | 1000 + 1000 + 1000 + 400 = 3400 kg > 1100 x 3. | `400` `{"error": "carga_excede_capacidad", "detalle": "El peso total de las paradas (3400 kg) supera capacidad_kg * max_viajes (3300 kg)"}`. No llama a la optimización. |
| `j-punto-sin-ruta-alcanzable.json` | La parada 2 está en un punto ficticio mar adentro, frente a Valparaíso (sin ruta alcanzable). | `200`. El pedido 2 va a `sin_resolver` con motivo `ubicación sin ruta alcanzable`; los pedidos 1 y 3 quedan en un viaje de 750 kg (`planta`) con `orden_entrega` 1..2. ORS responde primero con el error `code 3 "Unfound route(s) from location [lon,lat]"` y el workflow reintenta sin esa parada: 2 llamadas de optimización. Si hubiera más de 2 paradas así, o el error no se pudiera asociar a una parada, la respuesta sería `502 optimizacion_fallida`. |
| `g-casa-sin-definir.json` | Mismo payload que (b), pero **sin** `CASA_LAT`/`CASA_LON` en n8n. | `500` `{"error": "configuracion_incompleta", "detalle": "Definir CASA_LAT y CASA_LON"}`. No hay reoptimización hacia la casa. |

## Cómo reproducir (g)

El caso (g) necesita una instancia de n8n **sin** `CASA_LAT`/`CASA_LON`:

1. En una instancia local o de prueba (no producción), quitar o dejar vacías `CASA_LAT` y `CASA_LON`
   en el `.env` y reiniciar (`docker compose up -d`). Las variables de entorno se leen al arrancar.
2. Enviar `g-casa-sin-definir.json` (es el mismo cuerpo de (b)).
3. Volver a definir `CASA_LAT`/`CASA_LON` y reiniciar para que (b) vuelva a dar 200.

También sirve un valor no numérico (por ejemplo `CASA_LAT=abc`): se trata igual que si faltara.

## Notas

- Orden de la tabla: el caso `j` se agregó después de a–g (las letras h e i se usaron en la batería
  de 50 y 30 paradas, que se genera fuera del repo).
- Corrección (batería real): los casos `b`, `h` e `i` respondían 500 `Error in workflow` porque
  **Armar respuesta** leía el plan de `¿Terminar en casa?`, un IF con dos salidas, y n8n devolvía
  `undefined` en el camino con viaje a la casa. Ahora el plan se lee de **Preparar viaje a casa**
  (una salida, una ejecución). Con el workflow corregido `b` debe dar 200 con el último viaje a la casa.

- Los pesos y las reglas están en el payload (`reglas` es opcional). Para probar el respaldo por
  entorno, quitar `reglas` del JSON y definir `CAPACIDAD_KG`, `UMBRAL_CASA_KG` y `MAX_VIAJES`.
- Con `max_viajes` 3, ORS estándar admite como máximo 3 vehículos por solicitud (verificar en su
  documentación); un caso como 3 pedidos de 700 kg obliga a un reintento con un vehículo más.
