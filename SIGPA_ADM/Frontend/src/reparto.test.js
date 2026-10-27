import test from 'node:test';
import assert from 'node:assert/strict';
import { enlaceComoLlegar, enlaceWhatsApp, rutaEnCurso, textoRuta } from './reparto.js';

const pedido = (id, estado, orden_entrega, extra = {}) => ({ id, cliente_id: id, cliente_nombre: `Cliente ${id}`, estado, orden_entrega, direccion_despacho: null, latitud: null, longitud: null, motivo_revision_direccion: null, ...extra });

test('la ruta en curso son los confirmados o en despacho con orden, ordenados por parada', () => {
  const pedidos = [
    pedido(1, 'confirmado', 2, { direccion_despacho: 'Av. Libertad 100' }),
    pedido(2, 'en_despacho', 1),
    pedido(3, 'entregado', 3),
    pedido(4, 'pendiente', null),
    pedido(5, 'confirmado', null, { motivo_revision_direccion: 'No se encontró' }),
  ];
  const { paradas, repetidos, sinUbicacion } = rutaEnCurso(pedidos, [{ id: 2, direccion: 'Los Carrera 1200' }]);
  assert.deepEqual(paradas.map(p => [p.id, p.direccion]), [[2, 'Los Carrera 1200'], [1, 'Av. Libertad 100']]);
  assert.deepEqual(repetidos, []);
  assert.equal(sinUbicacion, 1);
  // Dos planificaciones distintas: ambas empiezan en 1.
  assert.deepEqual(rutaEnCurso([pedido(7, 'confirmado', 1), pedido(6, 'confirmado', 1), pedido(8, 'confirmado', 2)]).repetidos, [{ viaje: null, numeros: [1] }]);
  assert.deepEqual(rutaEnCurso([pedido(7, 'confirmado', 1), pedido(6, 'confirmado', 1)]).paradas.map(p => p.id), [6, 7]);
});

test('enlaces de Google Maps y texto para el repartidor', () => {
  assert.equal(enlaceComoLlegar({ latitud: -33.0478, longitud: -71.44 }), 'https://www.google.com/maps/dir/?api=1&destination=-33.0478%2C-71.44');
  assert.equal(enlaceComoLlegar({ latitud: null, longitud: null, direccion: 'Calle 5 Norte 10' }), 'https://www.google.com/maps/dir/?api=1&destination=Calle%205%20Norte%2010%2C%20Chile');
  assert.equal(enlaceComoLlegar({ latitud: null, longitud: null, direccion: null }), null);
  const texto = textoRuta([
    { id: 9, cliente_id: 3, cliente_nombre: 'Ana Rojas', cliente_telefono: '56911112222', direccion: 'Av. Libertad 100', latitud: -33, longitud: -71.5 },
    { id: 4, cliente_id: 5, cliente_nombre: null, direccion: null, latitud: null, longitud: null },
  ], '12-10-2026');
  assert.equal(texto, [
    'Ruta Agua DM · 12-10-2026 · 2 paradas',
    '1. Ana Rojas (pedido #9)\nAv. Libertad 100\nTel: 56911112222\nhttps://www.google.com/maps/dir/?api=1&destination=-33%2C-71.5',
    '2. Cliente 5 (pedido #4)\nSin dirección registrada',
  ].join('\n\n'));
  assert.equal(enlaceWhatsApp('Ruta 1 & 2'), 'https://wa.me/?text=Ruta%201%20%26%202');
});

test('con viajes: orden por viaje, el mismo número en viajes distintos no es repetido y el texto lleva encabezados', () => {
  const pedidos = [
    pedido(1, 'confirmado', 1, { viaje: 2 }), pedido(2, 'confirmado', 2, { viaje: 1 }),
    pedido(3, 'confirmado', 1, { viaje: 1 }), pedido(4, 'confirmado', 2, { viaje: 2 }),
  ];
  const { paradas, repetidos } = rutaEnCurso(pedidos);
  assert.deepEqual(paradas.map(p => p.id), [3, 2, 1, 4]);
  assert.deepEqual(repetidos, []);
  assert.deepEqual(rutaEnCurso([...pedidos, pedido(5, 'confirmado', 1, { viaje: 2 })]).repetidos, [{ viaje: 2, numeros: [1] }]);
  const texto = textoRuta(paradas, '27-10-2026', [{ viaje: 1, peso_kg: 900, termina_en: 'planta' }, { viaje: 2, peso_kg: 300, termina_en: 'casa' }]);
  assert.deepEqual(texto.split('\n\n').map(b => b.split('\n')[0]), [
    'Ruta Agua DM · 27-10-2026 · 4 paradas',
    '*Viaje 1 · 900 kg · termina en planta*', '1. Cliente 3 (pedido #3)', '2. Cliente 2 (pedido #2)',
    '*Viaje 2 · 300 kg · termina en casa del despachador*', '1. Cliente 1 (pedido #1)', '2. Cliente 4 (pedido #4)',
  ]);
});
