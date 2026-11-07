import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from './api';
import { logout } from './auth';
import { siguienteParada } from './capasMapa';
import { MEDIOS_COMBUSTIBLE, etiquetaCombustible, leerMonto, resumenCombustible, textoCarga } from './combustible';
import { ajustarEntregada, datosEntrega, formEntrega, resumenEntrega } from './entregas';
import { resuelta } from './estados';
import MapaRuta, { NumeroParada } from './MapaRuta';
import { enlaceComoLlegar, textoResultado } from './reparto';
import { agruparPorViaje, encabezadoViaje } from './viajes';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);
const RECARGA_MS = 30000;

// Interfaz del repartidor en el celular: es lo único que ve al entrar con su cuenta. Muestra la ruta que tiene
// asignada hoy y en cada parada marca Entregado (con la cantidad y el método de pago) o No entregado.
export default function VistaRepartidor({ session, onLogout }) {
  const [datos, setDatos] = useState(null);
  const [error, setError] = useState('');
  const [foco, setFoco] = useState(null);
  const [verMapa, setVerMapa] = useState(true);
  const [enviando, setEnviando] = useState(false);
  const [combustible, setCombustible] = useState([]);
  const [verCombustible, setVerCombustible] = useState(false);

  const cargar = useCallback(() => api('/repartidor/ruta').then(d => { setDatos(d); setError(''); }).catch(e => setError(e.message)), []);
  useEffect(() => { cargar(); }, [cargar]);
  const cargarCombustible = useCallback(() => api('/repartidor/combustible').then(setCombustible).catch(e => setError(e.message)), []);
  useEffect(() => { cargarCombustible(); }, [cargarCombustible]);
  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) cargar(); }, RECARGA_MS);
    return () => clearInterval(timer);
  }, [cargar]);

  async function marcar(id, cuerpo) {
    setEnviando(true); setError('');
    try { await api(`/repartidor/paradas/${id}/resultado`, { method: 'POST', body: JSON.stringify(cuerpo) }); await cargar(); }
    catch (e) { setError(e.message); }
    finally { setEnviando(false); }
  }

  const paradas = datos?.paradas || [];
  const grupos = agruparPorViaje(paradas, datos?.viajes || []);
  const siguiente = siguienteParada(grupos);
  const listas = paradas.filter(resuelta).length;
  const entregadas = paradas.filter(p => p.estado === 'entregado').length;
  const hayCarga = resumenCombustible(combustible).cargas.length > 0;
  const botonCombustible = <button type="button" className={`boton-combustible${hayCarga ? ' con-carga' : ''}`} onClick={() => setVerCombustible(true)}>{etiquetaCombustible(combustible)}</button>;
  const verParada = id => { setVerMapa(true); setFoco(f => ({ id, n: (f?.n ?? 0) + 1 })); };

  return <div className="repartidor">
    <header className="repartidor-cabecera">
      <img src="/marca/logo-blanco.svg" alt="Agua DM" width="2320" height="2280"/>
      <div><small>Repartidor</small><strong>{datos?.repartidor?.nombre || session.user.email}</strong></div>
      <button onClick={async () => { await logout(); onLogout(); }}>Salir</button>
    </header>
    <main>
      {error && <p role="alert" className="error">{error}</p>}
      {!datos && !error && <p className="muted">Cargando tu ruta…</p>}
      {datos && !paradas.length && <p className="notice">No tienes una ruta asignada para hoy. Avísale a la administración si deberías tenerla.</p>}
      {paradas.length > 0 && <>
        <div className="repartidor-avance" aria-live="polite">
          <h1>Ruta de hoy</h1>
          <p><strong>{listas} de {paradas.length}</strong> paradas listas · {entregadas} {entregadas === 1 ? 'entregada' : 'entregadas'}</p>
          <div className="barra-avance"><span style={{ width: `${(listas / paradas.length) * 100}%` }}/></div>
        </div>
        <button className="enlace" aria-expanded={verMapa} onClick={() => setVerMapa(v => !v)}>{verMapa ? 'Ocultar mapa' : 'Ver mapa'}</button>
        {verMapa ? <MapaRuta grupos={grupos} foco={foco} avance extra={botonCombustible}/> : <div className="mapa-filtros">{botonCombustible}</div>}
        {listas === paradas.length && <p role="status" className="ok">¡Ruta terminada! Marcaste todas las paradas.</p>}
        {grupos.map(g => <div key={g.viaje ?? 'sin-viaje'}>
          {grupos.length > 1 && g.viaje != null && <h2 className="viaje-titulo">{encabezadoViaje({ viaje: g.viaje })}</h2>}
          {g.paradas.map((p, i) => <TarjetaParada key={p.id} parada={p} numero={i + 1} siguiente={p.id === siguiente?.id} enviando={enviando} onMarcar={cuerpo => marcar(p.id, cuerpo)} onVer={() => verParada(p.id)}/>)}
        </div>)}
      </>}
    </main>
    {verCombustible && <VentanaCombustible registros={combustible} onCambio={cargarCombustible} onCerrar={() => setVerCombustible(false)}/>}
  </div>;
}

