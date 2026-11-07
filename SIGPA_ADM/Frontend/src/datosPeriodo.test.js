import test from 'node:test';
import assert from 'node:assert/strict';
import { canceladosEnPeriodo, diasEntre, lunesDe, resumenPeriodo, tramoEfectivo } from './datosPeriodo.js';

test('un período sin límite parte en el primer pedido resuelto y llega a hoy', () => {
  const lista = [{ estado: 'entregado', resultado_en: '2026-09-10T15:00:00' }, { estado: 'pendiente', creado_en: '2026-01-01T15:00:00' }];
  assert.deepEqual(tramoEfectivo(lista, { desde: '', hasta: '' }, '2026-11-06'), { desde: '2026-09-10', hasta: '2026-11-06' });
  assert.deepEqual(tramoEfectivo(lista, { desde: '2026-10-01', hasta: '2026-10-31' }, '2026-11-06'), { desde: '2026-10-01', hasta: '2026-10-31' });
  assert.deepEqual(tramoEfectivo([], {}, '2026-11-06'), { desde: '2026-11-06', hasta: '2026-11-06' });
});

test('diasEntre incluye los dos extremos y cruza meses', () => {
  assert.deepEqual(diasEntre('2026-10-30', '2026-11-02'), ['2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02']);
  assert.deepEqual(diasEntre('2026-11-02', '2026-11-01'), []);
});

test('lunesDe devuelve el lunes de la semana', () => {
  assert.equal(lunesDe('2026-11-06'), '2026-11-02');
  assert.equal(lunesDe('2026-11-08'), '2026-11-02');
  assert.equal(lunesDe('2026-11-02'), '2026-11-02');
});

const pedidos = [
  { estado: 'entregado', total: 5000, medio_pago: 'efectivo', resultado_en: '2026-11-02T15:00:00' },
  { estado: 'entregado', total: 3000, medio_pago: 'transferencia', resultado_en: '2026-11-02T16:00:00' },
  { estado: 'entregado', total: 2000, actualizado_en: '2026-11-03T15:00:00' },
  { estado: 'no_entregado', total: 9000, resultado_en: '2026-11-03T15:00:00' },
  { estado: 'entregado', total: 7000, medio_pago: 'efectivo', resultado_en: '2026-11-20T15:00:00' },
  { estado: 'confirmado', total: 4000, actualizado_en: '2026-11-03T15:00:00' },
];

test('resume la venta, lo entregado, lo no entregado y el pago solo dentro del tramo', () => {
  const r = resumenPeriodo(pedidos, { desde: '2026-11-02', hasta: '2026-11-08' });
  assert.equal(r.porSemana, false);
  assert.equal(r.filas.length, 7);
  assert.equal(r.total, 10000);
  assert.equal(r.entregados, 3);
  assert.equal(r.noEntregados, 1);
  assert.deepEqual(r.pagos, { efectivo: 5000, transferencia: 3000, sinDato: 2000 });
  assert.deepEqual(r.filas.slice(0, 2).map(f => [f.etiqueta, f.monto, f.entregados, f.noEntregados]), [['Lun 2', 8000, 2, 0], ['Mar 3', 2000, 1, 1]]);
});

test('con más de dos semanas la etiqueta lleva el mes', () => {
  const r = resumenPeriodo(pedidos, { desde: '2026-11-01', hasta: '2026-11-30' });
  assert.equal(r.filas[1].etiqueta, '2 nov');
  assert.equal(r.total, 17000);
});

test('un tramo largo se agrupa por semana sin perder montos', () => {
  const r = resumenPeriodo(pedidos, { desde: '2026-09-01', hasta: '2026-11-30' });
  assert.equal(r.porSemana, true);
  assert.equal(r.total, 17000);
  assert.ok(r.filas.length < 20);
  assert.equal(r.filas.find(f => f.clave === '2026-11-02').monto, 10000);
});

test('el día se cuenta en hora de Chile', () => {
  // 01:00 UTC del 9/11 sigue siendo el 8/11 en Chile.
  const r = resumenPeriodo([{ estado: 'entregado', total: 1000, resultado_en: '2026-11-09T01:00:00' }], { desde: '2026-11-02', hasta: '2026-11-08' });
  assert.equal(r.filas[6].monto, 1000);
});


test('cuenta los cancelados del período y suma su monto', () => {
  const lista = [
    { estado: 'cancelado', total: 5000, actualizado_en: '2026-11-02T15:00:00' },
    { estado: 'cancelado', total: 3000, actualizado_en: '2026-10-20T15:00:00' },
    { estado: 'no_entregado', total: 9000, resultado_en: '2026-11-02T15:00:00' },
  ];
  assert.deepEqual(canceladosEnPeriodo(lista, { desde: '2026-11-01', hasta: '2026-11-30' }), { cantidad: 1, monto: 5000 });
  assert.deepEqual(canceladosEnPeriodo(lista, { desde: '', hasta: '' }), { cantidad: 2, monto: 8000 });
  assert.deepEqual(canceladosEnPeriodo([], {}), { cantidad: 0, monto: 0 });
});
