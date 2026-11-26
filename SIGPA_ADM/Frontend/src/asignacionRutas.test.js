import test from 'node:test';
import assert from 'node:assert/strict';
import { asignacionesPorRepartidor, claveRuta, nombreRutas, repartidorDeRuta } from './asignacionRutas.js';

const paradas = [{ pedido_id: 1, viaje: 1 }, { pedido_id: 2, viaje: 1 }, { pedido_id: 3, viaje: 2 }, { pedido_id: 4, viaje: 3 }];

test('cada ruta va a su repartidor; dos rutas del mismo repartidor se juntan', () => {
  assert.deepEqual(asignacionesPorRepartidor(paradas, { 1: '7', 2: '9', 3: '7' }), [
    { repartidor_id: 7, rutas: [1, 3], pedido_ids: [1, 2, 4] },
    { repartidor_id: 9, rutas: [2], pedido_ids: [3] },
  ]);
});

test('una ruta sin repartidor elegido no se asigna', () => {
  assert.deepEqual(asignacionesPorRepartidor(paradas, { 1: '7', 2: '' }), [{ repartidor_id: 7, rutas: [1], pedido_ids: [1, 2] }]);
  assert.deepEqual(asignacionesPorRepartidor(paradas, {}), []);
});

test('paradas sin viaje usan la clave "sin"', () => {
  assert.equal(claveRuta(null), 'sin');
  assert.equal(claveRuta(2), '2');
  assert.deepEqual(asignacionesPorRepartidor([{ pedido_id: 5 }], { sin: '3' }), [{ repartidor_id: 3, rutas: [], pedido_ids: [5] }]);
});

test('nombreRutas arma el texto del aviso', () => {
  assert.equal(nombreRutas([]), 'la ruta');
  assert.equal(nombreRutas([2]), 'Ruta 2');
  assert.equal(nombreRutas([1, 2, 3]), 'Ruta 1, Ruta 2 y Ruta 3');
});

test('repartidorDeRuta: el de lo pendiente; mixta o sin asignar, vacío', () => {
  const resuelta = p => p.estado === 'entregado';
  assert.equal(repartidorDeRuta([{ repartidor_id: 7, estado: 'entregado' }, { repartidor_id: 9 }], resuelta), '9');
  assert.equal(repartidorDeRuta([{ repartidor_id: 7, estado: 'entregado' }], resuelta), '7', 'todo resuelto: quién la llevó');
  assert.equal(repartidorDeRuta([{ repartidor_id: 7 }, { repartidor_id: 9 }], resuelta), '');
  assert.equal(repartidorDeRuta([{ repartidor_id: null }], resuelta), '');
});
