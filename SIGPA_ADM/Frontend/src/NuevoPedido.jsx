import React, { useMemo, useState } from 'react';
import { api } from './api';
import { DIAS_REPARTO } from './clientes';
import { datosPedido, lineaVacia, pedidoForm, totalEstimado } from './pedidos';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const nombreCompleto = c => [c.nombre, c.apellido_paterno, c.apellido_materno].filter(Boolean).join(' ');
const sinTildes = texto => texto.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const MAX_OPCIONES = 50;

// Pedido ingresado a mano (por teléfono o en persona). Queda "pendiente", igual que los del bot,
// y desde ahí entra a la planificación de rutas.
export default function NuevoPedido({ clientes, productos, onClose, onSaved }) {
  const [form, setForm] = useState(pedidoForm);
  const [busqueda, setBusqueda] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const candidatos = useMemo(() => {
    const q = sinTildes(busqueda.trim());
    const digitos = busqueda.replace(/\D/g, '');
    return clientes.filter(c => !q || sinTildes(`${nombreCompleto(c)} ${c.direccion || ''}`).includes(q) || (digitos.length >= 3 && (c.telefono || '').replace(/\D/g, '').includes(digitos)))
      .sort((a, b) => (b.activo - a.activo) || nombreCompleto(a).localeCompare(nombreCompleto(b), 'es'));
  }, [clientes, busqueda]);
  const elegido = clientes.find(c => String(c.id) === form.cliente_id);
  // El cliente elegido se mantiene en la lista aunque no calce con la búsqueda.
  const opciones = [...(elegido && !candidatos.slice(0, MAX_OPCIONES).includes(elegido) ? [elegido] : []), ...candidatos.slice(0, MAX_OPCIONES)];
  const total = totalEstimado(form.lineas, productos);
  const setLinea = (i, cambio) => setForm(f => ({ ...f, lineas: f.lineas.map((l, j) => j === i ? { ...l, ...cambio } : l) }));
  const dia = elegido && DIAS_REPARTO.find(([valor]) => valor === elegido.dia_reparto)?.[1];

  async function submit(e) {
    e.preventDefault();
    const { datos, error } = datosPedido(form, productos);
    if (error) return setError(error);
    setBusy(true); setError('');
    try { onSaved(await api('/pedidos', { method: 'POST', body: JSON.stringify(datos) })); }
    catch (e) { setError(e.message); setBusy(false); }
  }

  return <div className="modal" role="dialog" aria-modal="true" aria-label="Nuevo pedido"><section><form onSubmit={submit} noValidate>
    <span className="eyebrow">Ingreso manual</span><h2>Nuevo pedido</h2>
    <p>Para pedidos recibidos por teléfono o en persona. Queda pendiente y entra a la planificación de rutas.</p>
    <label>Buscar cliente<input type="search" autoFocus value={busqueda} onChange={e => setBusqueda(e.target.value)} placeholder="Nombre, dirección o teléfono"/></label>
    <label>Cliente<select value={form.cliente_id} onChange={e => setForm(f => ({ ...f, cliente_id: e.target.value }))}>
      <option value="">{candidatos.length ? `Elige un cliente (${candidatos.length} ${candidatos.length === 1 ? 'resultado' : 'resultados'})` : 'Ningún cliente coincide'}</option>
      {opciones.map(c => <option key={c.id} value={c.id}>{nombreCompleto(c)} · {c.telefono || 'sin teléfono'}{c.activo ? '' : ' (inactivo)'}</option>)}
    </select></label>
    {candidatos.length > MAX_OPCIONES && <p className="muted">Se muestran los primeros {MAX_OPCIONES}: escribe más para acotar la búsqueda.</p>}
    {elegido && !elegido.activo && <p className="notice">Este cliente está inactivo. Puedes ingresar el pedido igual, pero revisa si corresponde reactivarlo.</p>}
    {elegido && <p className="muted">{elegido.direccion || 'El cliente no tiene dirección registrada.'}{dia ? ` · Reparto: ${dia}` : ''}</p>}
    <label>Dirección de despacho<input value={form.direccion_despacho} onChange={e => setForm(f => ({ ...f, direccion_despacho: e.target.value }))} placeholder={elegido?.direccion ? 'Vacía = la del cliente' : 'Calle y número'}/></label>
    {elegido && !elegido.direccion && !form.direccion_despacho.trim() && <p className="notice">Sin dirección, este pedido no se podrá ubicar en la ruta.</p>}
    <fieldset className="lineas">
      <legend>Productos</legend>
      {form.lineas.map((l, i) => <div className="linea" key={i}>
        <label>Producto<select value={l.producto_id} onChange={e => setLinea(i, { producto_id: e.target.value })}>
          <option value="">Elige un producto</option>
          {productos.map(p => <option key={p.id} value={p.id} disabled={p.id !== Number(l.producto_id) && form.lineas.some(x => x.producto_id === String(p.id))}>{p.nombre} · {dinero(Number(p.precio_unitario))}</option>)}
        </select></label>
        <label>Cantidad<input inputMode="numeric" value={l.cantidad} onChange={e => setLinea(i, { cantidad: e.target.value })}/></label>
        <button type="button" aria-label={`Quitar línea ${i + 1}`} disabled={form.lineas.length === 1} onClick={() => setForm(f => ({ ...f, lineas: f.lineas.filter((_, j) => j !== i) }))}>Quitar</button>
      </div>)}
      <button type="button" disabled={form.lineas.length >= productos.length} onClick={() => setForm(f => ({ ...f, lineas: [...f.lineas, lineaVacia()] }))}>Agregar producto</button>
    </fieldset>
    <p className="total">Total estimado: <strong>{dinero(total)}</strong><small>El backend lo calcula con el precio vigente de cada producto.</small></p>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" onClick={onClose} disabled={busy}>Cancelar</button><button className="primary" disabled={busy}>{busy ? 'Guardando…' : 'Crear pedido'}</button></div>
  </form></section></div>;
}
