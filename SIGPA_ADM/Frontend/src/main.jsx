import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { login, logout, onSessionExpired, restoreSession } from './auth';
import { api } from './api';
import { cambiosCliente, clienteForm } from './clientes';
import Landing from './Landing';
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
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    Promise.all(['/pedidos', '/clientes', '/rutas/pedidos-pendientes'].map(p => api(p, { signal: controller.signal })))
      .then(([pedidos, clientes, pendientes]) => { if (![pedidos, clientes, pendientes].every(Array.isArray)) throw new Error('Formato de datos inesperado.'); setData({ pedidos, clientes, pendientes }); })
      .catch(e => { if (e.name !== 'AbortError') { setError(e.message); setData(null); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [revision]);
  async function view(id) { setDetail(null); try { setDetail(await api(`/pedidos/${id}`)); } catch(e) { setError(e.message); } }
  const pedidos = (data?.pedidos || []).filter(p => (!status || p.estado === status) && `${p.id} ${p.cliente_nombre || ''} ${p.comuna_nombre || ''}`.toLowerCase().includes(query.toLowerCase()));
  const clientes = (data?.clientes || []).filter(c => `${name(c)} ${c.telefono || ''}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="shell"><aside><Brand blanco/><div className="nav-label">Operación</div>{['Resumen','Pedidos','Clientes','Rutas'].map(t => <button key={t} className={tab === t ? 'active' : ''} onClick={() => { setTab(t); setQuery(''); setStatus(''); }}>{t}</button>)}<div className="account"><small>{session.user.email}</small><button onClick={async () => { await logout(); onLogout(); }}>Cerrar sesión</button></div></aside><main className="workspace"><header><span>Panel administrativo</span><span className="badge">Agua DM</span></header><div className="content"><div className="title"><div><span className="eyebrow">Centro de operaciones</span><h1>{tab}</h1><p>Consulta y organiza la información de SIGPA.</p></div><button disabled={busy || planning} onClick={() => setRevision(r => r + 1)}>Actualizar datos</button></div>{error && <p role="alert" className="error">{error}</p>}{busy && <p role="status">Cargando información…</p>}{data && <>
  {tab === 'Resumen' && <><div className="kpis">{[['Pedidos registrados',data.pedidos.length],['Pendientes',data.pedidos.filter(p=>p.estado==='pendiente').length],['Confirmados',data.pedidos.filter(p=>p.estado==='confirmado').length],['Clientes activos',data.clientes.filter(c=>c.activo).length]].map(([label,n]) => <article key={label}><span>{label}</span><strong>{n}</strong></article>)}</div><p className="muted">Totales de todos los registros disponibles, sin filtro de fecha.</p><section><h2>Últimos pedidos</h2><OrderTable rows={data.pedidos.slice(0,8)} view={view}/></section></>}
  {tab === 'Pedidos' && <section><div className="filters"><input aria-label="Buscar pedidos" placeholder="Buscar cliente, comuna o ID…" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="Estado del pedido" value={status} onChange={e=>setStatus(e.target.value)}><option value="">Todos los estados</option>{['pendiente','confirmado','en_despacho','entregado','cancelado'].map(s=><option key={s}>{s}</option>)}</select></div><OrderTable rows={pedidos} view={view}/></section>}
  {tab === 'Clientes' && <section><input aria-label="Buscar clientes" placeholder="Buscar nombre o teléfono…" value={query} onChange={e=>setQuery(e.target.value)}/><div className="table"><table><thead><tr><th>Cliente</th><th>Teléfono</th><th>Dirección</th><th>Tipo</th><th>Día de reparto</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{clientes.map(c=><tr key={c.id}><td>{name(c)}</td><td>{c.telefono || '—'}</td><td>{c.direccion || 'Sin dirección'}</td><td>{c.tipo_cliente_nombre || '—'}</td><td>{c.dia_reparto || 'Sin asignar'}</td><td>{c.activo ? 'Activo' : 'Inactivo'}</td><td><button aria-label={`Editar a ${name(c)}`} onClick={()=>setEditing(c)}>Editar</button></td></tr>)}</tbody></table>{!clientes.length && <p>No se encontraron clientes.</p>}</div></section>}
  <div hidden={tab !== 'Rutas'}><Rutas pendientes={data.pendientes} busy={busy} onActualizar={() => setRevision(r => r + 1)} onPlanificando={setPlanning}/></div>
  </>}{detail && <div className="modal" role="dialog" aria-modal="true" aria-label="Detalle del pedido"><section><button autoFocus onClick={()=>setDetail(null)}>Cerrar detalle</button><h2>Pedido #{detail.id}</h2><p>{detail.cliente_nombre} · {detail.estado}</p><p>{detail.direccion_despacho || 'Sin dirección de despacho informada'}</p>{detail.lineas.map((l,i)=><p key={i}>{l.cantidad_solicitada} × {l.producto_nombre} · {money(l.precio_unitario)}</p>)}<strong>Total: {money(detail.total)}</strong></section></div>}{editing && <EditClient client={editing} onClose={()=>setEditing(null)} onSaved={c=>{setData(d=>({...d, clientes:d.clientes.map(x=>x.id===c.id ? c : x)})); setEditing(null); setRevision(r=>r+1);}}/>}</div></main></div>;
}
function EditClient({ client, onClose, onSaved }) {
  const [form, setForm] = useState(() => clienteForm(client));
  const [tipos, setTipos] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api('/tipos-cliente', { signal: controller.signal }).then(setTipos).catch(e => { if (e.name !== 'AbortError') setError(`No se pudieron cargar los tipos de cliente: ${e.message}`); });
    return () => controller.abort();
  }, []);
  const field = (key, label, props) => <label>{label}<input value={form[key]} onChange={e=>setForm(f=>({...f,[key]:e.target.value}))} {...props}/></label>;
  const check = (key, label) => <label className="check"><input type="checkbox" checked={form[key]} onChange={e=>setForm(f=>({...f,[key]:e.target.checked}))}/>{label}</label>;
  async function submit(e) {
    e.preventDefault();
    const { cambios, error } = cambiosCliente(client, form);
    if (error) return setError(error);
    if (!Object.keys(cambios).length) return onClose();
    setBusy(true); setError('');
    try { onSaved(await api(`/clientes/${client.id}`, { method: 'PATCH', body: JSON.stringify(cambios) })); }
    catch(e) { setError(e.message); setBusy(false); }
  }
  return <div className="modal" role="dialog" aria-modal="true" aria-label="Editar cliente"><section><form onSubmit={submit}><span className="eyebrow">Cliente #{client.id}</span><h2>Editar cliente</h2><p>{name(client)}</p>
    <div className="campos">{field('nombre', 'Nombre', { required: true, autoFocus: true })}{field('apellido_paterno', 'Apellido paterno')}{field('apellido_materno', 'Apellido materno')}{field('telefono', 'Teléfono', { required: true, inputMode: 'tel' })}</div>{field('direccion', 'Dirección')}
    <label>Tipo de cliente<select value={form.tipo_cliente_id} onChange={e=>setForm(f=>({...f,tipo_cliente_id:e.target.value}))}><option value="">Sin asignar</option>{client.tipo_cliente_id != null && !tipos.some(t=>t.id===client.tipo_cliente_id) && <option value={client.tipo_cliente_id}>{client.tipo_cliente_nombre || `Tipo ${client.tipo_cliente_id}`}</option>}{tipos.map(t=><option key={t.id} value={t.id}>{t.nombre}</option>)}</select></label>
    {check('activo', 'Cliente activo')}{check('opt_out_whatsapp', 'No desea recibir mensajes por WhatsApp')}
    {form.direccion.trim() !== (client.direccion || '') && <p className="notice">Cambiar la dirección no actualiza las coordenadas guardadas del cliente.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" onClick={onClose} disabled={busy}>Cancelar</button><button className="primary" disabled={busy}>{busy ? 'Guardando…' : 'Guardar cambios'}</button></div></form></section></div>;
}
function OrderTable({ rows, view }) { return <div className="table"><table><thead><tr><th>Pedido</th><th>Cliente</th><th>Comuna</th><th>Estado</th><th>Total</th><th>Detalle</th></tr></thead><tbody>{rows.map(p=><tr key={p.id}><td>#{p.id}</td><td>{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td><td>{p.comuna_nombre || '—'}</td><td><span className={`badge ${p.estado}`}>{p.estado.replaceAll('_',' ')}</span></td><td>{money(p.total)}</td><td><button onClick={()=>view(p.id)}>Ver</button></td></tr>)}</tbody></table>{!rows.length && <p>No hay pedidos para mostrar.</p>}</div>; }
createRoot(document.getElementById('root')).render(<App/>);
