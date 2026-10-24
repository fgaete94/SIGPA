import test from 'node:test';
import assert from 'node:assert/strict';
import { destinoArrastre, moveStop, moverA } from './route.js';
test('reordenar conserva todos los pedidos y no modifica el original', () => {
  const source = [{ pedido_id: 1 }, { pedido_id: 2 }, { pedido_id: 3 }];
  const moved = moveStop(source, 1, -1);
  assert.deepEqual(moved.map(p => p.pedido_id), [2, 1, 3]);
  assert.deepEqual(source.map(p => p.pedido_id), [1, 2, 3]);
  assert.equal(moveStop(source, 0, -1), source);
  assert.equal(moveStop(source, 2, 1), source);
});

test('arrastrar y soltar mueve la parada a la posición elegida y corre las demás', () => {
  const ids = lista => lista.map(p => p.pedido_id);
  const source = [1, 2, 3, 4, 5].map(pedido_id => ({ pedido_id }));
  assert.deepEqual(ids(moverA(source, 0, 3)), [2, 3, 4, 1, 5]);
  assert.deepEqual(ids(moverA(source, 4, 1)), [1, 5, 2, 3, 4]);
  assert.deepEqual(ids(source), [1, 2, 3, 4, 5], 'no modifica el original');
  assert.equal(moverA(source, 2, 2), source);
  assert.equal(moverA(source, 0, 9), source);
});

test('la parada arrastrada cae según cuántos centros pasó', () => {
  const centros = [25, 75, 125, 175];
  assert.equal(destinoArrastre(centros, 0, 25), 0, 'sin moverse');
  assert.equal(destinoArrastre(centros, 0, 80), 1, 'pasó la segunda');
  assert.equal(destinoArrastre(centros, 0, 400), 3, 'al final');
  assert.equal(destinoArrastre(centros, 3, 70), 1, 'hacia arriba, pasó dos');
  assert.equal(destinoArrastre(centros, 3, -50), 0, 'al principio');
});
