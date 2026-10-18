import React, { useState } from 'react';
import { DIAS_REPARTO } from './clientes';
import { SIN_DIA, VISTAS, diaHoy, gruposDespacho, opcionesDespacho, pedidosVista } from './agrupacionDespacho';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);
const nombreDia = d => (d === SIN_DIA ? 'Sin día de reparto' : DIAS_REPARTO.find(([v]) => v === d)?.[1] || d);
const pedidosTexto = n => (n === 1 ? '1 pedido' : `${n} pedidos`);

// Historia #67: listado para organizar el despacho, por día de reparto del cliente y comuna.
// tabla(filas) dibuja cada grupo con la misma tabla de Pedidos (acciones de despacho, entrega y detalle).
export default function Despacho({ pedidos, tabla }) {
  const hoy = diaHoy();
  const [vista, setVista] = useState('por_entregar');
  const [dia, setDia] = useState('');
  const [comuna, setComuna] = useState('');
  const base = pedidosVista(pedidos, vista);
  const { dias, comunas } = opcionesDespacho(base);
  const grupos = gruposDespacho(base, { dia, comuna });
  const cantidad = grupos.reduce((s, g) => s + g.cantidad, 0);
  const total = grupos.reduce((s, g) => s + g.comunas.reduce((t, c) => t + c.total, 0), 0);

  return <section>
    <p className="muted">Pedidos confirmados, en despacho y entregados, agrupados por el día de reparto del cliente y su comuna.</p>
    <div className="filters filtros-despacho">
      <select aria-label="Estado de despacho" value={vista} onChange={e => setVista(e.target.value)}>{VISTAS.map(([v, texto]) => <option key={v} value={v}>{texto}</option>)}</select>
      <select aria-label="Día de reparto" value={dia} onChange={e => setDia(e.target.value)}>
        <option value="">Todos los días ({base.length})</option>
        {dias.map(([d, n]) => <option key={d} value={d}>{nombreDia(d)}{d === hoy ? ' (hoy)' : ''} ({n})</option>)}
        {dia && !dias.some(([d]) => d === dia) && <option value={dia}>{nombreDia(dia)} (0)</option>}
      </select>
      <select aria-label="Comuna" value={comuna} onChange={e => setComuna(e.target.value)}>
        <option value="">Todas las comunas ({base.length})</option>
        {comunas.map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
        {comuna && !comunas.some(([c]) => c === comuna) && <option value={comuna}>{comuna} (0)</option>}
      </select>
    </div>
    {cantidad > 0 && <p role="status" className="resumen-despacho">{pedidosTexto(cantidad)} · {dinero(total)}</p>}
    {grupos.map(g => <div className="grupo-dia" key={g.dia}>
      <h3>{nombreDia(g.dia)}{g.dia === hoy && <span className="badge hoy">Hoy</span>}<small>{pedidosTexto(g.cantidad)}</small></h3>
      {g.dia === SIN_DIA && <p className="notice">Estos clientes no tienen día de reparto: asígnalo en Clientes → Editar para que entren al ciclo semanal.</p>}
      {g.comunas.map(c => <div className="grupo-comuna" key={c.comuna}>
        <h4>{c.comuna}<small>{pedidosTexto(c.pedidos.length)} · {dinero(c.total)}</small></h4>
        {tabla(c.pedidos)}
      </div>)}
    </div>)}
    {!grupos.length && <p>No hay pedidos {VISTAS.find(([v]) => v === vista)[1].toLowerCase()} con estos filtros.</p>}
  </section>;
}
