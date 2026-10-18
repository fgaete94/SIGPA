import test from 'node:test';
import assert from 'node:assert/strict';
import { cambiosCliente, clienteForm, elegirComuna, normalizarTelefono, nuevoCliente, sectoresDeComuna } from './clientes.js';
const cliente = { id: 7, nombre: 'Ana', apellido_paterno: 'Rojas', apellido_materno: null, telefono: '56911111111', direccion: 'Av. Libertad 100', tipo_cliente_id: 1, activo: true, opt_out_whatsapp: false };
test('sin cambios no envía campos', () => {
  assert.deepEqual(cambiosCliente(cliente, clienteForm(cliente)), { cambios: {} });
});
test('envía solo los campos modificados y recorta espacios', () => {
  const form = { ...clienteForm(cliente), direccion: '  Calle 5 #20 ', tipo_cliente_id: '2', activo: false };
  assert.deepEqual(cambiosCliente(cliente, form), { cambios: { direccion: 'Calle 5 #20', tipo_cliente_id: 2, activo: false } });
});
test('un campo opcional vaciado se guarda como null', () => {
  const form = { ...clienteForm(cliente), apellido_paterno: '   ', tipo_cliente_id: '' };
  assert.deepEqual(cambiosCliente(cliente, form), { cambios: { apellido_paterno: null, tipo_cliente_id: null } });
});
test('nombre y teléfono son obligatorios', () => {
  assert.equal(cambiosCliente(cliente, { ...clienteForm(cliente), nombre: ' ' }).error, 'El nombre es obligatorio.');
  assert.equal(cambiosCliente(cliente, { ...clienteForm(cliente), telefono: '' }).error, 'El teléfono es obligatorio.');
});
test('teléfono: se normaliza como el backend y el mismo número con otro formato no es cambio', () => {
  assert.equal(normalizarTelefono('+56 9 1234 5678'), '56912345678');
  assert.equal(normalizarTelefono('9-1234-5678'), '56912345678');
  assert.equal(normalizarTelefono('56912345678'), '56912345678');
  assert.deepEqual(cambiosCliente(cliente, { ...clienteForm(cliente), telefono: '+56 9 1111 1111' }), { cambios: {} });
  assert.deepEqual(cambiosCliente(cliente, { ...clienteForm(cliente), telefono: '9 2222 3333' }), { cambios: { telefono: '56922223333' } });
  assert.match(cambiosCliente(cliente, { ...clienteForm(cliente), telefono: '1234' }).error, /9 dígitos/);
  // Un teléfono antiguo con formato raro no impide editar otros campos.
  assert.deepEqual(cambiosCliente({ ...cliente, telefono: '1234' }, { ...clienteForm({ ...cliente, telefono: '1234' }), activo: false }), { cambios: { activo: false } });
});
test('teléfono repetido con otro formato se rechaza; fijo se acepta con aviso', () => {
  const otros = [cliente, { id: 8, nombre: 'Luis', telefono: '+56 9 2222 3333' }];
  assert.match(cambiosCliente(cliente, { ...clienteForm(cliente), telefono: '922223333' }, otros).error, /Luis \(#8\)/);
  const fijo = cambiosCliente(cliente, { ...clienteForm(cliente), telefono: '32 212 3456' }, otros);
  assert.deepEqual(fijo.cambios, { telefono: '56322123456' });
  assert.match(fijo.aviso, /no es un celular/);
});
test('cliente nuevo: activo por defecto, con día de reparto, comuna, sector y teléfono normalizado', () => {
  const form = { ...clienteForm(), nombre: ' Marta ', telefono: '9 4444 5555', direccion: 'Freire 455', dia_reparto: 'Miercoles', comuna_id: '1', sector_id: '3', tipo_cliente_id: '2' };
  assert.deepEqual(nuevoCliente(form, [cliente]), { datos: { nombre: 'Marta', apellido_paterno: null, apellido_materno: null, telefono: '56944445555', direccion: 'Freire 455', tipo_cliente_id: 2, comuna_id: 1, sector_id: 3, dia_reparto: 'Miercoles', activo: true, opt_out_whatsapp: false } });
  assert.match(nuevoCliente({ ...form, telefono: '+56911111111' }, [cliente]).error, /Ana Rojas \(#7\)/);
  assert.equal(nuevoCliente({ ...form, nombre: '' }).error, 'El nombre es obligatorio.');
});


// Historia #136: comuna obligatoria al crear y sector opcional dentro de la comuna.
const SECTORES = [{ id: 1, nombre: 'Reñaca', comuna_id: 1 }, { id: 2, nombre: 'Miraflores', comuna_id: 1 }, { id: 3, nombre: 'Centro', comuna_id: 2 }];
test('cliente nuevo: la comuna es obligatoria y el sector no', () => {
  const form = { ...clienteForm(), nombre: 'Marta', telefono: '9 4444 5555' };
  assert.equal(nuevoCliente(form).error, 'La comuna es obligatoria.');
  assert.deepEqual(nuevoCliente({ ...form, comuna_id: '2' }).datos.comuna_id, 2);
  assert.equal(nuevoCliente({ ...form, comuna_id: '2' }).datos.sector_id, null);
});
test('los sectores se filtran por la comuna elegida', () => {
  assert.deepEqual(sectoresDeComuna(SECTORES, '1').map(s => s.nombre), ['Reñaca', 'Miraflores']);
  assert.deepEqual(sectoresDeComuna(SECTORES, '').map(s => s.id), []);
});
test('al cambiar la comuna se quita el sector si no pertenece a ella', () => {
  const form = { ...clienteForm(), comuna_id: '1', sector_id: '2' };
  assert.deepEqual([elegirComuna(form, '2', SECTORES).comuna_id, elegirComuna(form, '2', SECTORES).sector_id], ['2', '']);
  assert.equal(elegirComuna(form, '1', SECTORES).sector_id, '2');
  assert.equal(elegirComuna(form, '', SECTORES).sector_id, '');
});
test('editar: cambiar de comuna envía también el sector vacío, y un cliente sin comuna se puede editar', () => {
  const conSector = { ...cliente, comuna_id: 1, sector_id: 2 };
  assert.deepEqual(cambiosCliente(conSector, elegirComuna(clienteForm(conSector), '2', SECTORES)), { cambios: { comuna_id: 2, sector_id: null } });
  assert.deepEqual(cambiosCliente(cliente, { ...clienteForm(cliente), activo: false }), { cambios: { activo: false } });
});