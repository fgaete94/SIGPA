import React, { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { hoyChile, horaCorta } from './fechas';
import { AGRUPACIONES, PERIODOS, SIN_COMUNA, opcionesVentas, pedidosPorComuna, rangoPeriodo, ventasPorPeriodo } from './indicadores';
import { useOrden } from './Ordenable';
import { MODOS, csvRanking, nombreArchivo, parametrosRanking, rankingConParticipacion } from './productosVendidos';

const porcentaje = new Intl.NumberFormat('es-CL', { style: 'percent', maximumFractionDigits: 1 });
const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);

// #137: columnas ordenables. El período se ordena por fecha (su clave) y "Sin comuna asignada" queda al final.
const COLUMNAS_VENTAS = { periodo: s => s.clave, ventas: s => s.monto, pedidos: s => s.pedidos };
const COLUMNAS_COMUNA = {
  comuna: f => (f.comuna === SIN_COMUNA ? null : f.comuna),
  pedidos: f => f.pedidos,
  clientes: f => f.clientes,
  participacion: f => f.porcentaje,
};

// Pestaña Reportes: un período común para ventas (#71), productos más vendidos (#72) y pedidos por comuna (#73).
export default function Reportes({ pedidos, clientes, actualizadoEn }) {
  const [periodo, setPeriodo] = useState('30');
  const [rango, setRango] = useState(() => rangoPeriodo('30', hoyChile()));
  const invalido = Boolean(rango.desde && rango.hasta && rango.desde > rango.hasta);
  const elegirPeriodo = valor => { setPeriodo(valor); if (valor !== 'otro') setRango(rangoPeriodo(valor, hoyChile())); };
  const cambiarFecha = (campo, valor) => { setPeriodo('otro'); setRango(r => ({ ...r, [campo]: valor })); };

  return <>
    <section className="no-print">
      <div className="filters periodo">
        <label>Período<select value={periodo} onChange={e => elegirPeriodo(e.target.value)}>{PERIODOS.map(([valor, texto]) => <option key={valor} value={valor}>{texto}</option>)}</select></label>
        <label>Desde<input type="date" value={rango.desde} max={rango.hasta || undefined} onChange={e => cambiarFecha('desde', e.target.value)}/></label>
        <label>Hasta<input type="date" value={rango.hasta} min={rango.desde || undefined} onChange={e => cambiarFecha('hasta', e.target.value)}/></label>
        {actualizadoEn && <span className="muted actualizado">Datos de las {horaCorta(actualizadoEn)} · se actualizan cada minuto</span>}
      </div>
      {invalido && <p role="alert" className="error">La fecha "desde" es posterior a la fecha "hasta".</p>}
    </section>
    {!invalido && <>
      <Ventas pedidos={pedidos} clientes={clientes} rango={rango}/>
      <ProductosVendidos rango={rango} actualizadoEn={actualizadoEn}/>
      <PorComuna pedidos={pedidos} rango={rango}/>
    </>}
  </>;
}

