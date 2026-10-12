import React, { useEffect, useState } from 'react';
import { api } from './api';
import { DIAS_REPARTO, cambiosCliente, clienteForm, nuevoCliente } from './clientes';

const nombreCompleto = c => [c.nombre, c.apellido_paterno, c.apellido_materno].filter(Boolean).join(' ');

// Crear (cliente = null) o editar un cliente. El bot de WhatsApp solo atiende a clientes ya registrados,
// así que este formulario es la forma de dar de alta a uno nuevo.
export default function FormCliente({ cliente, clientes, onClose, onSaved }) {
  const nuevo = !cliente;
  const [form, setForm] = useState(() => clienteForm(cliente || {}));
  const [tipos, setTipos] = useState([]);
  const [sectores, setSectores] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const cargar = (ruta, guardar, que) => api(ruta, { signal: controller.signal }).then(guardar).catch(e => { if (e.name !== 'AbortError') setError(`No se pudieron cargar ${que}: ${e.message}`); });
    cargar('/tipos-cliente', setTipos, 'los tipos de cliente');
    cargar('/sectores', setSectores, 'los sectores');
    return () => controller.abort();
  }, []);
  const resultado = nuevo ? nuevoCliente(form, clientes) : cambiosCliente(cliente, form, clientes);
  const set = (key, value) => setForm(f => ({ ...f, [key]: value }));
  const field = (key, label, props) => <label>{label}<input value={form[key]} onChange={e => set(key, e.target.value)} {...props}/></label>;
  const check = (key, label) => <label className="check"><input type="checkbox" checked={form[key]} onChange={e => set(key, e.target.checked)}/>{label}</label>;
  // Si el valor guardado ya no está en la lista (o la lista no cargó), se conserva como opción.
  const actual = (lista, id, texto) => id != null && !lista.some(x => x.id === id) && <option value={id}>{texto}</option>;

  async function submit(e) {
    e.preventDefault();
    if (resultado.error) return setError(resultado.error);
    if (!nuevo && !Object.keys(resultado.cambios).length) return onClose();
    setBusy(true); setError('');
    try {
      const guardado = nuevo
        ? await api('/clientes', { method: 'POST', body: JSON.stringify(resultado.datos) })
        : await api(`/clientes/${cliente.id}`, { method: 'PATCH', body: JSON.stringify(resultado.cambios) });
      onSaved(guardado);
    } catch (e) { setError(e.message); setBusy(false); }
  }

  return <div className="modal" role="dialog" aria-modal="true" aria-label={nuevo ? 'Nuevo cliente' : 'Editar cliente'}><section><form onSubmit={submit} noValidate>
    <span className="eyebrow">{nuevo ? 'Alta manual' : `Cliente #${cliente.id}`}</span><h2>{nuevo ? 'Nuevo cliente' : 'Editar cliente'}</h2>
    <p>{nuevo ? 'El bot de WhatsApp solo atiende a clientes registrados: desde que lo guardes, podrá pedir con este número.' : nombreCompleto(cliente)}</p>
    <div className="campos">
      {field('nombre', 'Nombre', { required: true, autoFocus: true })}{field('apellido_paterno', 'Apellido paterno')}{field('apellido_materno', 'Apellido materno')}
      {field('telefono', 'Teléfono (celular)', { required: true, inputMode: 'tel', placeholder: '9 1234 5678' })}
    </div>
    {resultado.aviso && <p className="notice">{resultado.aviso}</p>}
    {field('direccion', 'Dirección', { placeholder: 'Calle y número, depto o block' })}
    <div className="campos">
      <label>Sector<select value={form.sector_id} onChange={e => set('sector_id', e.target.value)}><option value="">Sin asignar</option>{actual(sectores, cliente?.sector_id, `Sector ${cliente?.sector_id}`)}{sectores.map(s => <option key={s.id} value={s.id}>{s.comuna_nombre} · {s.nombre}</option>)}</select></label>
      <label>Día de reparto<select value={form.dia_reparto} onChange={e => set('dia_reparto', e.target.value)}><option value="">Sin asignar</option>{DIAS_REPARTO.map(([valor, texto]) => <option key={valor} value={valor}>{texto}</option>)}</select></label>
    </div>
    <label>Tipo de cliente<select value={form.tipo_cliente_id} onChange={e => set('tipo_cliente_id', e.target.value)}><option value="">Sin asignar</option>{actual(tipos, cliente?.tipo_cliente_id, cliente?.tipo_cliente_nombre || `Tipo ${cliente?.tipo_cliente_id}`)}{tipos.map(t => <option key={t.id} value={t.id}>{t.nombre}</option>)}</select></label>
    {check('activo', 'Cliente activo')}{check('opt_out_whatsapp', 'No desea recibir recordatorios por WhatsApp')}
    {!form.dia_reparto && form.activo && !form.opt_out_whatsapp && <p className="muted">Sin día de reparto no recibe el recordatorio del día anterior.</p>}
    {!nuevo && form.direccion.trim() !== (cliente.direccion || '') && <p className="notice">Cambiar la dirección no actualiza las coordenadas guardadas del cliente.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" onClick={onClose} disabled={busy}>Cancelar</button><button className="primary" disabled={busy}>{busy ? 'Guardando…' : nuevo ? 'Crear cliente' : 'Guardar cambios'}</button></div>
  </form></section></div>;
}
