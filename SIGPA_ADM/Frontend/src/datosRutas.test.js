import test from 'node:test';
import assert from 'node:assert/strict';
import { aniosCalendario, etiquetaDia, etiquetaSemana, limitarMes, mesContiguo, resueltoEnTramo, rutasDeLaSemana, rutasDelTramo, semanasDelMes } from './datosRutas.js';

const pedido = (id, estado, viaje, orden_entrega, resultado_en, extra = {}) => ({ id, cliente_id: 1, estado, viaje, orden_entrega, resultado_en, direccion_despacho: null, ...extra });
const clientes = [{ id: 1, direccion: 'Calle 1' }];

test('el calendario arma las semanas del mes, de lunes a viernes', () => {
  const semanas = semanasDelMes('2026-11');
  // Noviembre de 2026 parte en domingo: el lunes 26 de octubre no tiene días hábiles del mes y queda fuera.
  assert.equal(semanas[0].lunes, '2026-11-02');
  assert.deepEqual(semanas[0].dias.map(d => d.numero), [2, 3, 4, 5, 6]);
  assert.equal(semanas.at(-1).lunes, '2026-11-30');
  assert.deepEqual(semanas.at(-1).dias.map(d => [d.numero, d.delMes]), [[30, true], [1, false], [2, false], [3, false], [4, false]]);
  assert.ok(semanas.every(s => s.dias.length === 5));
});

test('el mes anterior y el siguiente cruzan el año', () => {
  assert.equal(mesContiguo('2026-01', -1), '2025-12');
  assert.equal(mesContiguo('2026-12', 1), '2027-01');
});

test('se puede elegir el año, sin pasar del mes actual', () => {
  assert.deepEqual(aniosCalendario('2026-11-06', '2026-11', 3), ['2026', '2025', '2024']);
  assert.deepEqual(aniosCalendario('2026-11-06', '2019-03', 2), ['2026', '2025', '2019']);
  assert.equal(limitarMes('2026-12', '2026-11-06'), '2026-11');
  assert.equal(limitarMes('2025-12', '2026-11-06'), '2025-12');
});

test('la semana siempre muestra de lunes a viernes, aunque un día no tenga despachos', () => {
  const dias = rutasDeLaSemana([pedido(1, 'entregado', 1, 1, '2026-11-04T15:00:00')], clientes, '2026-11-02');
  assert.deepEqual(dias.map(d => [d.dia, d.paradas.length]), [['2026-11-02', 0], ['2026-11-03', 0], ['2026-11-04', 1], ['2026-11-05', 0], ['2026-11-06', 0]]);
});

test('un despacho excepcional en fin de semana no se pierde', () => {
  const dias = rutasDeLaSemana([pedido(1, 'entregado', 1, 1, '2026-11-07T15:00:00')], clientes, '2026-11-02');
  assert.equal(dias.length, 6);
  assert.equal(dias.at(-1).dia, '2026-11-07');
});

test('las etiquetas de semana y de día se leen en español', () => {
  assert.equal(etiquetaSemana('2026-10-26'), 'Semana del 26 oct al 30 oct');
  assert.match(etiquetaDia('2026-11-06'), /^Viernes,? 6 de noviembre$/);
});

test('reconstruye la ruta del día en el orden planificado, por viaje', () => {
  const pedidos = [
    pedido(3, 'entregado', 2, 1, '2026-11-04T16:00:00'),
    pedido(2, 'no_entregado', 1, 2, '2026-11-04T15:00:00'),
    pedido(1, 'entregado', 1, 1, '2026-11-04T17:00:00'),
  ];
  const [ruta] = rutasDelTramo(pedidos, clientes, { desde: '2026-11-04', hasta: '2026-11-04' });
  assert.deepEqual(ruta.paradas.map(p => p.id), [1, 2, 3]);
  assert.equal(ruta.paradas[0].direccion, 'Calle 1');
});

test('separa por día, de lunes a viernes, y excluye lo que no es de una ruta resuelta', () => {
  const pedidos = [
    pedido(1, 'entregado', 1, 1, '2026-11-02T15:00:00'),
    pedido(2, 'entregado', 1, 1, '2026-11-04T15:00:00'),
    pedido(3, 'entregado', null, null, '2026-11-04T15:00:00'),
    pedido(4, 'confirmado', 1, 2, null, { actualizado_en: '2026-11-04T15:00:00' }),
    pedido(5, 'cancelado', 1, 3, '2026-11-04T15:00:00'),
    pedido(6, 'entregado', 1, 1, '2026-10-20T15:00:00'),
  ];
  const rutas = rutasDelTramo(pedidos, clientes, { desde: '2026-11-02', hasta: '2026-11-08' });
  assert.deepEqual(rutas.map(r => [r.dia, r.paradas.map(p => p.id)]), [['2026-11-02', [1]], ['2026-11-04', [2]]]);
});

test('el día de la entrega se cuenta en hora de Chile', () => {
  // 01:00 UTC del 5/11 sigue siendo el 4/11 en Chile.
  const p = pedido(1, 'entregado', 1, 1, '2026-11-05T01:00:00');
  assert.equal(resueltoEnTramo(p, { desde: '2026-11-04', hasta: '2026-11-04' }), true);
  assert.equal(rutasDelTramo([p], clientes, { desde: '2026-11-04', hasta: '2026-11-04' })[0].dia, '2026-11-04');
});
