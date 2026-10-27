import test from 'node:test';
import assert from 'node:assert/strict';
import { RUTA_MAX_PEDIDOS, alternarSeleccion, detallesErrorRuta, enlaceMapa, leerCoordenadas } from './planificacion.js';

test('la selección nunca pasa del máximo por ruta', () => {
  const llenos = Array.from({ length: RUTA_MAX_PEDIDOS }, (_, i) => i + 1);
  assert.equal(alternarSeleccion(llenos, 99, true), llenos);
  assert.deepEqual(alternarSeleccion([1, 2], 3, true), [1, 2, 3]);
  assert.deepEqual(alternarSeleccion([1, 2], 2, true), [1, 2]);
  assert.deepEqual(alternarSeleccion(llenos, 1, false), llenos.slice(1));
});

test('lee las coordenadas tal como las copia Google Maps', () => {
  assert.deepEqual(leerCoordenadas('-33.0458, -71.6197'), { latitud: -33.0458, longitud: -71.6197 });
  assert.deepEqual(leerCoordenadas('  -33.0458   -71.6197 '), { latitud: -33.0458, longitud: -71.6197 });
  assert.deepEqual(leerCoordenadas('-33.0458;-71.6197'), { latitud: -33.0458, longitud: -71.6197 });
});

test('rechaza coordenadas incompletas, con coma decimal, invertidas o sin signo', () => {
  for (const texto of ['', '-33.0458', '-33,0458, -71,6197', 'abc, def', '-71.6197, -33.0458', '33.0458, 71.6197']) {
    assert.ok(leerCoordenadas(texto).error, texto);
  }
});

test('el enlace del mapa busca la dirección en Chile', () => {
  assert.equal(enlaceMapa('Av. Libertad 100, Viña del Mar'), 'https://www.google.com/maps/search/?api=1&query=Av.%20Libertad%20100%2C%20Vi%C3%B1a%20del%20Mar%2C%20Chile');
});

test('explica los errores del backend al planificar', () => {
  const conflicto = Object.assign(new Error('x'), { status: 409, detalle: { mensaje: 'x', pedido_ids: [4, 7], inexistentes: [4], estado_invalido: [{ pedido_id: 7, estado: 'en_despacho' }] } });
  assert.deepEqual(detallesErrorRuta(conflicto), ['Ya no existen: #4.', 'El pedido #7 ahora está "en despacho".', 'No se guardó nada: los pedidos siguen como estaban.']);

  const tope = Object.assign(new Error('x'), { status: 422, detalle: { mensaje: 'x', maximo: 30, recibidos: 31 } });
  assert.equal(detallesErrorRuta(tope)[0], 'Seleccionaste 31 pedidos; el máximo por ruta es 30.');

  const repetidos = Object.assign(new Error('x'), { status: 422, detalle: { mensaje: 'x', pedido_ids: [3] } });
  assert.equal(detallesErrorRuta(repetidos)[0], 'Pedidos repetidos: #3.');

  const invalida = Object.assign(new Error('x'), { status: 502, detalle: { mensaje: 'x', problemas: ['pedidos repetidos: [5]'], codigo: 'entrada_invalida' } });
  assert.deepEqual(detallesErrorRuta(invalida).slice(0, 2), ['pedidos repetidos: [5]', 'Código del servicio de rutas: entrada_invalida.']);

  const sinPeso = Object.assign(new Error('x'), { status: 422, detalle: { mensaje: 'x', sin_peso: [8, 9], productos_sin_peso: [{ pedido_id: 8, productos: ['Promo USB + 2 Bidones'] }] } });
  assert.equal(detallesErrorRuta(sinPeso)[0], 'Sin peso calculable: #8 (Promo USB + 2 Bidones), #9. Sácalos de la selección hasta que sus productos tengan peso registrado.');
  const pesado = Object.assign(new Error('x'), { status: 422, detalle: { mensaje: 'x', capacidad_kg: 1100, sobre_capacidad: [{ pedido_id: 5, peso_kg: 1200 }] } });
  assert.equal(detallesErrorRuta(pesado)[0], 'El pedido #5 pesa 1.200 kg: solo ya supera lo que carga el camión (1.100 kg).');
  const total = Object.assign(new Error('x'), { status: 422, detalle: { mensaje: 'x', peso_total_kg: 3500, maximo_kg: 3300 } });
  assert.match(detallesErrorRuta(total)[0], /^Lo seleccionado pesa 3\.500 kg y el máximo por ruta es 3\.300 kg/);
  const confirmar = Object.assign(new Error('x'), { status: 422, detalle: { mensaje: 'x', problemas: ['viaje 1: pesa 1200 kg y la capacidad es 1100 kg'] } });
  assert.deepEqual(detallesErrorRuta(confirmar), ['viaje 1: pesa 1200 kg y la capacidad es 1100 kg', 'No se guardó nada: los pedidos siguen como estaban.']);
  const sinConfigurar = Object.assign(new Error('x'), { status: 503, detalle: { mensaje: 'x' } });
  assert.match(detallesErrorRuta(sinConfigurar)[0], /no está configurado/);

  const dormido = Object.assign(new Error('x'), { status: 504, detalle: { mensaje: 'x' } });
  assert.match(detallesErrorRuta(dormido)[0], /despertar/);

  const sinRed = new Error('No se pudo conectar con SIGPA.');
  assert.match(detallesErrorRuta(sinRed).at(-1), /pudo haberse guardado/);
});
