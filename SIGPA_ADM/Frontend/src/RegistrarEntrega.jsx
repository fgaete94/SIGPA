import React, { useEffect, useState } from 'react';
import { api } from './api';
import { datosEntrega, errorEntrega, formEntrega, puedeEntregarse, resumenEntrega } from './entregas';
import { ETIQUETA_ESTADO } from './estados';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// Historia #99: la ejecutiva anota lo que el conductor dejó en cada parada (la fila "Entrega" de la
// hoja de ruta). El pedido queda "entregado" con el total de lo entregado; eso es lo que suma en Ventas.
export default function RegistrarEntrega({ pedidoId, onClose, onRegistrado }) {
  const [pedido, setPedido] = useState(null);
  const [form, setForm] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api(`/pedidos/${pedidoId}`, { signal: controller.signal })
      .then(p => { setPedido(p); setForm(formEntrega(p.lineas)); })
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [pedidoId]);

  const resumen = resumenEntrega(form);
  const setEntregada = (id, entregada) => setForm(f => f.map(l => (l.id === id ? { ...l, entregada } : l)));

  async function submit(e) {
    e.preventDefault();
    const problema = errorEntrega(resumen);
    if (problema) return setError(problema);
    setBusy(true); setError('');
    try { onRegistrado(await api(`/pedidos/${pedidoId}/entrega`, { method: 'POST', body: JSON.stringify(datosEntrega(form)) })); }
    catch (e) { setError(e.message); setBusy(false); }
  }

  return <div className="modal" role="dialog" aria-modal="true" aria-label={`Registrar entrega del pedido ${pedidoId}`}><section><form onSubmit={submit} noValidate>
    <span className="eyebrow">Entrega</span><h2>Pedido #{pedidoId}</h2>
    {!pedido && !error && <p role="status">Cargando el pedido…</p>}
    {pedido && <>
      <p>{pedido.cliente_nombre || `Cliente ${pedido.cliente_id}`} · <span className={`badge ${pedido.estado}`}>{ETIQUETA_ESTADO[pedido.estado]}</span></p>
      {!puedeEntregarse(pedido.estado) ? <p className="notice">Solo se registra la entrega de pedidos confirmados o en despacho. Este pedido está {ETIQUETA_ESTADO[pedido.estado]?.toLowerCase()}.</p> : <>
        <p className="muted">Anota lo que realmente se dejó. Parte con lo pedido: cambia solo lo que no se entregó completo.</p>
        <fieldset className="lineas entrega">
          <legend>Productos</legend>
          {form.map(l => <div className="linea" key={l.id}>
            <div><strong>{l.producto}</strong><small>Pidió {l.solicitada} · {dinero(l.precio)} c/u</small></div>
            <label>Entregó<input inputMode="numeric" value={l.entregada} aria-label={`Cantidad entregada de ${l.producto}`} onChange={e => setEntregada(l.id, e.target.value)} onFocus={e => e.target.select()}/></label>
          </div>)}
          {form.some(l => l.entregada !== String(l.solicitada)) && <button type="button" onClick={() => setForm(formEntrega(pedido.lineas.map(l => ({ ...l, cantidad_entregada: null }))))}>Volver a lo pedido</button>}
        </fieldset>
        <p className="total">Total entregado: <strong>{dinero(resumen.total)}</strong>{resumen.total !== resumen.totalPedido && <small>El pedido era por {dinero(resumen.totalPedido)}; el total queda con lo entregado y eso es lo que suma en Ventas.</small>}</p>
        {resumen.parcial && !errorEntrega(resumen) && <p className="notice">Entrega parcial: lo que faltó no queda pendiente. Si el cliente lo sigue necesitando, crea un pedido nuevo.</p>}
        {resumen.demas.length > 0 && <p className="notice">Se entregó más de lo pedido en {resumen.demas.join(', ')}.</p>}
      </>}
    </>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions">
      <button type="button" onClick={onClose} disabled={busy}>{pedido && puedeEntregarse(pedido.estado) ? 'Cancelar' : 'Cerrar'}</button>
      {pedido && puedeEntregarse(pedido.estado) && <button className="primary" disabled={busy}>{busy ? 'Registrando…' : 'Registrar entrega'}</button>}
    </div>
  </form></section></div>;
}
