import test from 'node:test';
import assert from 'node:assert/strict';
import { rutaSugerencia, textoSugerencia } from './sugerenciaDia.js';

test('ruta: el sector tiene prioridad sobre la comuna', () => {
  assert.equal(rutaSugerencia({ comuna_id: '2', sector_id: '15' }), '/clientes/sugerencia-dia?sector_id=15');
  assert.equal(rutaSugerencia({ comuna_id: '4', sector_id: '' }), '/clientes/sugerencia-dia?comuna_id=4');
});
test('ruta: sin comuna ni sector no se pide sugerencia', () => {
  assert.equal(rutaSugerencia({ comuna_id: '', sector_id: '' }), null);
});
test('ruta: al editar se excluye al propio cliente', () => {
  assert.equal(rutaSugerencia({ comuna_id: '2', sector_id: '15' }, 7), '/clientes/sugerencia-dia?sector_id=15&excluir_cliente_id=7');
});
test('texto: con día sugerido, por sector y por comuna', () => {
  const sector = { base: 'sector', base_nombre: 'Gómez Carreño', dia_reparto: 'Viernes', clientes_con_ese_dia: 27, clientes_con_dia: 31 };
  assert.equal(textoSugerencia(sector), 'Sugerido: Viernes (27 de 31 clientes del sector Gómez Carreño).');
  const comuna = { base: 'comuna', base_nombre: 'Concón', dia_reparto: 'Jueves', clientes_con_ese_dia: 1, clientes_con_dia: 1 };
  assert.equal(textoSugerencia(comuna), 'Sugerido: Jueves (1 de 1 cliente de Concón).');
});
test('texto: el día se muestra con tilde', () => {
  const s = { base: 'sector', base_nombre: 'Santa Inés', dia_reparto: 'Miercoles', clientes_con_ese_dia: 6, clientes_con_dia: 6 };
  assert.match(textoSugerencia(s), /^Sugerido: Miércoles/);
});
test('texto: sin sugerencia por falta de datos o por días distintos', () => {
  assert.equal(textoSugerencia({ base: 'sector', base_nombre: 'Curauma', dia_reparto: null, clientes_con_ese_dia: 0, clientes_con_dia: 0 }), 'Sin sugerencia: ningún cliente del sector Curauma tiene día asignado.');
  assert.equal(textoSugerencia({ base: 'comuna', base_nombre: 'Viña del Mar', dia_reparto: null, clientes_con_ese_dia: 0, clientes_con_dia: 592 }), 'Sin sugerencia: los clientes de Viña del Mar tienen días distintos.');
  assert.equal(textoSugerencia(null), null);
});