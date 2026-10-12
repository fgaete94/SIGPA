import test from 'node:test';
import assert from 'node:assert/strict';
import { diaChile, hoyChile, sumarDias } from './fechas.js';
import { SIN_COMUNA, pedidosPorComuna, rangoPeriodo } from './indicadores.js';

test('las fechas sin zona del backend se leen como UTC y se pasan al día de Chile', () => {
  // 01:30 UTC del 9 de octubre = 22:30 del 8 de octubre en Chile (UTC-3).
  assert.equal(diaChile('2026-10-09T01:30:00'), '2026-10-08');
  assert.equal(diaChile('2026-10-09T01:30:00Z'), '2026-10-08');
  assert.equal(diaChile('2026-10-09T01:30:00+00:00'), '2026-10-08');
  assert.equal(diaChile('2026-10-09T12:00:00.123456'), '2026-10-09');
  assert.equal(hoyChile(new Date('2026-10-13T02:00:00Z')), '2026-10-12');
});

test('períodos predefinidos', () => {
  assert.equal(sumarDias('2026-03-01', -1), '2026-02-28');
  assert.deepEqual(rangoPeriodo('7', '2026-10-12'), { desde: '2026-10-06', hasta: '2026-10-12' });
  assert.deepEqual(rangoPeriodo('30', '2026-10-12'), { desde: '2026-09-13', hasta: '2026-10-12' });
  assert.deepEqual(rangoPeriodo('mes', '2026-10-12'), { desde: '2026-10-01', hasta: '2026-10-12' });
  assert.deepEqual(rangoPeriodo('todo', '2026-10-12'), { desde: '', hasta: '' });
});

test('agrupa por comuna sin cancelados, de mayor a menor y con "sin comuna" al final', () => {
  const p = (id, comuna_nombre, creado_en, extra = {}) => ({ id, cliente_id: id, comuna_nombre, creado_en, estado: 'pendiente', ...extra });
  const pedidos = [
    p(1, 'Viña del Mar', '2026-10-01T15:00:00'),
    p(2, 'Viña del Mar', '2026-10-02T15:00:00', { cliente_id: 1 }),
    p(3, null, '2026-10-02T15:00:00'),
    p(4, null, '2026-10-02T15:00:00'),
    p(5, null, '2026-10-02T15:00:00'),
    p(6, 'Quilpué', '2026-10-03T15:00:00'),
    p(7, 'Quilpué', '2026-10-03T15:00:00', { estado: 'cancelado' }),
    p(8, 'Concón', '2026-09-20T15:00:00'),
  ];
  const { total, filas } = pedidosPorComuna(pedidos, { desde: '2026-10-01', hasta: '2026-10-31' });
  assert.equal(total, 6);
  assert.deepEqual(filas.map(f => [f.comuna, f.pedidos, f.clientes]), [['Viña del Mar', 2, 1], ['Quilpué', 1, 1], [SIN_COMUNA, 3, 3]]);
  assert.equal(filas[0].porcentaje, 2 / 6);
  assert.deepEqual(pedidosPorComuna(pedidos, {}).filas.map(f => f.comuna), ['Viña del Mar', 'Concón', 'Quilpué', SIN_COMUNA]);
  assert.deepEqual(pedidosPorComuna([], {}), { total: 0, filas: [] });
});
