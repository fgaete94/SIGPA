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
  return <div className="resumen-periodo resumen-hoy">
    <IndicesContables delDia={delDia} combustible={combustible} hoy={hoy}/>
    <EntregasDelDia pedidos={pedidos} clientes={clientes}/>
  </div>;
}

// Lo que se está entregando hoy: las paradas de la ruta con su avance. Solo se consulta; se gestiona en Rutas.
// Cada ruta abre con la misma franja de agua que ve el repartidor en su celular.
function EntregasDelDia({ pedidos, clientes }) {
  const { paradas } = rutaEnCurso(pedidos, clientes);
  const r = resumenEntregas(paradas);
  return <section className="entregas-dia entregas-hoy">
    <div className="entregas-hoy-titulo">
      <h2>Entregas del día</h2>
      {paradas.length > 0 && <p aria-live="polite"><b>{r.entregadas + r.noEntregadas}</b> de <b>{r.total}</b> paradas resueltas{r.porEntregar > 0 && <> · faltan <b>{r.porEntregar}</b> ({dinero(r.montoPorEntregar)})</>}</p>}
    </div>
    {!paradas.length ? <p className="muted">Hoy no hay una ruta en curso. Cuando confirmes una en "Preparar ruta", su avance aparece aquí.</p> : <>
      {agruparPorViaje(paradas).map(g => {
        const ruta = resumenEntregas(g.paradas);
        const listas = ruta.entregadas + ruta.noEntregadas;
        const terminada = ruta.total > 0 && listas === ruta.total;
        return <div className={`ruta-resumen ruta-hoy${terminada ? ' terminada' : ''}`} key={g.viaje ?? 'sin-ruta'}>
          <div className="ruta-hoy-franja" style={{ '--avance': `${ruta.total ? (listas / ruta.total) * 100 : 0}%` }}>
            <span className="ruta-agua" aria-hidden="true"/>
            <h3>{g.viaje != null ? `Ruta ${g.viaje}` : 'Sin ruta'}</h3>
            <p><b>{listas} de {ruta.total}</b> {ruta.total === 1 ? 'parada' : 'paradas'}</p>
            <p><b>{dinero(ruta.montoEntregado)}</b> entregado</p>
            {ruta.noEntregadas > 0 && <p className="ruta-hoy-fallas">{ruta.noEntregadas} no {ruta.noEntregadas === 1 ? 'entregado' : 'entregados'}</p>}
          </div>
          <div className="table"><table className="tabla-movil">
            <thead><tr><th>Parada</th><th>Cliente</th><th>Dirección</th><th className="num">Total</th><th>Estado</th></tr></thead>
            <tbody>{g.paradas.map((p, i) => <tr key={p.id} className={p.estado === 'entregado' ? 'fila-entregada' : p.estado === 'no_entregado' ? 'fila-no-entregada' : ''}>
              <td data-label="Parada" className="parada-num">{i + 1}</td>
              <td data-label="Cliente">{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td>
              <td data-label="Dirección">{p.direccion || 'Sin dirección registrada'}</td>
              <td data-label="Total" className="num">{dinero(p.total)}</td>
              <td data-label="Estado"><span className={`badge ${p.estado}`}>{textoResultado(p) ?? ETIQUETA_ESTADO[p.estado]}</span></td>
            </tr>)}</tbody>
          </table></div>
        </div>;
      })}
    </>}
  </section>;
}

// Índices en dinero. Son montos brutos: el sistema no registra IVA ni costos.
// La venta del día es la cifra principal; debajo, una sola barra con cómo se pagó.
function IndicesContables({ delDia, combustible, hoy }) {
  const { total, pagos } = delDia;
  const gasto = resumenCombustible(combustible ?? []);
  const cargasTexto = gasto.cargas.length === 1 ? '1 carga' : `${gasto.cargas.length} cargas`;
  const tramos = [
    ['efectivo', 'Efectivo', pagos.efectivo],
    ['transferencia', 'Transferencia', pagos.transferencia],
    ['sin-dato', 'Sin método registrado', pagos.sinDato],
  ].filter(([, , monto]) => monto > 0);
  return <section className="venta-hoy">
    <div className="venta-hoy-cifra">
      <h2>Venta de hoy</h2>
      <p className="venta-hoy-total">{dinero(total)}</p>
      <div className="mezcla-pagos" role="img" aria-label={tramos.length ? tramos.map(([, nombre, monto]) => `${nombre} ${dinero(monto)}`).join(', ') : 'Todavía no hay ventas hoy'}>
        {tramos.map(([clase, nombre, monto]) => <span key={clase} className={`tramo ${clase}`} style={{ flexGrow: monto }} title={`${nombre}: ${dinero(monto)}`}/>)}
      </div>
      <ul className="mezcla-leyenda">
        <li><i className="tramo efectivo"/>Efectivo <b>{dinero(pagos.efectivo)}</b> <small>{porcentaje(pagos.efectivo, total)}%</small></li>
        <li><i className="tramo transferencia"/>Transferencia <b>{dinero(pagos.transferencia)}</b> <small>{porcentaje(pagos.transferencia, total)}%</small></li>
        {pagos.sinDato > 0 && <li><i className="tramo sin-dato"/>Sin método registrado <b>{dinero(pagos.sinDato)}</b></li>}
      </ul>
    </div>
    <dl className="venta-hoy-datos">
      <div><dt>Pedidos entregados</dt><dd>{delDia.entregados}</dd></div>
      <div><dt>Combustible</dt><dd>{combustible ? dinero(gasto.total) : '—'}</dd><small>{combustible ? `${cargasTexto} · ${porcentaje(gasto.total, total)}% de la venta` : 'No se pudo consultar'}</small></div>
    </dl>
    <p className="venta-hoy-nota">{fechaDe(hoy)}. Montos brutos según lo entregado: la venta no descuenta IVA, costos ni combustible.</p>
  </section>;
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
