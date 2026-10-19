import React, { useEffect, useState } from 'react';
import { api } from './api';
import { DIAS_REPARTO } from './clientes';
import { ETIQUETA_ESTADO } from './estados';
import { fechaCorta } from './fechas';
import { resumenCliente, textoTipo } from './fichaCliente';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);
const nombre = c => [c.nombre, c.apellido_paterno, c.apellido_materno].filter(Boolean).join(' ');
const dia = valor => DIAS_REPARTO.find(([v]) => v === valor)?.[1] || valor;

// Historia #102: ficha completa del cliente (GET /clientes/{id}) para conocer su situación antes de
// gestionar sus pedidos: datos, resumen de compras e historial completo con acceso al detalle de cada pedido.
export default function FichaDelCliente({ clienteId, onClose, onVerPedido, onEditar }) {
  const [cliente, setCliente] = useState(null);
  const [sectores, setSectores] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    api(`/clientes/${clienteId}`, { signal: controller.signal }).then(setCliente).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    // El nombre del sector es un dato de apoyo: si /sectores falla, la ficha se muestra igual.
    api('/sectores', { signal: controller.signal }).then(s => { if (Array.isArray(s)) setSectores(s); }).catch(() => {});
    return () => controller.abort();
  }, [clienteId]);

  const r = cliente && resumenCliente(cliente.pedidos);
  const sector = cliente && sectores.find(s => s.id === cliente.sector_id)?.nombre;

  return <div className="modal" role="dialog" aria-modal="true" aria-label="Ficha del cliente"><section className="ficha">
    <button autoFocus onClick={onClose}>Cerrar ficha</button>
    <span className="eyebrow">Ficha del cliente</span>
    {!cliente && !error && <p role="status">Cargando la ficha…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {cliente && <>
      <h2>{nombre(cliente)}</h2>
      <p className="etiquetas-ficha">
        <span className={`badge ${cliente.activo ? 'entregado' : 'cancelado'}`}>{cliente.activo ? 'Activo' : 'Inactivo'}</span>
        <span className="badge">{textoTipo(cliente.tipo_cliente_nombre)}</span>
        {cliente.opt_out_whatsapp && <span className="badge revisar">No recibe recordatorios</span>}
      </p>
      <dl className="datos-ficha">
        <div><dt>Teléfono</dt><dd>{cliente.telefono || '—'}</dd></div>
        <div><dt>Dirección</dt><dd>{cliente.direccion || 'Sin dirección'}</dd></div>
        <div><dt>Comuna</dt><dd>{cliente.comuna_nombre || 'Sin comuna'}{sector && sector !== cliente.comuna_nombre ? ` · ${sector}` : ''}</dd></div>
        <div><dt>Día de reparto</dt><dd>{cliente.dia_reparto ? dia(cliente.dia_reparto) : 'Sin asignar'}</dd></div>
      </dl>

      <div className="kpis kpis-ficha">
        <article><span>Pedidos</span><strong>{r.cantidad}</strong></article>
        <article><span>Entregados</span><strong>{r.entregados}</strong></article>
        <article><span>Comprado</span><strong>{dinero(r.comprado)}</strong></article>
        <article><span>Promedio por entrega</span><strong>{r.promedio == null ? '—' : dinero(r.promedio)}</strong></article>
      </div>
      <p className="muted">
        {r.ultimo ? `Último pedido: #${r.ultimo.id} del ${fechaCorta(r.ultimo.creado_en)} (${ETIQUETA_ESTADO[r.ultimo.estado]?.toLowerCase()}).` : 'Todavía no tiene pedidos.'}
        {r.enCurso > 0 && ` ${r.enCurso === 1 ? 'Tiene 1 pedido en curso' : `Tiene ${r.enCurso} pedidos en curso`}.`}
        {r.cancelados > 0 && ` ${r.cancelados === 1 ? '1 cancelado' : `${r.cancelados} cancelados`}.`}
        {' '}"Comprado" suma solo lo entregado.
      </p>

      <h3>Historial de pedidos</h3>
      {r.cantidad > 0 ? <div className="table"><table className="tabla-movil">
        <thead><tr><th>Pedido</th><th>Fecha</th><th>Estado</th><th>Total</th><th>Detalle</th></tr></thead>
        <tbody>{r.pedidos.map(p => <tr key={p.id}>
          <td data-label="Pedido">#{p.id}</td>
          <td data-label="Fecha">{fechaCorta(p.creado_en)}</td>
          <td data-label="Estado"><span className={`badge ${p.estado}`}>{ETIQUETA_ESTADO[p.estado] || p.estado}</span></td>
          <td data-label="Total">{dinero(Number(p.total))}</td>
          <td className="celda-acciones"><button aria-label={`Ver pedido ${p.id}`} onClick={() => onVerPedido(p.id)}>Ver detalle</button></td>
        </tr>)}</tbody>
      </table></div> : <p>Sin pedidos registrados.</p>}

      <div className="actions"><button onClick={() => onEditar(cliente)}>Editar cliente</button></div>
    </>}
  </section></div>;
}
