import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from './api';
import { fechaHora, hoyChile } from './fechas';
import { cargaRuta, columnasProductos, etiquetaProducto, fechaHoja, filasHoja, totalPorCobrar } from './hojaRuta';
import { agruparPorViaje, encabezadoViaje } from './viajes';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);
const MEDIOS_PAGO = ['Efectivo', 'Transferencia', 'Tarjeta'];
const CLAVE_CONDUCTOR = 'sigpa.conductor';

function leerConductor() {
  try { return localStorage.getItem(CLAVE_CONDUCTOR) || ''; } catch { return ''; }
}
function recordarConductor(nombre) {
  try { localStorage.setItem(CLAVE_CONDUCTOR, nombre); } catch { /* sin almacenamiento se vuelve a escribir la próxima vez */ }
}

// Hoja de ruta para el conductor: vista previa a pantalla completa e impresión en horizontal.
// Recibe una copia fija de las paradas (con el orden que se ve en "Ruta en curso"), así una recarga
// de datos no la cambia mientras está abierta. Los productos salen de GET /pedidos/{id} de cada parada.
export default function HojaDeRuta({ paradas, viajes = [], clientes, productos, onClose }) {
  const [detalles, setDetalles] = useState(null);
  const [fallidos, setFallidos] = useState([]);
  const [sectores, setSectores] = useState([]);
  const [intento, setIntento] = useState(0);
  const [dia, setDia] = useState(() => hoyChile());
  const [conductor, setConductor] = useState(leerConductor);
  const cerrar = useRef(null);
  const alCerrar = useRef(onClose);
  alCerrar.current = onClose;

  useEffect(() => {
    const controller = new AbortController();
    setDetalles(null); setFallidos([]);
    Promise.allSettled(paradas.map(p => api(`/pedidos/${p.id}`, { signal: controller.signal }))).then(resultados => {
      if (controller.signal.aborted) return;
      const porId = {};
      const errores = [];
      resultados.forEach((r, i) => { if (r.status === 'fulfilled') porId[paradas[i].id] = r.value; else errores.push(paradas[i].id); });
      setDetalles(porId); setFallidos(errores);
    });
    return () => controller.abort();
  }, [paradas, intento]);

  // El sector es un dato de apoyo: si /sectores falla, la hoja sale igual sin él.
  useEffect(() => {
    const controller = new AbortController();
    api('/sectores', { signal: controller.signal }).then(s => { if (Array.isArray(s)) setSectores(s); }).catch(() => {});
    return () => controller.abort();
  }, []);

  useEffect(() => {
    cerrar.current?.focus();
    const alPresionar = e => { if (e.key === 'Escape') alCerrar.current(); };
    document.addEventListener('keydown', alPresionar);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', alPresionar); document.body.style.overflow = overflow; };
  }, []);

  function imprimir() {
    recordarConductor(conductor.trim());
    window.print();
  }

  const filas = filasHoja(paradas, detalles || {}, clientes, sectores);
  const columnas = columnasProductos(filas, productos);
  const carga = cargaRuta(filas, columnas);
  const unidades = Object.values(carga).reduce((suma, n) => suma + n, 0);
  // Con más de un viaje, la carga de cada uno (el camión vuelve a cargar entre viajes) y el encabezado en las paradas.
  const grupos = agruparPorViaje(filas, viajes);
  const conViajes = grupos.length > 1 || grupos.some(g => g.viaje != null);
  const sumar = cantidades => Object.values(cantidades).reduce((suma, n) => suma + n, 0);
  const listo = detalles && !fallidos.length;
  const encabezadosProductos = columnas.map(nombre => <th key={nombre} scope="col" title={nombre}>{etiquetaProducto(nombre)}</th>);

  return createPortal(<div className="hoja-capa" role="dialog" aria-modal="true" aria-labelledby="hoja-titulo">
    <div className="hoja-barra">
      <div className="hoja-barra-texto">
        <h2 id="hoja-titulo">Hoja de ruta</h2>
        <p className="muted">Vista previa. Se imprime en horizontal; en el diálogo de impresión también puedes elegir "Guardar como PDF".</p>
      </div>
      <label>Fecha de reparto<input type="date" value={dia} onChange={e => setDia(e.target.value)}/></label>
      <label>Conductor<input value={conductor} placeholder="Opcional" maxLength={40} onChange={e => setConductor(e.target.value)}/></label>
      <div className="acciones">
        <button className="primary" disabled={!listo} onClick={imprimir}>Imprimir</button>
        <button ref={cerrar} onClick={onClose}>Cerrar</button>
      </div>
      {!detalles && <p role="status" className="muted">Cargando los productos de cada pedido…</p>}
      {fallidos.length > 0 && <div role="alert" className="error">No se pudo cargar el detalle {fallidos.length === 1 ? 'del pedido' : 'de los pedidos'} {fallidos.map(id => `#${id}`).join(', ')}: la hoja saldría sin sus productos. <button onClick={() => setIntento(n => n + 1)}>Reintentar</button></div>}
    </div>
    <div className="hoja-papel">
      <article className="hoja" aria-label="Hoja de ruta para imprimir">
        <div className="hoja-cabecera">
          <div className="hoja-marca">
            <img src="/marca/logo.svg" alt="Agua DM" width="2320" height="2280"/>
            <div><strong>Hoja de ruta</strong><span>Agua DM · Reparto a domicilio</span></div>
          </div>
          <dl className="hoja-datos">
            <div><dt>Fecha</dt><dd className="hoja-fecha">{fechaHoja(dia) || <span className="hoja-linea"/>}</dd></div>
            <div><dt>Conductor</dt><dd>{conductor.trim() || <span className="hoja-linea"/>}</dd></div>
            <div><dt>Paradas</dt><dd>{filas.length}</dd></div>
            <div><dt>Por cobrar</dt><dd>{dinero(totalPorCobrar(filas))}</dd></div>
          </dl>
          <dl className="hoja-casillas">
            <div><dt>Km salida</dt><dd/></div>
            <div><dt>Km llegada</dt><dd/></div>
            <div><dt>Combustible</dt><dd/></div>
          </dl>
        </div>

        <div className="hoja-resumen">
          <div>
            <h3>Carga del camión</h3>
            <table className="hoja-carga">
              <thead><tr><th scope="col"><span className="hoja-oculto">Fila</span></th>{encabezadosProductos}<th scope="col">Total</th></tr></thead>
              <tbody>
                {conViajes && grupos.map(g => { const c = cargaRuta(g.paradas, columnas); return <tr key={g.viaje ?? 'sin-viaje'}><th scope="row">{g.viaje != null ? `Viaje ${g.viaje}` : 'Sin viaje'}</th>{columnas.map(n => <td key={n}>{c[n] || '–'}</td>)}<td>{sumar(c)}</td></tr>; })}
                <tr><th scope="row">{conViajes ? 'Total a entregar' : 'A entregar'}</th>{columnas.map(n => <td key={n}>{carga[n] || '–'}</td>)}<td>{unidades}</td></tr>
                <tr><th scope="row">Cargado</th>{columnas.map(n => <td key={n}/>)}<td/></tr>
                <tr><th scope="row">Vuelve</th>{columnas.map(n => <td key={n}/>)}<td/></tr>
              </tbody>
            </table>
          </div>
          <div>
            <h3>Rendición</h3>
            <table className="hoja-rendicion">
              <tbody>
                <tr><th scope="row">Por cobrar</th><td>{dinero(totalPorCobrar(filas))}</td></tr>
                {MEDIOS_PAGO.map(m => <tr key={m}><th scope="row">{m}</th><td/></tr>)}
                <tr><th scope="row">Total recaudado</th><td/></tr>
              </tbody>
            </table>
          </div>
        </div>

        <h3>Paradas</h3>
        <table className="hoja-paradas">
          <thead><tr>
            <th scope="col">N°</th><th scope="col">Cliente</th><th scope="col">Dirección</th><th scope="col"><span className="hoja-oculto">Fila</span></th>
            {encabezadosProductos}
            <th scope="col">Monto</th><th scope="col">Pago</th><th scope="col">Observaciones / firma</th>
          </tr></thead>
          {filas.map((f, i) => <React.Fragment key={f.pedidoId}>{conViajes && f.viaje !== filas[i - 1]?.viaje && <tbody className="hoja-viaje"><tr><th scope="rowgroup" colSpan={columnas.length + 7}>{encabezadoViaje(grupos.find(g => g.viaje === f.viaje)) || 'Sin viaje'}</th></tr></tbody>}<tbody>
            <tr>
              <td rowSpan={2} className="hoja-numero"><b>{f.numero}</b></td>
              <td rowSpan={2} className="hoja-cliente"><strong>{f.cliente}</strong><small className="hoja-codigos">Pedido #{f.pedidoId} · Cliente {f.clienteId}</small>{f.telefono && <small>{f.telefono}</small>}</td>
              <td rowSpan={2} className="hoja-direccion"><strong>{f.direccion || 'Sin dirección registrada'}</strong>{f.zona && <small>{f.zona}</small>}</td>
              <th scope="row" className="hoja-fila">Pide</th>
              {columnas.map(n => { const item = f.productos[n]; return <td key={n} className="hoja-cantidad">{item && <><b>{item.cantidad}</b>{item.precio != null && <small>{dinero(item.precio)} c/u</small>}</>}</td>; })}
              <td className="hoja-monto"><b>{dinero(f.total)}</b></td>
              <td rowSpan={2} className="hoja-pago">{MEDIOS_PAGO.map(m => <span key={m}><i className="hoja-casilla"/>{m}</span>)}</td>
              <td rowSpan={2} className="hoja-obs"/>
            </tr>
            <tr className="hoja-entrega">
              <th scope="row" className="hoja-fila">Entrega</th>
              {columnas.map(n => <td key={n}/>)}
              <td className="hoja-cobrado"><small>Cobrado</small></td>
            </tr>
          </tbody></React.Fragment>)}
        </table>

        <div className="hoja-pie">
          <p className="hoja-generada">Generada en SIGPA el {fechaHora(new Date().toISOString())}. "Pide" es lo registrado en el pedido; anota en "Entrega" lo que realmente se dejó.</p>
          <div className="hoja-firmas"><span>Firma conductor</span><span>Recibe rendición</span></div>
        </div>
      </article>
    </div>
  </div>, document.body);
}
