import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { login, logout, onSessionExpired, restoreSession } from './auth';
import { api } from './api';
import { DIAS_REPARTO } from './clientes';
import FormCliente from './FormCliente';
import Landing from './Landing';
import NuevoPedido from './NuevoPedido';
import Productos from './Productos';
import Reportes from './Reportes';
import RutaEnCurso from './RutaEnCurso';
import Rutas from './Rutas';
import './styles.css';

const money = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const name = c => [c.nombre, c.apellido_paterno, c.apellido_materno].filter(Boolean).join(' ');
function Brand({ blanco }) { return <span className="brand"><img src={blanco ? '/marca/logo-blanco.svg' : '/marca/logo.svg'} alt="Agua DM" width="2320" height="2280"/><span>SIGPA<small>Panel de operación</small></span></span>; }
function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState('inicio');
  useEffect(() => {
    let active = true;
    const unsubscribe = onSessionExpired(() => { setSession(null); setPage('login'); });
    restoreSession().then(user => { if (active) { setSession(user ? { user } : null); setLoading(false); } });
    return () => { active = false; unsubscribe(); };
  }, []);
  if (loading) return <p className="loading">Cargando sesión…</p>;
  if (session) return <Dashboard key={session.user.id} session={session} onLogout={() => { setSession(null); setPage('inicio'); }}/>;
  if (page === 'inicio') return <Landing onAdmin={() => { setPage('login'); window.scrollTo(0, 0); }}/>;
  return <><header><Brand/><button onClick={() => setPage('inicio')}>Volver al inicio</button></header><Login onLogin={user => setSession({ user })}/></>;
}
function Login({ onLogin }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError('');
    const form = new FormData(e.currentTarget);
    try { onLogin(await login(form.get('email'), form.get('password'))); }
    catch (error) { setError(error.message || 'No se pudo iniciar sesión. Revisa el correo, la contraseña y la conexión.'); setBusy(false); }
  }
  return <main className="login"><form onSubmit={submit}><span className="eyebrow">Bienvenido a SIGPA</span><h1>Acceso al equipo</h1><p>Ingresa con tu cuenta autorizada.</p><label>Correo electrónico<input autoComplete="username" name="email" type="email" required/></label><label>Contraseña<input autoComplete="current-password" name="password" type="password" required/></label>{error && <p role="alert" className="error">{error}</p>}<button disabled={busy} className="primary">{busy ? 'Ingresando…' : 'Ingresar'}</button></form></main>;
}
function Dashboard({ session, onLogout }) {
  const [tab, setTab] = useState('Resumen');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [planning, setPlanning] = useState(false);
  const [detail, setDetail] = useState(null);
  const [editing, setEditing] = useState(null);
  const [creandoPedido, setCreandoPedido] = useState(false);
  const [aviso, setAviso] = useState('');
  const [actualizadoEn, setActualizadoEn] = useState(null);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    Promise.all(['/pedidos', '/clientes', '/rutas/pedidos-pendientes', '/productos'].map(p => api(p, { signal: controller.signal })))
      .then(([pedidos, clientes, pendientes, productos]) => { if (![pedidos, clientes, pendientes, productos].every(Array.isArray)) throw new Error('Formato de datos inesperado.'); setData({ pedidos, clientes, pendientes, productos }); setActualizadoEn(new Date()); })
      .catch(e => { if (e.name !== 'AbortError') { setError(e.message); setData(null); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [revision]);
  // #73 pide datos casi en tiempo real: con Reportes abierto se recargan cada minuto (no durante una planificación).
  useEffect(() => {
    if (tab !== 'Reportes' || planning) return;
    const timer = setInterval(() => { if (!document.hidden) setRevision(r => r + 1); }, 60000);
    return () => clearInterval(timer);
  }, [tab, planning]);
  async function view(id) { setDetail(null); try { setDetail(await api(`/pedidos/${id}`)); } catch(e) { setError(e.message); } }
  const pedidos = (data?.pedidos || []).filter(p => (!status || p.estado === status) && `${p.id} ${p.cliente_nombre || ''} ${p.comuna_nombre || ''}`.toLowerCase().includes(query.toLowerCase()));
  const clientes = (data?.clientes || []).filter(c => `${name(c)} ${c.telefono || ''}`.toLowerCase().includes(query.toLowerCase()));
  // En el celular las pestañas van en una fila que se desliza: la elegida se trae a la vista.
  const navButton = t => <button key={t} className={tab === t ? 'active' : ''} aria-current={tab === t ? 'page' : undefined} onClick={e => { setTab(t); setQuery(''); setStatus(''); setAviso(''); e.currentTarget.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }}>{t}</button>;
  const dia = valor => DIAS_REPARTO.find(([v]) => v === valor)?.[1] || valor;
  return <div className="shell"><aside><Brand blanco/><nav className="pestanas" aria-label="Secciones del panel"><div className="nav-label">Operación</div>{['Resumen','Pedidos','Clientes','Productos','Rutas'].map(navButton)}<div className="nav-label">Análisis</div>{navButton('Reportes')}</nav><div className="account"><small>{session.user.email}</small><button onClick={async () => { await logout(); onLogout(); }}>Cerrar sesión</button></div></aside><main className="workspace"><header><span>Panel administrativo</span><span className="badge">Agua DM</span></header><div className="content"><div className="title"><div><span className="eyebrow">Centro de operaciones</span><h1>{tab}</h1><p>Consulta y organiza la información de SIGPA.</p></div><button disabled={busy || planning} onClick={() => setRevision(r => r + 1)}>Actualizar datos</button></div>{error && <p role="alert" className="error">{error}</p>}{aviso && <p role="status" className="ok">{aviso}</p>}{busy && <p role="status">Cargando información…</p>}{data && <>
  {tab === 'Resumen' && <><div className="kpis">{[['Pedidos registrados',data.pedidos.length],['Pendientes',data.pedidos.filter(p=>p.estado==='pendiente').length],['Confirmados',data.pedidos.filter(p=>p.estado==='confirmado').length],['Clientes activos',data.clientes.filter(c=>c.activo).length]].map(([label,n]) => <article key={label}><span>{label}</span><strong>{n}</strong></article>)}</div><p className="muted">Totales de todos los registros disponibles, sin filtro de fecha.</p><section><h2>Últimos pedidos</h2><OrderTable rows={data.pedidos.slice(0,8)} view={view}/></section></>}
  {tab === 'Pedidos' && <section><div className="filters"><input aria-label="Buscar pedidos" placeholder="Buscar cliente, comuna o ID…" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="Estado del pedido" value={status} onChange={e=>setStatus(e.target.value)}><option value="">Todos los estados</option>{['pendiente','confirmado','en_despacho','entregado','cancelado'].map(s=><option key={s}>{s}</option>)}</select><button className="primary" onClick={()=>{setCreandoPedido(true); setAviso('');}}>Nuevo pedido</button></div><OrderTable rows={pedidos} view={view}/></section>}
  {tab === 'Clientes' && <section><div className="filters"><input aria-label="Buscar clientes" placeholder="Buscar nombre o teléfono…" value={query} onChange={e=>setQuery(e.target.value)}/><button className="primary" onClick={()=>{setEditing('nuevo'); setAviso('');}}>Nuevo cliente</button></div><div className="table"><table className="tabla-movil"><thead><tr><th>Cliente</th><th>Teléfono</th><th>Dirección</th><th>Tipo</th><th>Día de reparto</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{clientes.map(c=><tr key={c.id}><td data-label="Cliente">{name(c)}</td><td data-label="Teléfono">{c.telefono || '—'}</td><td data-label="Dirección">{c.direccion || 'Sin dirección'}</td><td data-label="Tipo">{c.tipo_cliente_nombre || '—'}</td><td data-label="Día de reparto">{c.dia_reparto ? dia(c.dia_reparto) : 'Sin asignar'}</td><td data-label="Estado">{c.activo ? 'Activo' : 'Inactivo'}</td><td className="celda-acciones"><button aria-label={`Editar a ${name(c)}`} onClick={()=>{setEditing(c); setAviso('');}}>Editar</button></td></tr>)}</tbody></table>{!clientes.length && <p>No se encontraron clientes.</p>}</div></section>}
  {tab === 'Productos' && <Productos productos={data.productos} onCambio={() => setRevision(r => r + 1)}/>}
  <div hidden={tab !== 'Reportes'}><Reportes pedidos={data.pedidos} clientes={data.clientes} actualizadoEn={actualizadoEn}/></div>
  <div hidden={tab !== 'Rutas'}><RutaEnCurso pedidos={data.pedidos} clientes={data.clientes}/><Rutas pendientes={data.pendientes} busy={busy} onActualizar={() => setRevision(r => r + 1)} onPlanificando={setPlanning}/></div>
  </>}{detail && <div className="modal" role="dialog" aria-modal="true" aria-label="Detalle del pedido"><section><button autoFocus onClick={()=>setDetail(null)}>Cerrar detalle</button><h2>Pedido #{detail.id}</h2><p>{detail.cliente_nombre} · {detail.estado}</p><p>{detail.direccion_despacho || 'Sin dirección de despacho informada'}</p>{detail.lineas.map((l,i)=><p key={i}>{l.cantidad_solicitada} × {l.producto_nombre} · {money(l.precio_unitario)}</p>)}<strong>Total: {money(detail.total)}</strong></section></div>}{editing && <FormCliente cliente={editing === 'nuevo' ? null : editing} clientes={data?.clientes || []} onClose={()=>setEditing(null)} onSaved={c=>{setAviso(editing === 'nuevo' ? `Cliente ${name(c)} creado (#${c.id}). Ya puede pedir por WhatsApp desde su número.` : `Cambios de ${name(c)} guardados.`); setEditing(null); setRevision(r=>r+1);}}/>}{creandoPedido && data && <NuevoPedido clientes={data.clientes} productos={data.productos} onClose={()=>setCreandoPedido(false)} onSaved={p=>{setAviso(`Pedido #${p.id} creado para ${p.cliente_nombre || 'el cliente'} por ${money(p.total)}. Quedó pendiente.`); setCreandoPedido(false); setRevision(r=>r+1);}}/>}</div></main></div>;
}
function OrderTable({ rows, view }) { return <div className="table"><table className="tabla-movil"><thead><tr><th>Pedido</th><th>Cliente</th><th>Comuna</th><th>Estado</th><th>Total</th><th>Detalle</th></tr></thead><tbody>{rows.map(p=><tr key={p.id}><td data-label="Pedido">#{p.id}</td><td data-label="Cliente">{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td><td data-label="Comuna">{p.comuna_nombre || '—'}</td><td data-label="Estado"><span className={`badge ${p.estado}`}>{p.estado.replaceAll('_',' ')}</span></td><td data-label="Total">{money(p.total)}</td><td className="celda-acciones"><button aria-label={`Ver pedido ${p.id}`} onClick={()=>view(p.id)}>Ver detalle</button></td></tr>)}</tbody></table>{!rows.length && <p>No hay pedidos para mostrar.</p>}</div>; }
createRoot(document.getElementById('root')).render(<App/>);
