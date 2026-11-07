import React, { useEffect, useState } from 'react';
import { api } from './api';
import { resumenCombustible } from './combustible';
import { MAX_BARRAS_CON_VALOR, resumenPeriodo } from './datosPeriodo';
import { ETIQUETA_ESTADO } from './estados';
import { fechaCorta, hoyChile } from './fechas';
import { resumenEntregas, rutaEnCurso, textoResultado } from './reparto';
import { agruparPorViaje } from './viajes';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const pedidosTexto = n => (n === 1 ? '1 pedido' : `${n} pedidos`);
const porcentaje = (parte, total) => (total ? Math.round((parte / total) * 100) : 0);
const fechaDe = dia => fechaCorta(`${dia}T12:00:00Z`);

// Pestaña Resumen: la operación del día.
// Para consultar otros períodos está la pestaña Reportes.
export default function ResumenDia({ pedidos, clientes }) {
  const hoy = hoyChile();
  const delDia = resumenPeriodo(pedidos, { desde: hoy, hasta: hoy });
  // null: no se pudo consultar. Se pide de nuevo al recargar los datos.
  const [combustible, setCombustible] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    api(`/combustible?desde=${hoy}&hasta=${hoy}`, { signal: controller.signal }).then(setCombustible).catch(e => { if (e.name !== 'AbortError') setCombustible(null); });
    return () => controller.abort();
  }, [hoy, pedidos]);
  return <div className="resumen-periodo">
    <IndicesContables delDia={delDia} combustible={combustible} hoy={hoy}/>
    <EntregasDelDia pedidos={pedidos} clientes={clientes}/>
  </div>;
}

