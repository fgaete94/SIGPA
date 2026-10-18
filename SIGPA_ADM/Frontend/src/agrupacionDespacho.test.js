import test from 'node:test';
import assert from 'node:assert/strict';
import { SIN_COMUNA, SIN_DIA, diaHoy, gruposDespacho, opcionesDespacho, pedidosVista } from './agrupacionDespacho.js';

const pedido = (id, estado, dia_reparto, comuna_nombre, creado_en = '2026-10-15T12:00:00', total = 2500) => ({ id, estado, dia_reparto, comuna_nombre, creado_en, total });
const pedidos = [
  pedido(1, 'confirmado', 'Miercoles', 'Viña del Mar', '2026-10-14T12:00:00'),
  pedido(2, 'en_despacho', 'Lunes', 'Quilpué'),
  pedido(3, 'entregado', 'Lunes', 'Quilpué'),
  pedido(4, 'pendiente', 'Lunes', 'Quilpué'),
  pedido(5, 'cancelado', 'Martes', 'Concón'),
  pedido(6, 'confirmado', null, null),
  pedido(7, 'confirmado', 'Miercoles', 'Viña del Mar', '2026-10-15T12:00:00', 4000),
  pedido(8, 'confirmado', 'Miercoles', 'Concón'),
];

test('la vista por defecto deja lo que falta entregar; pendientes y cancelados nunca aparecen', () => {
  assert.deepEqual(pedidosVista(pedidos, 'por_entregar').map(p => p.id), [1, 2, 6, 7, 8]);
  assert.deepEqual(pedidosVista(pedidos, 'entregados').map(p => p.id), [3]);
  assert.deepEqual(pedidosVista(pedidos, 'todos').map(p => p.id), [1, 2, 3, 6, 7, 8]);
  assert.deepEqual(pedidosVista(pedidos, 'otra').map(p => p.id), [1, 2, 6, 7, 8]);
});

test('agrupa por día de reparto y comuna, en orden de la semana y con lo sin asignar al final', () => {
  const grupos = gruposDespacho(pedidosVista(pedidos, 'por_entregar'));
  assert.deepEqual(grupos.map(g => [g.dia, g.cantidad]), [['Lunes', 1], ['Miercoles', 3], [SIN_DIA, 1]]);
  const miercoles = grupos[1];
  assert.deepEqual(miercoles.comunas.map(c => c.comuna), ['Concón', 'Viña del Mar']);
  // Dentro de la comuna, el más reciente primero, y el total del grupo.
  assert.deepEqual(miercoles.comunas[1].pedidos.map(p => p.id), [7, 1]);
  assert.equal(miercoles.comunas[1].total, 6500);
  assert.equal(grupos[2].comunas[0].comuna, SIN_COMUNA);
});

test('filtra por día y comuna', () => {
  const base = pedidosVista(pedidos, 'todos');
  // Misma fecha: primero el de id mayor (el último ingresado).
  assert.deepEqual(gruposDespacho(base, { dia: 'Lunes' }).flatMap(g => g.comunas.flatMap(c => c.pedidos.map(p => p.id))), [3, 2]);
  assert.deepEqual(gruposDespacho(base, { comuna: 'Concón' }).flatMap(g => g.comunas.flatMap(c => c.pedidos.map(p => p.id))), [8]);
  assert.deepEqual(gruposDespacho(base, { dia: SIN_DIA, comuna: SIN_COMUNA }).map(g => g.cantidad), [1]);
  assert.deepEqual(gruposDespacho(base, { dia: 'Viernes' }), []);
});

test('las opciones de los filtros traen su cantidad', () => {
  const { dias, comunas } = opcionesDespacho(pedidosVista(pedidos, 'por_entregar'));
  assert.deepEqual(dias, [['Lunes', 1], ['Miercoles', 3], [SIN_DIA, 1]]);
  assert.deepEqual(comunas, [['Concón', 1], ['Quilpué', 1], ['Viña del Mar', 2], [SIN_COMUNA, 1]]);
});

test('el día de hoy en Chile, o null el fin de semana', () => {
  assert.equal(diaHoy(new Date('2026-10-14T15:00:00Z')), 'Miercoles');
  // 02:00 UTC del martes sigue siendo lunes en Chile.
  assert.equal(diaHoy(new Date('2026-10-13T02:00:00Z')), 'Lunes');
  assert.equal(diaHoy(new Date('2026-10-18T15:00:00Z')), null);
});
