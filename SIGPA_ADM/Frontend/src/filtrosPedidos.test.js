import test from 'node:test';
import assert from 'node:assert/strict';
import { SIN_DIA, VISTA_INICIAL, VISTAS_PEDIDOS, cuentaVistas, filtrarPedidos, opcionesFiltros, siguienteDiaReparto, totalDe } from './filtrosPedidos.js';
import { SIN_COMUNA } from './indicadores.js';

const pedidos = [
  { id: 1, cliente_nombre: 'Carla Muñoz', comuna_nombre: 'Quilpué', estado: 'pendiente', creado_en: '2026-11-02T15:00:00' },
  { id: 2, cliente_nombre: 'Ana Rojas', comuna_nombre: 'Viña del Mar', estado: 'entregado', creado_en: '2026-11-03T15:00:00' },
  { id: 3, cliente_nombre: 'Ana Vera', comuna_nombre: 'Viña del Mar', estado: 'pendiente', creado_en: '2026-10-30T15:00:00' },
  { id: 40, cliente_nombre: 'Luis Araya', comuna_nombre: null, estado: 'cancelado', creado_en: '2026-11-04T15:00:00' },
];
const ids = filtros => filtrarPedidos(pedidos, filtros).map(p => p.id);

test('sin filtros aparecen todos', () => {
  assert.deepEqual(ids({}), [1, 2, 3, 40]);
});

test('el buscador encuentra por cliente o número de pedido, sin importar acentos ni mayúsculas', () => {
  assert.deepEqual(ids({ texto: 'ana' }), [2, 3]);
  assert.deepEqual(ids({ texto: ' MUNOZ ' }), [1]);
  assert.deepEqual(ids({ texto: '40' }), [40]);
  // La comuna tiene su propio filtro: el buscador ya no busca en ella.
  assert.deepEqual(ids({ texto: 'quilpué' }), []);
});

test('filtra por estado y por comuna, incluidos los pedidos sin comuna', () => {
  assert.deepEqual(ids({ estado: 'pendiente' }), [1, 3]);
  assert.deepEqual(ids({ comuna: 'Viña del Mar' }), [2, 3]);
  assert.deepEqual(ids({ comuna: SIN_COMUNA }), [40]);
});

test('los filtros se combinan', () => {
  assert.deepEqual(ids({ estado: 'pendiente', comuna: 'Viña del Mar', texto: 'vera' }), [3]);
});

test('cada opción cuenta con los demás filtros aplicados, sin contar el propio', () => {
  const { comunas } = opcionesFiltros(pedidos, { estado: 'pendiente', comuna: 'Viña del Mar' });
  assert.deepEqual(comunas, [['Quilpué', 1], ['Viña del Mar', 1]]);
});

test('la comuna elegida se mantiene aunque quede sin pedidos, y "sin comuna" va al final', () => {
  assert.deepEqual(opcionesFiltros(pedidos, { comuna: 'Concón', estado: 'cancelado' }).comunas, [['Concón', 0], [SIN_COMUNA, 1]]);
});

test('la pestaña abre con los pendientes y permite ver los no entregados', () => {
  assert.equal(VISTA_INICIAL, 'pendiente');
  assert.deepEqual(VISTAS_PEDIDOS.map(([v]) => v), ['pendiente', 'no_entregado']);
  assert.deepEqual(ids({ estado: VISTA_INICIAL }), [1, 3]);
  assert.deepEqual(ids({ estado: 'cancelado' }), [40]);
});

test('las vistas cuentan con los demás filtros aplicados', () => {
  const cuenta = cuentaVistas(pedidos, { estado: 'pendiente', comuna: 'Viña del Mar' });
  assert.equal(cuenta.get('pendiente'), 1);
  assert.equal(cuenta.get('cancelado'), undefined);
  assert.equal(cuentaVistas(pedidos, { estado: 'pendiente' }).get('cancelado'), 1);
});

test('filtra por día de reparto y ordena los días de la semana', () => {
  const conDia = [{ id: 1, estado: 'pendiente', dia_reparto: 'Jueves', creado_en: '2026-11-02T15:00:00' }, { id: 2, estado: 'pendiente', dia_reparto: 'Lunes', creado_en: '2026-11-02T15:00:00' }, { id: 3, estado: 'pendiente', dia_reparto: null, creado_en: '2026-11-02T15:00:00' }];
  assert.deepEqual(filtrarPedidos(conDia, { dia: 'Lunes' }).map(p => p.id), [2]);
  assert.deepEqual(filtrarPedidos(conDia, { dia: SIN_DIA }).map(p => p.id), [3]);
  assert.deepEqual(opcionesFiltros(conDia, {}).dias, [['Lunes', 1], ['Jueves', 1], [SIN_DIA, 1]]);
});

test('suma el total de lo filtrado', () => {
  assert.equal(totalDe([{ total: 3500 }, { total: '7000' }, {}]), 10500);
});

test('se reagenda para el siguiente día con reparto, saltando el fin de semana', () => {
  assert.equal(siguienteDiaReparto('2026-11-02'), '2026-11-03'); // lunes a martes
  assert.equal(siguienteDiaReparto('2026-11-06'), '2026-11-09'); // viernes a lunes
  assert.equal(siguienteDiaReparto('2026-11-07'), '2026-11-09'); // sábado a lunes
  assert.equal(siguienteDiaReparto('2026-11-08'), '2026-11-09'); // domingo a lunes
});
