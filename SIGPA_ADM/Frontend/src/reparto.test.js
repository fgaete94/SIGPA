import test from 'node:test';
import assert from 'node:assert/strict';
import { enlaceComoLlegar, resumenEntregas, rutaEnCurso, textoResultado } from './reparto.js';

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

test('lo entregado hoy sigue en la ruta, en su lugar; lo de otro día no', () => {
  const hoy = '2026-11-06';
  const pedidos = [
    pedido(1, 'entregado', 1, { actualizado_en: '2026-11-06T15:00:00' }),
    pedido(2, 'en_despacho', 2),
    pedido(3, 'entregado', 3, { actualizado_en: '2026-11-05T15:00:00' }),
    pedido(4, 'entregado', null, { actualizado_en: '2026-11-06T15:00:00' }),
  ];
  assert.deepEqual(rutaEnCurso(pedidos, [], hoy).paradas.map(p => p.id), [1, 2]);
});

test('el día de la entrega se cuenta en hora de Chile', () => {
  // 01:00 UTC del 7/11 sigue siendo el 6/11 en Chile.
  const pedidos = [pedido(1, 'entregado', 1, { actualizado_en: '2026-11-07T01:00:00' })];
  assert.equal(rutaEnCurso(pedidos, [], '2026-11-06').paradas.length, 1);
  assert.equal(rutaEnCurso(pedidos, [], '2026-11-07').paradas.length, 0);
});

test('una ruta nueva del mismo día manda sobre lo entregado con el mismo número y no se avisa como repetido', () => {
  const pedidos = [
    pedido(1, 'entregado', 1, { viaje: 1, actualizado_en: '2026-11-06T15:00:00' }),
    pedido(2, 'entregado', 2, { viaje: 1, actualizado_en: '2026-11-06T15:00:00' }),
    pedido(3, 'confirmado', 1, { viaje: 1 }),
  ];
  const { paradas, repetidos } = rutaEnCurso(pedidos, [], '2026-11-06');
  assert.deepEqual(paradas.map(p => p.id), [3, 2]);
  assert.deepEqual(repetidos, []);
});

test('lo no entregado hoy sigue en la ruta, marcado', () => {
  const pedidos = [pedido(1, 'no_entregado', 1, { resultado_en: '2026-11-06T15:00:00' }), pedido(2, 'no_entregado', 2, { resultado_en: '2026-11-05T15:00:00' })];
  assert.deepEqual(rutaEnCurso(pedidos, [], '2026-11-06').paradas.map(p => p.id), [1]);
});

test('textoResultado dice el resultado, el método de pago y la hora de Chile', () => {
  assert.equal(textoResultado({ estado: 'entregado', medio_pago: 'efectivo', resultado_en: '2026-11-06T17:32:00' }), '✓ Entregado · Efectivo · 14:32');
  assert.equal(textoResultado({ estado: 'entregado', resultado_en: '2026-11-06T17:32:00' }), '✓ Entregado · 14:32');
  assert.equal(textoResultado({ estado: 'no_entregado', resultado_en: '2026-11-06T18:10:00' }), '✗ No entregado · 15:10');
  assert.equal(textoResultado({ estado: 'entregado', entrega_ajustada: true, total: 5000, medio_pago: 'efectivo', resultado_en: '2026-11-06T17:32:00' }), '✓ Entrega ajustada · $5.000 · Efectivo · 14:32');
  assert.equal(textoResultado({ estado: 'confirmado' }), null);
});

test('resumenEntregas cuenta las paradas por estado y suma lo entregado y lo que falta', () => {
  const paradas = [pedido(1, 'entregado', 1, { total: 5000 }), pedido(2, 'no_entregado', 2, { total: 9000 }), pedido(3, 'confirmado', 3, { total: 3000 }), pedido(4, 'en_despacho', 4, { total: 2000 })];
  assert.deepEqual(resumenEntregas(paradas), { total: 4, entregadas: 1, noEntregadas: 1, porEntregar: 2, montoEntregado: 5000, montoPorEntregar: 5000 });
  assert.deepEqual(resumenEntregas([]), { total: 0, entregadas: 0, noEntregadas: 0, porEntregar: 0, montoEntregado: 0, montoPorEntregar: 0 });
});
