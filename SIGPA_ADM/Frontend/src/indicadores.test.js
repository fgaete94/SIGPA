import test from 'node:test';
import assert from 'node:assert/strict';
import { diaChile, hoyChile, sumarDias } from './fechas.js';
import { SIN_COMUNA, SIN_TIPO, claveGrupo, etiquetaGrupo, opcionesVentas, pedidosPorComuna, rangoPeriodo, ventasPorPeriodo } from './indicadores.js';

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

test('agrupa días en semanas (lunes) y meses', () => {
  assert.equal(claveGrupo('2026-10-12', 'semana'), '2026-10-12'); // lunes
  assert.equal(claveGrupo('2026-10-18', 'semana'), '2026-10-12'); // domingo
  assert.equal(claveGrupo('2026-10-01', 'semana'), '2026-09-28');
  assert.equal(claveGrupo('2026-10-18', 'mes'), '2026-10');
  assert.equal(claveGrupo('2026-10-18', 'dia'), '2026-10-18');
  assert.equal(etiquetaGrupo('2026-10-06', 'dia'), '6 oct');
  assert.equal(etiquetaGrupo('2026-09-28', 'semana'), 'Sem. 28 sept');
  assert.equal(etiquetaGrupo('2026-09', 'mes'), 'sept 2026');
});

test('ventas: solo entregados suman, con en curso aparte, filtros y serie continua', () => {
  const p = (id, cliente_id, estado, total, creado_en, comuna_nombre = 'Viña del Mar') => ({ id, cliente_id, estado, total, creado_en, comuna_nombre });
  const pedidos = [
    p(1, 1, 'entregado', 7000, '2026-10-05T15:00:00'),
    p(2, 2, 'entregado', 3500, '2026-10-05T18:00:00', 'Quilpué'),
    p(3, 1, 'entregado', 10500, '2026-10-07T15:00:00'),
    p(4, 3, 'cancelado', 9999, '2026-10-07T15:00:00'),
    p(5, 3, 'confirmado', 4000, '2026-10-07T15:00:00', null),
    p(6, 1, 'entregado', 1000, '2026-09-20T15:00:00'),
  ];
  const tipoPorCliente = new Map([[1, 10], [2, 20], [3, null]]);
  const r = ventasPorPeriodo(pedidos, { desde: '2026-10-05', hasta: '2026-10-07', agrupar: 'dia', tipoPorCliente });
  assert.deepEqual([r.monto, r.pedidos, r.ticket], [21000, 3, 7000]);
  assert.deepEqual(r.enCurso, { pedidos: 1, monto: 4000 });
  assert.deepEqual(r.serie.map(s => [s.clave, s.monto, s.pedidos]), [['2026-10-05', 10500, 2], ['2026-10-06', 0, 0], ['2026-10-07', 10500, 1]]);
  assert.equal(ventasPorPeriodo(pedidos, { comuna: 'Quilpué', tipoPorCliente }).monto, 3500);
  assert.deepEqual(ventasPorPeriodo(pedidos, { comuna: SIN_COMUNA, tipoPorCliente }).enCurso, { pedidos: 1, monto: 4000 });
  assert.equal(ventasPorPeriodo(pedidos, { tipo: '10', tipoPorCliente }).monto, 18500);
  assert.equal(ventasPorPeriodo(pedidos, { tipo: SIN_TIPO, tipoPorCliente }).monto, 0);
  // Sin fechas: la serie va de la primera a la última venta.
  assert.deepEqual(ventasPorPeriodo(pedidos, { agrupar: 'mes', tipoPorCliente }).serie.map(s => [s.clave, s.monto]), [['2026-09', 1000], ['2026-10', 21000]]);
  assert.deepEqual(ventasPorPeriodo([], {}), { monto: 0, pedidos: 0, ticket: 0, enCurso: { pedidos: 0, monto: 0 }, serie: [] });
});

test('opciones de filtro de ventas', () => {
  const { comunas, tipos, tipoPorCliente } = opcionesVentas(
    [{ comuna_nombre: 'Quilpué' }, { comuna_nombre: null }, { comuna_nombre: 'Concón' }, { comuna_nombre: 'Quilpué' }],
    [{ id: 1, tipo_cliente_id: 2, tipo_cliente_nombre: 'Hogar' }, { id: 2, tipo_cliente_id: 1, tipo_cliente_nombre: 'Empresa' }, { id: 3, tipo_cliente_id: null }],
  );
  assert.deepEqual(comunas, ['Concón', 'Quilpué', SIN_COMUNA]);
  assert.deepEqual(tipos, [['1', 'Empresa'], ['2', 'Hogar'], [SIN_TIPO, 'Sin tipo asignado']]);
  assert.equal(tipoPorCliente.get(1), 2);
});
