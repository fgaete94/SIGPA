import test from 'node:test';
import assert from 'node:assert/strict';
import { csvRanking, nombreArchivo, parametrosRanking, rankingConParticipacion } from './productosVendidos.js';

const filas = [
  { producto_id: 4, producto_nombre: 'Bidón 20L Recarga', unidades: 30, monto: 75000, pedidos: 20 },
  { producto_id: 2, producto_nombre: 'Bidón 12L Recarga', unidades: 8, monto: 16000, pedidos: 6 },
  { producto_id: 7, producto_nombre: 'Promo "USB" + 1; Bidón', unidades: 2, monto: 22000, pedidos: 2 },
];

test('parámetros del período y del modo', () => {
  assert.equal(parametrosRanking({ desde: '2026-10-01', hasta: '2026-10-18' }, 'pedidos'), 'contar=pedidos&desde=2026-10-01&hasta=2026-10-18');
  assert.equal(parametrosRanking({ desde: '', hasta: '' }), 'contar=entregados');
  assert.equal(parametrosRanking(undefined, 'entregados'), 'contar=entregados');
});

test('posición, participación en unidades y largo relativo de la barra', () => {
  const { total, filas: r } = rankingConParticipacion(filas);
  assert.equal(total, 40);
  assert.deepEqual(r.map(f => f.posicion), [1, 2, 3]);
  assert.equal(r[0].porcentaje, 0.75);
  assert.equal(r[0].relativo, 1);
  assert.equal(r[1].relativo, 8 / 30);
  assert.deepEqual(rankingConParticipacion([]), { total: 0, filas: [] });
});

test('CSV con punto y coma, decimales con coma y textos escapados', () => {
  const csv = csvRanking(rankingConParticipacion(filas).filas, { desde: '2026-10-01', hasta: '2026-10-18' }, 'entregados').split('\r\n');
  assert.equal(csv[0], 'Productos más vendidos (Vendido (pedidos entregados)), 2026-10-01 a 2026-10-18');
  assert.equal(csv[1], 'Posición;Producto;Unidades;Monto (CLP);Pedidos;Participación (%)');
  assert.equal(csv[2], '1;Bidón 20L Recarga;30;75000;20;75,0');
  assert.equal(csv[4], '3;"Promo ""USB"" + 1; Bidón";2;22000;2;5,0');
  assert.match(csvRanking([], {}, 'pedidos'), /todo el historial/);
  assert.equal(nombreArchivo({ desde: '2026-10-01', hasta: '2026-10-18' }), 'productos-mas-vendidos_2026-10-01_2026-10-18.csv');
  assert.equal(nombreArchivo({}), 'productos-mas-vendidos.csv');
});