// Historia #71: ventas (pedidos entregados) por día, semana o mes, con filtro de comuna y tipo de cliente.
function Ventas({ pedidos, clientes, rango }) {
  const [agrupar, setAgrupar] = useState('dia');
  const [comuna, setComuna] = useState('');
  const [tipo, setTipo] = useState('');
  const opciones = useMemo(() => opcionesVentas(pedidos, clientes), [pedidos, clientes]);
  const ventas = useMemo(() => ventasPorPeriodo(pedidos, { ...rango, agrupar, comuna, tipo, tipoPorCliente: opciones.tipoPorCliente }), [pedidos, rango, agrupar, comuna, tipo, opciones]);
  const unidad = AGRUPACIONES.find(([valor]) => valor === agrupar)[1].toLowerCase();

  return <section className="ventas">
    <div className="title"><h2>Ventas</h2></div>
    <p>Suma de los pedidos entregados, según la fecha del pedido. El total de cada pedido es lo realmente entregado.</p>
    <div className="filters periodo">
      <label>Ver por<select value={agrupar} onChange={e => setAgrupar(e.target.value)}>{AGRUPACIONES.map(([valor, texto]) => <option key={valor} value={valor}>{texto}</option>)}</select></label>
      <label>Comuna<select value={comuna} onChange={e => setComuna(e.target.value)}><option value="">Todas</option>{opciones.comunas.map(c => <option key={c}>{c}</option>)}</select></label>
      <label>Tipo de cliente<select value={tipo} onChange={e => setTipo(e.target.value)}><option value="">Todos</option>{opciones.tipos.map(([id, nombre]) => <option key={id} value={id}>{nombre}</option>)}</select></label>
    </div>
    <div className="kpis">
      <article><span>Ventas</span><strong>{dinero(ventas.monto)}</strong></article>
      <article><span>Pedidos entregados</span><strong>{ventas.pedidos}</strong></article>
      <article><span>Ticket promedio</span><strong>{dinero(ventas.ticket)}</strong></article>
      <article><span>En curso (aún sin entregar)</span><strong>{dinero(ventas.enCurso.monto)}</strong><small>{ventas.enCurso.pedidos === 1 ? '1 pedido' : `${ventas.enCurso.pedidos} pedidos`}</small></article>
    </div>
    {ventas.serie.length > 0 ? <GraficoVentas serie={ventas.serie} unidad={unidad}/> : <p>No hay ventas en el período y filtros elegidos.</p>}
    <p className="muted">Unidades vendidas: pendiente. El listado de pedidos del backend todavía no trae las cantidades por producto.{agrupar !== 'dia' && ' La primera y la última barra pueden cubrir un período incompleto, según las fechas elegidas.'}</p>
  </section>;
}