// Si cargó combustible, cuánto y cómo pagó. Se pueden registrar varias cargas en el día.
function VentanaCombustible({ registros, onCambio, onCerrar }) {
  const [cargando, setCargando] = useState(false);
  const [monto, setMonto] = useState('');
  const [medio, setMedio] = useState('');
  const [error, setError] = useState('');
  const [ocupado, setOcupado] = useState(false);
  const { cargas, noCargo } = resumenCombustible(registros);
  async function enviar(ruta, opciones) {
    setOcupado(true); setError('');
    try { await api(ruta, opciones); await onCambio(); return true; }
    catch (e) { setError(e.message); return false; }
    finally { setOcupado(false); }
  }
  const noCargue = async () => { if (await enviar('/repartidor/combustible', { method: 'POST', body: JSON.stringify({ cargo: false }) })) onCerrar(); };
  async function guardar(e) {
    e.preventDefault();
    const valor = leerMonto(monto);
    if (!valor) return setError('Escribe el monto en pesos. Ejemplo: 20000');
    if (!medio) return setError('Elige cómo pagaste.');
    if (await enviar('/repartidor/combustible', { method: 'POST', body: JSON.stringify({ cargo: true, monto: valor, medio_pago: medio }) })) onCerrar();
  }
  return createPortal(<div className="modal" role="dialog" aria-modal="true" aria-labelledby="combustible-titulo"><section className="ventana-combustible">
    <h2 id="combustible-titulo">Combustible de hoy</h2>
    {cargas.length > 0 && <ul className="cargas">{cargas.map(r => <li key={r.id}><span>{textoCarga(r)}</span><button type="button" className="enlace" disabled={ocupado} onClick={() => enviar(`/repartidor/combustible/${r.id}`, { method: 'DELETE' })}>Quitar</button></li>)}</ul>}
    {noCargo && <p className="muted">Informaste que hoy no cargaste.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {!cargando ? <>
      <p><strong>{cargas.length ? '¿Cargaste de nuevo?' : '¿Cargaste combustible?'}</strong></p>
      <div className="tarjeta-acciones">
        <button type="button" className="boton-pago" disabled={ocupado} onClick={() => { setCargando(true); setError(''); }}>Sí, cargué</button>
        <button type="button" disabled={ocupado} onClick={cargas.length ? onCerrar : noCargue}>{cargas.length ? 'No, cerrar' : 'No cargué'}</button>
      </div>
      {!cargas.length && <button type="button" className="enlace cerrar-combustible" disabled={ocupado} onClick={onCerrar}>Cerrar</button>}
    </> : <form onSubmit={guardar}>
      <label>¿Cuánto cargaste?<input autoFocus inputMode="numeric" placeholder="20000" value={monto} disabled={ocupado} onChange={e => setMonto(e.target.value)}/></label>
      <p className="etiqueta-pago"><strong>¿Cómo pagaste?</strong></p>
      <div className="medios-combustible">{MEDIOS_COMBUSTIBLE.map(([clave, nombre]) => <button type="button" key={clave} aria-pressed={medio === clave} disabled={ocupado} onClick={() => setMedio(clave)}>{nombre}</button>)}</div>
      <div className="tarjeta-acciones">
        <button type="button" disabled={ocupado} onClick={() => setCargando(false)}>Volver</button>
        <button className="primary" disabled={ocupado}>{ocupado ? 'Guardando…' : 'Guardar'}</button>
      </div>
    </form>}
  </section></div>, document.body);
}

function TarjetaParada({ parada: p, numero, siguiente, enviando, onMarcar, onVer }) {
  // paso: null (botones), 'total' (¿se entregó todo?), 'ajustar' (cuánto entregó), 'pago' (elegir el método) o 'no' (confirmar que no se pudo entregar).
  const [paso, setPaso] = useState(null);
  const [form, setForm] = useState([]);
  const resumen = resumenEntrega(form);
  const empezarEntrega = () => { setForm(formEntrega(p.lineas)); setPaso('total'); };
  const entregoTodo = () => { setForm(formEntrega(p.lineas)); setPaso('pago'); };
  const pagar = medio_pago => onMarcar({ resultado: 'entregado', medio_pago, ...datosEntrega(form) });
  const lista = resuelta(p);
  const enlace = enlaceComoLlegar(p);
  const nombre = p.cliente_nombre || `Pedido ${p.id}`;
  useEffect(() => { setPaso(null); }, [p.estado]);
  const clase = p.estado === 'entregado' ? ' entregada' : p.estado === 'no_entregado' ? ' no-entregada' : siguiente ? ' siguiente' : '';
  return <article className={`tarjeta-parada${clase}`}>
    <div className="tarjeta-datos">
      <NumeroParada numero={numero} parada={p} nombre={nombre} onVer={onVer}/>
      <div>
        {siguiente && <span className="badge siguiente">Siguiente</span>}
        <h3>{nombre}</h3>
        <p>{p.direccion || 'Sin dirección registrada'}</p>
        {!lista && paso === null && <p className="por-cobrar">Por cobrar: <b>{dinero(p.total)}</b></p>}
      </div>
    </div>
    {lista ? <div className="tarjeta-resultado">
      <strong>{textoResultado(p)}</strong>
      <button type="button" className="enlace" disabled={enviando} onClick={() => onMarcar({ resultado: null })}>Corregir</button>
    </div> : <>
      {paso === null && <>
        <div className="tarjeta-contacto">
          {enlace && <a className="boton" href={enlace} target="_blank" rel="noopener noreferrer">Cómo llegar</a>}
          {p.cliente_telefono && <a className="boton" href={`tel:+${p.cliente_telefono}`}>Llamar</a>}
        </div>
        <div className="tarjeta-acciones">
          <button type="button" className="boton-entregado" disabled={enviando} onClick={empezarEntrega}>Entregado</button>
          <button type="button" className="boton-no-entregado" disabled={enviando} onClick={() => setPaso('no')}>No entregado</button>
        </div>
      </>}
      {paso === 'total' && <div className="tarjeta-paso">
        <p><strong>¿Se entregó todo el pedido?</strong></p>
        <ul className="lineas-parada">{form.map(l => <li key={l.id}>{l.solicitada} × {l.producto}</li>)}</ul>
        <p>Total: <b>{dinero(resumen.totalPedido)}</b></p>
        <div className="tarjeta-acciones">
          <button type="button" className="boton-entregado" disabled={enviando} onClick={entregoTodo}>Sí</button>
          <button type="button" disabled={enviando} onClick={() => setPaso('ajustar')}>Otra cantidad</button>
        </div>
        <button type="button" className="enlace" disabled={enviando} onClick={() => setPaso(null)}>Volver</button>
      </div>}
      {paso === 'ajustar' && <div className="tarjeta-paso">
        <p><strong>¿Cuánto entregó?</strong></p>
        <div className="cantidades-parada">{form.map(l => <div className="cantidad-parada" key={l.id}>
          <div><strong>{l.producto}</strong><small>Pidió {l.solicitada} · {dinero(l.precio)} c/u</small></div>
          <div className="selector-cantidad">
            <button type="button" aria-label={`Quitar una unidad de ${l.producto}`} disabled={enviando || l.entregada === '0'} onClick={() => setForm(f => ajustarEntregada(f, l.id, -1))}>−</button>
            <output aria-live="polite">{l.entregada}</output>
            <button type="button" aria-label={`Agregar una unidad de ${l.producto}`} disabled={enviando} onClick={() => setForm(f => ajustarEntregada(f, l.id, 1))}>+</button>
          </div>
        </div>)}</div>
        <p className="total-parada">Por cobrar: <b>{dinero(resumen.total)}</b>{resumen.total !== resumen.totalPedido && <small> · era {dinero(resumen.totalPedido)}</small>}</p>
        {!resumen.unidades && <p className="notice">Si no dejó nada, vuelve y marca No entregado.</p>}
        <div className="tarjeta-acciones">
          <button type="button" disabled={enviando} onClick={() => setPaso('total')}>Volver</button>
          <button type="button" className="primary" disabled={enviando || !resumen.unidades} onClick={() => setPaso('pago')}>Continuar</button>
        </div>
      </div>}
      {paso === 'pago' && <div className="tarjeta-paso">
        <p>Por cobrar: <b>{dinero(resumen.total)}</b></p>
        <p className="total-parada"><strong>¿Cómo pagó?</strong></p>
        <div className="tarjeta-acciones">
          <button type="button" className="boton-pago" disabled={enviando} onClick={() => pagar('efectivo')}>Efectivo</button>
          <button type="button" className="boton-pago" disabled={enviando} onClick={() => pagar('transferencia')}>Transferencia</button>
        </div>
        <button type="button" className="enlace" disabled={enviando} onClick={() => setPaso('total')}>Volver</button>
      </div>}
      {paso === 'no' && <div className="tarjeta-paso">
        <p><strong>¿Confirmas que no se pudo entregar?</strong></p>
        <div className="tarjeta-acciones">
          <button type="button" className="boton-no-entregado" disabled={enviando} onClick={() => onMarcar({ resultado: 'no_entregado' })}>Sí</button>
          <button type="button" disabled={enviando} onClick={() => setPaso(null)}>Volver</button>
        </div>
      </div>}
    </>}
  </article>;
}
