import React, { useState } from 'react';
import { api } from './api';
import { ETIQUETA_ESTADO } from './estados';
import { datosEdicion, formEdicion, lineaVacia, totalEdicion } from './pedidos';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// Historia #68: corregir un pedido ya ingresado (error del cliente o del bot): productos, cantidades y
// dirección. El cliente no se cambia: para otro cliente se crea un pedido nuevo. Queda en la auditoría.
export default function EditarPedido({ pedido, cliente, productos, onClose, onSaved }) {
  const [form, setForm] = useState(() => formEdicion(pedido));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const total = totalEdicion(form.lineas, productos, pedido);
  const setLinea = (i, cambio) => setForm(f => ({ ...f, lineas: f.lineas.map((l, j) => (j === i ? { ...l, ...cambio } : l)) }));
  const direccionNueva = form.direccion_despacho.trim();
  const direccionCambia = direccionNueva.toLowerCase() !== (pedido.direccion_despacho || '').trim().toLowerCase();

  async function submit(e) {
    e.preventDefault();
    const { datos, error } = datosEdicion(form, pedido, productos);
    if (error) return setError(error);
    setBusy(true); setError('');
    try { onSaved(await api(`/pedidos/${pedido.id}`, { method: 'PATCH', body: JSON.stringify(datos) })); }
    catch (e) { setError(e.message); setBusy(false); }
  }

  return <div className="modal" role="dialog" aria-modal="true" aria-label={`Editar pedido ${pedido.id}`}><section><form onSubmit={submit} noValidate>
    <span className="eyebrow">Corrección</span><h2>Editar pedido #{pedido.id}</h2>
    <p>{pedido.cliente_nombre || `Cliente ${pedido.cliente_id}`} · <span className={`badge ${pedido.estado}`}>{ETIQUETA_ESTADO[pedido.estado]}</span></p>
    {pedido.estado === 'en_despacho' && <p className="notice">Este pedido ya salió a reparto: avisa al conductor del cambio.</p>}
    <label>Dirección de despacho<input value={form.direccion_despacho} onChange={e => setForm(f => ({ ...f, direccion_despacho: e.target.value }))} placeholder={cliente?.direccion ? `Vacía = la del cliente (${cliente.direccion})` : 'Calle y número'}/></label>
    {direccionCambia && <p className="notice">
      {direccionNueva ? 'Con la nueva dirección habrá que volver a ubicar el pedido en el mapa.' : 'Volverá a la dirección registrada del cliente y usará su ubicación.'}
      {pedido.estado === 'confirmado' && pedido.orden_entrega != null && ' Además sale de la ruta guardada y vuelve a "Preparar ruta" para planificarlo de nuevo.'}
    </p>}
    <fieldset className="lineas">
      <legend>Productos</legend>
      {form.lineas.map((l, i) => <div className="linea" key={i}>
        <label>Producto<select value={l.producto_id} onChange={e => setLinea(i, { producto_id: e.target.value })}>
          <option value="">Elige un producto</option>
          {productos.map(p => <option key={p.id} value={p.id} disabled={String(p.id) !== l.producto_id && form.lineas.some(x => x.producto_id === String(p.id))}>{p.nombre} · {dinero(Number(p.precio_unitario))}</option>)}
        </select></label>
        <label>Cantidad<input inputMode="numeric" value={l.cantidad} onChange={e => setLinea(i, { cantidad: e.target.value })}/></label>
        <button type="button" aria-label={`Quitar línea ${i + 1}`} disabled={form.lineas.length === 1} onClick={() => setForm(f => ({ ...f, lineas: f.lineas.filter((_, j) => j !== i) }))}>Quitar</button>
      </div>)}
      <button type="button" disabled={form.lineas.length >= productos.length} onClick={() => setForm(f => ({ ...f, lineas: [...f.lineas, lineaVacia()] }))}>Agregar producto</button>
    </fieldset>
    <p className="total">Total: <strong>{dinero(total)}</strong><small>{pedido.lineas.length ? 'Los productos que ya estaban mantienen el precio con que se pidieron; los nuevos usan el precio actual.' : ''} Antes: {dinero(Number(pedido.total))}.</small></p>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" onClick={onClose} disabled={busy}>Cancelar</button><button className="primary" disabled={busy}>{busy ? 'Guardando…' : 'Guardar cambios'}</button></div>
  </form></section></div>;
}