function GraficoVentas({ serie, unidad }) {
  const { filas, encabezado } = useOrden(serie, COLUMNAS_VENTAS);
  const maximo = Math.max(...serie.map(s => s.monto));
  // Con muchas barras se muestra una etiqueta cada tantas, para que no se monten.
  const paso = Math.ceil(serie.length / 12);
  return <figure className="grafico">
    <div className="barras" role="img" aria-label={`Ventas por ${unidad}: ${serie.length} barras. Mayor venta: ${dinero(maximo)}. El detalle está en la tabla de abajo.`}>
      {serie.map((s, i) => <div className="columna" key={s.clave} title={`${s.etiqueta}: ${dinero(s.monto)} (${s.pedidos} ${s.pedidos === 1 ? 'pedido' : 'pedidos'})`}>
        <div className="area"><span className={`valor${s.monto ? '' : ' vacio'}`} style={{ height: maximo ? `${(s.monto / maximo) * 100}%` : 0 }}/></div>
        <small aria-hidden="true">{i % paso === 0 ? s.etiqueta : ''}</small>
      </div>)}
    </div>
    <figcaption className="muted">Barra más alta: {dinero(maximo)}. El detalle de cada barra está en "Ver como tabla".</figcaption>
    <details>
      <summary>Ver como tabla</summary>
      <div className="table">
        <table className="tabla-movil">
          <thead>
            <tr>
              {encabezado('periodo', 'Período')}
              {encabezado('ventas', 'Ventas')}
              {encabezado('pedidos', 'Pedidos')}
            </tr>
          </thead>
          <tbody>
            {filas.map(s => <tr key={s.clave}>
              <td data-label="Período">{s.etiqueta}</td>
              <td data-label="Ventas">{dinero(s.monto)}</td>
              <td data-label="Pedidos">{s.pedidos}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </details>
  </figure>;
}

// Historia #72: ranking de productos para planificar inventario. Las unidades salen de las líneas de los
// pedidos, que GET /pedidos no trae: se piden a GET /reportes/productos con el mismo período.
function ProductosVendidos({ rango, actualizadoEn }) {
  const [contar, setContar] = useState('entregados');
  const [datos, setDatos] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api(`/reportes/productos?${parametrosRanking(rango, contar)}`, { signal: controller.signal })
      .then(filas => setDatos(Array.isArray(filas) ? filas : []))
      .catch(e => { if (e.name !== 'AbortError') { setError(e.message); setDatos(null); } });
    return () => controller.abort();
  }, [rango.desde, rango.hasta, contar, actualizadoEn]);
  const { total, filas } = useMemo(() => rankingConParticipacion(datos || []), [datos]);

  function descargar() {
    // BOM para que Excel lea bien las tildes.
    const url = URL.createObjectURL(new Blob(['﻿' + csvRanking(filas, rango, contar)], { type: 'text/csv;charset=utf-8' }));
    const enlace = Object.assign(document.createElement('a'), { href: url, download: nombreArchivo(rango) });
    enlace.click();
    URL.revokeObjectURL(url);
  }

  return <section className="productos-vendidos">
    <div className="title"><h2>Productos más vendidos</h2>{filas.length > 0 && <button onClick={descargar}>Descargar CSV</button>}</div>
    <p>Ranking por unidades en el período, de mayor a menor.</p>
    <div className="filters periodo">
      <label>Contar<select value={contar} onChange={e => setContar(e.target.value)}>{MODOS.map(([valor, texto]) => <option key={valor} value={valor}>{texto}</option>)}</select></label>
    </div>
    {error && <p role="alert" className="error">No se pudo cargar el ranking: {error}</p>}
    {!datos && !error && <p role="status" className="muted">Cargando…</p>}
    {datos && <>
      <ol className="ranking" aria-label="Ranking de productos">
        {filas.map(f => <li key={f.producto_id}>
          <span className="posicion">{f.posicion}</span>
          <span className="nombre">{f.producto_nombre}<small>{dinero(f.monto)} · {f.pedidos === 1 ? '1 pedido' : `${f.pedidos} pedidos`} · {porcentaje.format(f.porcentaje)}</small></span>
          <span className="barra-ranking" aria-hidden="true"><span style={{ width: `${f.relativo * 100}%` }}/></span>
          <strong>{f.unidades}<small>{f.unidades === 1 ? 'unidad' : 'unidades'}</small></strong>
        </li>)}
      </ol>
      {filas.length > 0 && <p className="muted">{total === 1 ? '1 unidad' : `${total} unidades`} en total.{contar === 'entregados' ? ' Cuenta lo realmente entregado (registro de entrega).' : ' En los pedidos entregados cuenta lo entregado; en el resto, lo pedido.'}</p>}
      {!filas.length && <p>{contar === 'entregados' ? 'No hay pedidos entregados en el período. Las entregas se registran desde Ruta en curso o Despacho; mientras tanto puedes ver lo pedido.' : 'No hay pedidos en el período elegido.'}</p>}
    </>}
  </section>;
}

// Historia #73: dónde se concentra la demanda.
function PorComuna({ pedidos, rango }) {
  const { total, filas: porComuna } = useMemo(() => pedidosPorComuna(pedidos, rango), [pedidos, rango]);
  const { filas, encabezado } = useOrden(porComuna, COLUMNAS_COMUNA);
  return <section>
    <div className="title"><h2>Pedidos por comuna</h2></div>
    <p>Dónde se concentra la demanda, según la comuna del cliente. No incluye pedidos cancelados.</p>
    <div className="table">
      <table className="por-comuna tabla-movil">
        <caption className="muted">{total === 1 ? '1 pedido' : `${total} pedidos`} en el período</caption>
        <thead>
          <tr>
            {encabezado('comuna', 'Comuna')}
            {encabezado('pedidos', 'Pedidos')}
            {encabezado('clientes', 'Clientes')}
            {encabezado('participacion', 'Participación')}
          </tr>
        </thead>
        <tbody>
          {filas.map(f => <tr key={f.comuna}>
            <td data-label="Comuna">{f.comuna}</td>
            <td data-label="Pedidos">{f.pedidos}</td>
            <td data-label="Clientes">{f.clientes}</td>
            <td data-label="Participación"><span className="barra" aria-hidden="true"><span style={{ width: `${f.porcentaje * 100}%` }}/></span>{porcentaje.format(f.porcentaje)}</td>
          </tr>)}
        </tbody>
      </table>
      {!filas.length && <p>No hay pedidos en el período elegido.</p>}
    </div>
  </section>;
}
