import test from 'node:test';
import assert from 'node:assert/strict';
import { COLORES_VIAJE, capasMapa, capasVisibles, claveEncuadre, sinCoordenadas, tieneCoordenadas } from './capasMapa.js';

const parada = (pedido_id, latitud = -33 - pedido_id / 100, longitud = -71.5) => ({ pedido_id, cliente_nombre: `C${pedido_id}`, direccion_texto: `Calle ${pedido_id}`, latitud, longitud });

test('los puntos se numeran como la lista y marcan la primera y la última parada del viaje', () => {
  const [capa] = capasMapa([{ viaje: 1, paradas: [parada(1), parada(2), parada(3)] }]);
  assert.deepEqual(capa.puntos.map(p => [p.id, p.numero, p.tipo]), [[1, 1, 'inicio'], [2, 2, ''], [3, 3, 'fin']]);
  assert.equal(capa.etiqueta, 'Viaje 1');
  assert.equal(capa.puntos[0].nombre, 'C1');
  assert.equal(capa.puntos[0].direccion, 'Calle 1');
});

test('una parada sola es la primera', () => {
  const [capa] = capasMapa([{ viaje: 1, paradas: [parada(1)] }]);
  assert.equal(capa.puntos[0].tipo, 'inicio');
});

test('una parada sin coordenadas no se dibuja, pero no corre el número de las demás', () => {
  const capas = capasMapa([{ viaje: 1, paradas: [parada(1), { ...parada(2), latitud: null }, parada(3)] }]);
  assert.deepEqual(capas[0].puntos.map(p => p.numero), [1, 3]);
  assert.equal(sinCoordenadas(capas), 1);
});

test('tieneCoordenadas exige las dos, y el 0 cuenta', () => {
  assert.equal(tieneCoordenadas({ latitud: 0, longitud: 0 }), true);
  assert.equal(tieneCoordenadas({ latitud: -33 }), false);
  assert.equal(tieneCoordenadas({ latitud: null, longitud: -71 }), false);
});

test('cada viaje tiene su color y en la vista previa se muestra el número renumerado', () => {
  const capas = capasMapa([{ viaje: 1, numero: 1, paradas: [parada(1)] }, { viaje: 3, numero: 2, paradas: [parada(2)] }]);
  assert.deepEqual(capas.map(c => c.etiqueta), ['Viaje 1', 'Viaje 2']);
  assert.deepEqual(capas.map(c => c.color), COLORES_VIAJE.slice(0, 2));
  assert.deepEqual(capas.map(c => c.clave), [1, 3]);
});

test('una ruta anterior a los viajes va en una capa propia', () => {
  const [capa] = capasMapa([{ viaje: null, paradas: [parada(1)] }]);
  assert.equal(capa.clave, 'sin-viaje');
  assert.equal(capa.etiqueta, 'Sin viaje');
});

test('los pedidos guardados usan id y direccion', () => {
  const [capa] = capasMapa([{ viaje: 1, paradas: [{ id: 7, cliente_id: 2, direccion: 'Calle 7', latitud: -33, longitud: -71 }] }]);
  assert.equal(capa.puntos[0].id, 7);
  assert.equal(capa.puntos[0].nombre, 'Pedido #7');
  assert.equal(capa.puntos[0].direccion, 'Calle 7');
});

test('los puntos en el mismo lugar se desplazan, también entre viajes', () => {
  const capas = capasMapa([{ viaje: 1, paradas: [parada(1, -33, -71), parada(2, -33, -71)] }, { viaje: 2, paradas: [parada(3, -33, -71), parada(4, -34, -71)] }]);
  assert.deepEqual(capas.flatMap(c => c.puntos.map(p => p.desfase)), [0, 1, 2, 0]);
});

test('reordenar no cambia la clave del encuadre; sacar una parada sí', () => {
  const clave = paradas => claveEncuadre(capasMapa([{ viaje: 1, paradas }]));
  assert.equal(clave([parada(10), parada(9)]), clave([parada(9), parada(10)]));
  assert.notEqual(clave([parada(1), parada(2)]), clave([parada(1)]));
});

test('el filtro muestra el viaje elegido y vuelve a todos si queda vacío', () => {
  const capas = capasMapa([{ viaje: 1, paradas: [parada(1)] }, { viaje: 2, paradas: [{ ...parada(2), longitud: null }] }]);
  assert.deepEqual(capasVisibles(capas, 1).map(c => c.clave), [1]);
  assert.equal(capasVisibles(capas, 2).length, 2);
  assert.equal(capasVisibles(capas, null).length, 2);
});
