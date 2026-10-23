import React, { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { ETIQUETA_ESTADO, despachables } from './estados';
import { fechaCorta } from './fechas';
import HojaDeRuta from './HojaDeRuta';
import { enlaceComoLlegar, enlaceWhatsApp, rutaEnCurso, textoRuta } from './reparto';
import { useArrastre } from './Arrastre';
import { moverA } from './route';

// Historia #85: la ruta guardada en la base, para coordinar al repartidor (imprimir, WhatsApp o copiar).
export default function RutaEnCurso({ pedidos, clientes, productos, onActualizar, onEntregar }) {
  const { paradas: guardadas, repetidos, sinUbicacion } = useMemo(() => rutaEnCurso(pedidos, clientes), [pedidos, clientes]);
  // Reordenar (arrastrando) solo cambia esta vista: impresión y texto. Para guardar otro orden se vuelve a calcular la ruta (#87).
  const [paradas, setParadas] = useState(guardadas);
  const [copiado, setCopiado] = useState('');
  // Copia de las paradas al abrir la hoja de ruta: una recarga de datos no la cambia mientras está abierta.
  const [hoja, setHoja] = useState(null);
  useEffect(() => setParadas(guardadas), [guardadas]);
  const manual = paradas.some((p, i) => p.id !== guardadas[i]?.id);
  const texto = textoRuta(paradas, fechaCorta(new Date().toISOString()));
  const { propsFila, propsManilla } = useArrastre(paradas.length, (desde, hasta) => setParadas(ps => moverA(ps, desde, hasta)));

  async function copiar() {
    try { await navigator.clipboard.writeText(texto); setCopiado('Ruta copiada. Pégala en el chat del repartidor.'); }
    catch { setCopiado('No se pudo copiar automáticamente. Usa "Enviar por WhatsApp" o imprime la ruta.'); }
  }

  // #70: cuando el camión sale, todos los confirmados de la ruta pasan a "en despacho" (uno por uno en el backend).
  const porDespachar = despachables(paradas);
  const [despachando, setDespachando] = useState(false);
  const [resultado, setResultado] = useState(null);
  async function despacharRuta() {
    if (!window.confirm(`¿Marcar ${porDespachar.length === 1 ? 'el pedido confirmado' : `los ${porDespachar.length} pedidos confirmados`} de la ruta como "en despacho"? Se puede volver atrás pedido por pedido en la pestaña Pedidos.`)) return;
    setDespachando(true); setResultado(null);
    const fallidos = [];
    for (const id of porDespachar) {
      try { await api(`/pedidos/${id}/estado`, { method: 'POST', body: JSON.stringify({ estado: 'en_despacho' }) }); }
      catch (e) { fallidos.push(`#${id}: ${e.message}`); }
    }
    setDespachando(false);
    setResultado({ listos: porDespachar.length - fallidos.length, fallidos });
    onActualizar();
  }

  return <section className="print-route">
    <div className="title">
      <div><h2>Ruta en curso</h2><p className="muted">{paradas.length === 1 ? '1 parada' : `${paradas.length} paradas`} · pedidos confirmados o en despacho, en el orden guardado al planificar</p></div>
      {paradas.length > 0 && <div className="acciones no-print">
        <button onClick={() => setHoja(paradas)}>Imprimir hoja de ruta</button>
        <a className="boton" href={enlaceWhatsApp(texto)} target="_blank" rel="noopener noreferrer">Enviar por WhatsApp</a>
        <button onClick={copiar}>Copiar ruta</button>
        {porDespachar.length > 0 && <button className="primary" disabled={despachando} onClick={despacharRuta}>{despachando ? 'Marcando…' : `Salió a reparto (${porDespachar.length})`}</button>}
      </div>}
    </div>
    {copiado && <p role="status" className="ok no-print">{copiado}</p>}
    {resultado && <div role="status" className={`${resultado.fallidos.length ? 'error' : 'ok'} no-print`}>
      {resultado.listos > 0 && <p>{resultado.listos === 1 ? '1 pedido quedó' : `${resultado.listos} pedidos quedaron`} en despacho.</p>}
      {resultado.fallidos.length > 0 && <><p>No se pudieron cambiar:</p><ul>{resultado.fallidos.map(f => <li key={f}>{f}</li>)}</ul></>}
    </div>}
    {repetidos.length > 0 && <p className="notice">Hay pedidos de más de una planificación con el mismo número de parada ({repetidos.join(', ')}). Revisa el orden antes de entregarla al repartidor o vuelve a planificar esos pedidos.</p>}
    {manual && <p className="notice">Orden cambiado solo para imprimir o enviar. No se guarda en el servidor: al actualizar los datos vuelve al orden planificado. <button className="no-print enlace" onClick={() => setParadas(guardadas)}>Volver al orden planificado</button></p>}
    {paradas.map((p, i) => {
      const enlace = enlaceComoLlegar(p);
      const fila = propsFila(i);
      return <div key={p.id} {...fila} className={`stop ${fila.className}`}>
        <button {...propsManilla(i, p.cliente_nombre || `pedido ${p.id}`)} className="manilla no-print">⠿</button>
        <b className="number">{i + 1}</b>
        <span>
          <strong>{p.cliente_nombre || `Cliente ${p.cliente_id}`} · #{p.id}</strong>
          <span className="etiquetas"><span className={`badge ${p.estado}`}>{ETIQUETA_ESTADO[p.estado]}</span></span>
          <small>{p.direccion || 'Sin dirección registrada'}{p.comuna_nombre && !p.direccion?.includes(p.comuna_nombre) && ` · ${p.comuna_nombre}`}{p.cliente_telefono && ` · Tel: ${p.cliente_telefono}`}</small>
        </span>
        <div className="no-print acciones-parada">
          {enlace && <a className="boton" href={enlace} target="_blank" rel="noopener noreferrer">Cómo llegar</a>}
          <button aria-label={`Registrar entrega de la parada ${i + 1}`} onClick={() => onEntregar(p.id)}>Entregado</button>
        </div>
      </div>;
    })}
    {!paradas.length && <p>No hay una ruta en curso. Selecciona pedidos en "Preparar ruta" y planifícala.</p>}
    {hoja && <HojaDeRuta paradas={hoja} clientes={clientes} productos={productos} onClose={() => setHoja(null)}/>}
    {sinUbicacion > 0 && <p className="notice no-print">{sinUbicacion === 1 ? '1 pedido confirmado quedó' : `${sinUbicacion} pedidos confirmados quedaron`} fuera de la ruta por dirección por revisar. Corrige su ubicación más abajo y vuelve a planificar.</p>}
  </section>;
}
