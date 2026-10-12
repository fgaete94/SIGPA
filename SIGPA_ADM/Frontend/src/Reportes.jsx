import React, { useMemo, useState } from 'react';
import { hoyChile, horaCorta } from './fechas';
import { PERIODOS, pedidosPorComuna, rangoPeriodo } from './indicadores';

const porcentaje = new Intl.NumberFormat('es-CL', { style: 'percent', maximumFractionDigits: 1 });

// Pestaña Reportes. Por ahora, historia #73 (pedidos por comuna); aquí irán #71 y #72.
export default function Reportes({ pedidos, actualizadoEn }) {
  const [periodo, setPeriodo] = useState('30');
  const [rango, setRango] = useState(() => rangoPeriodo('30', hoyChile()));
  const invalido = rango.desde && rango.hasta && rango.desde > rango.hasta;
  const { total, filas } = useMemo(() => invalido ? { total: 0, filas: [] } : pedidosPorComuna(pedidos, rango), [pedidos, rango, invalido]);
  const elegirPeriodo = valor => { setPeriodo(valor); if (valor !== 'otro') setRango(rangoPeriodo(valor, hoyChile())); };
  const cambiarFecha = (campo, valor) => { setPeriodo('otro'); setRango(r => ({ ...r, [campo]: valor })); };

  return <section>
    <div className="title"><h2>Pedidos por comuna</h2>{actualizadoEn && <span className="muted">Datos de las {horaCorta(actualizadoEn)} · se actualizan cada minuto</span>}</div>
    <p>Dónde se concentra la demanda, según la comuna del cliente. No incluye pedidos cancelados.</p>
    <div className="filters periodo">
      <label>Período<select value={periodo} onChange={e => elegirPeriodo(e.target.value)}>{PERIODOS.map(([valor, texto]) => <option key={valor} value={valor}>{texto}</option>)}</select></label>
      <label>Desde<input type="date" value={rango.desde} max={rango.hasta || undefined} onChange={e => cambiarFecha('desde', e.target.value)}/></label>
      <label>Hasta<input type="date" value={rango.hasta} min={rango.desde || undefined} onChange={e => cambiarFecha('hasta', e.target.value)}/></label>
    </div>
    {invalido && <p role="alert" className="error">La fecha "desde" es posterior a la fecha "hasta".</p>}
    {!invalido && <div className="table"><table className="por-comuna">
      <caption className="muted">{total === 1 ? '1 pedido' : `${total} pedidos`} en el período</caption>
      <thead><tr><th>Comuna</th><th>Pedidos</th><th>Clientes</th><th>Participación</th></tr></thead>
      <tbody>{filas.map(f => <tr key={f.comuna}>
        <td>{f.comuna}</td><td>{f.pedidos}</td><td>{f.clientes}</td>
        <td><span className="barra" aria-hidden="true"><span style={{ width: `${f.porcentaje * 100}%` }}/></span>{porcentaje.format(f.porcentaje)}</td>
      </tr>)}</tbody>
    </table>{!filas.length && <p>No hay pedidos en el período elegido.</p>}</div>}
  </section>;
}