// Lo que se está entregando hoy: las paradas de la ruta con su avance. Solo se consulta; se gestiona en Rutas.
function EntregasDelDia({ pedidos, clientes }) {
  const { paradas } = rutaEnCurso(pedidos, clientes);
  const r = resumenEntregas(paradas);
  return <section className="entregas-dia">
    <div className="title grafico-titulo"><h2>Entregas del día</h2><span className="etiqueta-hoy">Hoy</span></div>
    {!paradas.length ? <p className="muted">Hoy no hay una ruta en curso.</p> : <>
      <ul className="resumen-entregas" aria-live="polite">
        <li><b>{r.total}</b> {r.total === 1 ? 'parada' : 'paradas'}</li>
        <li>✓ <b>{r.entregadas}</b> {r.entregadas === 1 ? 'entregada' : 'entregadas'} · {dinero(r.montoEntregado)}</li>
        <li>✗ <b>{r.noEntregadas}</b> {r.noEntregadas === 1 ? 'no entregada' : 'no entregadas'}</li>
        <li><b>{r.porEntregar}</b> por entregar · {dinero(r.montoPorEntregar)}</li>
      </ul>
      {agruparPorViaje(paradas).map(g => { const ruta = resumenEntregas(g.paradas); return <div className="ruta-resumen" key={g.viaje ?? 'sin-ruta'}>
        <h3 className="viaje-titulo">{g.viaje != null ? `Ruta ${g.viaje}` : 'Sin ruta'}<small>{ruta.total} {ruta.total === 1 ? 'parada' : 'paradas'} · ✓ {ruta.entregadas} · ✗ {ruta.noEntregadas} · {ruta.porEntregar} por entregar</small></h3>
        <div className="table"><table className="tabla-movil">
          <thead><tr><th>Parada</th><th>Cliente</th><th>Dirección</th><th>Total</th><th>Estado</th></tr></thead>
          <tbody>{g.paradas.map((p, i) => <tr key={p.id} className={p.estado === 'entregado' ? 'fila-entregada' : p.estado === 'no_entregado' ? 'fila-no-entregada' : ''}>
            <td data-label="Parada">{i + 1}</td>
            <td data-label="Cliente">{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td>
            <td data-label="Dirección">{p.direccion || 'Sin dirección registrada'}</td>
            <td data-label="Total">{dinero(p.total)}</td>
            <td data-label="Estado"><span className={`badge ${p.estado}`}>{textoResultado(p) ?? ETIQUETA_ESTADO[p.estado]}</span></td>
          </tr>)}</tbody>
        </table></div>
      </div>; })}
    </>}
  </section>;
}

// Índices en dinero. Son montos brutos: el sistema no registra IVA ni costos.
function IndicesContables({ delDia, combustible, hoy }) {
  const { total, pagos } = delDia;
  const gasto = resumenCombustible(combustible ?? []);
  const cargasTexto = gasto.cargas.length === 1 ? '1 carga' : `${gasto.cargas.length} cargas`;
  return <>
    <div className="kpis indices">{[
      ['Venta del día', dinero(total), delDia.entregados === 1 ? '1 pedido entregado hoy' : `${delDia.entregados} pedidos entregados hoy`],
      ['Efectivo del día', dinero(pagos.efectivo), `${porcentaje(pagos.efectivo, total)}% de la venta del día`],
      ['Transferencias del día', dinero(pagos.transferencia), `${porcentaje(pagos.transferencia, total)}% de la venta del día`],
      ['Combustible del día', combustible ? dinero(gasto.total) : '—', combustible ? `${cargasTexto} · ${porcentaje(gasto.total, total)}% de la venta del día` : 'No se pudo consultar'],
    ].map(([etiqueta, valor, detalle]) => <article key={etiqueta}><span>{etiqueta}</span><strong>{valor}</strong><small>{detalle}</small></article>)}</div>
    <p className="muted">Operación de hoy, {fechaDe(hoy)}. Montos brutos según lo entregado: la venta no descuenta IVA, costos ni combustible.{pagos.sinDato > 0 && ` Hay ${dinero(pagos.sinDato)} en ventas sin método de pago registrado.`}</p>
  </>;
}

// Con muchas barras se muestra una etiqueta cada tantas, para que no se monten.
const pasoEtiquetas = cantidad => Math.ceil(cantidad / 9);
const claseBarras = filas => `barras-periodo${filas.length > MAX_BARRAS_CON_VALOR ? ' muchas' : filas.length > 7 ? ' varias' : ''}`;

// Entregados y no entregados por fecha (pestaña Reportes).
export function EntregasPorFecha({ filas, entregados, noEntregados }) {
  const maximo = Math.max(...filas.map(f => f.entregados + f.noEntregados));
  const altura = n => (maximo ? `${(n / maximo) * 100}%` : 0);
  const paso = pasoEtiquetas(filas.length);
  return <figure className="grafico">
    <ul className="leyenda-grafico">
      <li><i style={{ background: 'var(--estado-entregado)' }}/>✓ <b>{entregados}</b> entregados · {porcentaje(entregados, entregados + noEntregados)}%</li>
      <li><i style={{ background: 'var(--estado-no-entregado)' }}/>✗ <b>{noEntregados}</b> no entregados · {porcentaje(noEntregados, entregados + noEntregados)}%</li>
    </ul>
    <div className={claseBarras(filas)} role="img" aria-label={`${entregados} pedidos entregados y ${noEntregados} no entregados en el período. El detalle está en la tabla de abajo.`}>
      {filas.map((f, i) => <div className="col-periodo" key={f.clave} tabIndex={0}>
        <div className="area-periodo apilada">
          {f.noEntregados > 0 && <span className="barra-periodo no-entregada" style={{ height: altura(f.noEntregados) }}/>}
          {f.entregados > 0 && <span className="barra-periodo entregada" style={{ height: altura(f.entregados) }}/>}
        </div>
        <small>{i % paso === 0 ? f.etiqueta : ''}</small>
        <span className="tip-grafico"><b>✓ {f.entregados} · ✗ {f.noEntregados}</b>{f.etiqueta}</span>
      </div>)}
    </div>
    <details><summary>Ver como tabla</summary>
      <div className="table"><table><thead><tr><th>Fecha</th><th>Entregados</th><th>No entregados</th></tr></thead>
        <tbody>{filas.map(f => <tr key={f.clave}><td>{f.etiqueta}</td><td>{f.entregados}</td><td>{f.noEntregados}</td></tr>)}</tbody></table></div>
    </details>
  </figure>;
}
