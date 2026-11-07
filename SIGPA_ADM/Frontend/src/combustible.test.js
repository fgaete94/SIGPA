import test from 'node:test';
import assert from 'node:assert/strict';
import { etiquetaCombustible, leerMonto, resumenCombustible, textoCarga } from './combustible.js';

test('leerMonto acepta los formatos habituales y rechaza lo que no es un monto', () => {
  assert.equal(leerMonto('20000'), 20000);
  assert.equal(leerMonto('20.000'), 20000);
  assert.equal(leerMonto(' $20.000 '), 20000);
  for (const malo of ['', '0', '-5', 'veinte', '20,5', null]) assert.equal(leerMonto(malo), null);
});

test('resumenCombustible suma las cargas y distingue "no cargó" de "sin informar"', () => {
  assert.deepEqual(resumenCombustible([]), { cargas: [], total: 0, noCargo: false });
  assert.equal(resumenCombustible([{ cargo: false }]).noCargo, true);
  const r = resumenCombustible([{ cargo: true, monto: 20000 }, { cargo: true, monto: 5000 }, { cargo: false }]);
  assert.equal(r.total, 25000);
  assert.equal(r.noCargo, false);
});

test('textoCarga dice el monto, cómo pagó y la hora de Chile', () => {
  assert.match(textoCarga({ monto: 20000, medio_pago: 'tarjeta', creado_en: '2026-11-06T17:32:00' }), /^\$\s?20\.000 · Tarjeta · 14:32$/);
});

test('el botón muestra el estado del día', () => {
  assert.equal(etiquetaCombustible([]), 'Combustible');
  assert.equal(etiquetaCombustible([{ cargo: false }]), 'Combustible: no cargó');
  assert.match(etiquetaCombustible([{ cargo: true, monto: 20000 }]), /^Combustible ✓ \$\s?20\.000$/);
});
