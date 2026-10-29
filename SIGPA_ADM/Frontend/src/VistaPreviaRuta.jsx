import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useArrastre } from './Arrastre';
import { encabezadoViaje } from './viajes';
import { cambiada, cambiarViaje, enlacePunto, moverParada, puedeConfirmar, reponer, restablecer, sacar, viajesVista } from './vistaPrevia';

const paradasTexto = n => (n === 1 ? '1 parada' : `${n} paradas`);

// Historia #87: ruta propuesta por n8n, todavía sin guardar. Se puede reordenar o sacar paradas por
// factores que el cálculo no conoce (horarios, un accidente, una calle cortada) y recién después confirmar.
// Las paradas se reordenan arrastrándolas dentro de su viaje y se pueden pasar a otro viaje. Se muestra sobre toda la página (portal): cambiar de pestaña no la oculta.
export default function VistaPreviaRuta({ estado, onCambiar, onCancelar, onConfirmar, onRecalcular, guardando, error }) {
  const titulo = useRef(null);
  useEffect(() => { titulo.current?.focus(); }, []);
  const modificada = cambiada(estado);
  const { paradas, fuera, sinResolver } = estado;
  const { grupos: viajes, problemas } = viajesVista(estado);
  const conViajes = viajes.filter(v => v.viaje != null);
  const numeroDe = new Map(conViajes.map(v => [v.viaje, v.numero]));

  return createPortal(<div className="modal" role="dialog" aria-modal="true" aria-labelledby="vista-previa-titulo"><section className="vista-previa">
    <span className="eyebrow">Vista previa · todavía no se guarda nada</span>
    <h2 id="vista-previa-titulo" tabIndex={-1} ref={titulo}>Ruta propuesta</h2>
    <p>Orden calculado por el servicio de rutas. Ajústalo si hay algo que el cálculo no considera, como horarios del cliente, un accidente o una calle cortada: <strong>mantén presionada una parada y arrástrala</strong> a su nuevo lugar{conViajes.length > 1 && ' dentro de su viaje. Cada viaje es una carga del camión: para cambiar una parada de viaje, elígelo en su lista'}. Después confirma para guardarlo.</p>
    {modificada && <p className="notice">Cambiaste la ruta propuesta. <button type="button" className="enlace" onClick={() => onCambiar(restablecer(estado))}>Volver al orden propuesto</button></p>}

    <h3>{paradasTexto(paradas.length)}{viajes.length > 1 && ` en ${viajes.length} viajes`}</h3>
    {!paradas.length && <p className="muted">No queda ninguna parada en la ruta.</p>}
    {viajes.map(v => <ViajePrevia key={v.viaje ?? 'sin-viaje'} viaje={v} viajes={conViajes} guardando={guardando}
      onMover={(desde, hasta) => onCambiar(moverParada(estado, v.viaje, desde, hasta))} onSacar={id => onCambiar(sacar(estado, id))}
      onCambiarViaje={(id, viaje) => onCambiar(cambiarViaje(estado, id, viaje))}/>)}
    {problemas.length > 0 && <div role="alert" className="error"><strong>Así no se puede confirmar:</strong><ul>{problemas.map(p => <li key={p}>{p}</li>)}</ul></div>}

    {fuera.length > 0 && <>
      <h3>Fuera de esta ruta</h3>
      <p className="muted">No se guardan: siguen pendientes y vuelven a "Preparar ruta" para la próxima.</p>
      <ul className="paradas-previa fuera">
        {fuera.map(p => <li key={p.pedido_id}>
          <span><strong>{p.cliente_nombre || `Pedido #${p.pedido_id}`} · #{p.pedido_id}</strong><small>{p.direccion_texto || 'Sin dirección registrada'}</small></span>
          <span className="acciones-parada"><button type="button" disabled={guardando} onClick={() => onCambiar(reponer(estado, p.pedido_id))}>{numeroDe.has(p.viaje) ? `Volver al viaje ${numeroDe.get(p.viaje)}` : 'Volver a la ruta'}</button></span>
        </li>)}
      </ul>
    </>}

    {sinResolver.length > 0 && <div className="error">
      <h3>{sinResolver.length === 1 ? '1 dirección no se pudo ubicar' : `${sinResolver.length} direcciones no se pudieron ubicar`}</h3>
      <ul>{sinResolver.map(p => <li key={p.pedido_id}>#{p.pedido_id} · {p.cliente_nombre}: {p.motivo}</li>)}</ul>
      <p>Al confirmar quedan confirmadas pero fuera de la ruta, marcadas "Dirección por revisar": corrige su ubicación y vuelve a calcular.</p>
    </div>}

    {error && <div role="alert" className="error"><strong>{error.mensaje}</strong>{error.detalles?.length > 0 && <ul>{error.detalles.map(d => <li key={d}>{d}</li>)}</ul>}</div>}
    <div className="actions">
      <button type="button" onClick={onCancelar} disabled={guardando}>Cancelar</button>
      {error && <button type="button" onClick={onRecalcular} disabled={guardando}>Volver a calcular</button>}
      <button type="button" className="primary" disabled={guardando || !puedeConfirmar(estado)} onClick={onConfirmar}>
        {guardando ? 'Guardando…' : paradas.length ? `Confirmar ruta (${paradasTexto(paradas.length)})` : 'Confirmar y marcar para revisión'}
      </button>
    </div>
  </section></div>, document.body);
}

// Un viaje: su encabezado (número, peso y dónde termina) y sus paradas, que se ordenan arrastrando
// dentro del viaje. La numeración parte de 1 en cada viaje, como orden_entrega.
function ViajePrevia({ viaje, viajes, guardando, onMover, onSacar, onCambiarViaje }) {
  const { paradas } = viaje;
  const { propsFila, propsManilla } = useArrastre(paradas.length, onMover, { deshabilitado: guardando });
  const titulo = encabezadoViaje({ ...viaje, viaje: viaje.numero });
  const prefijo = viaje.numero != null ? `del viaje ${viaje.numero} ` : '';
  return <>
    {titulo && <h4 className="viaje-titulo">{titulo}</h4>}
    <ol className="paradas-previa">
      {paradas.map((p, i) => <li key={p.pedido_id} {...propsFila(i)}>
        <button {...propsManilla(i, p.cliente_nombre || `pedido ${p.pedido_id}`)}>⠿</button>
        <b className="number">{i + 1}</b>
        <span><strong>{p.cliente_nombre || `Pedido #${p.pedido_id}`} · #{p.pedido_id}</strong><small>{p.direccion_texto || 'Sin dirección registrada'}{Number.isFinite(p.peso_kg) && ` · ${p.peso_kg} kg`}</small></span>
        <span className="acciones-parada">
          {viajes.length > 1 && viaje.viaje != null && <select aria-label={`Viaje del pedido ${p.pedido_id}`} value={viaje.viaje} disabled={guardando} onChange={e => onCambiarViaje(p.pedido_id, Number(e.target.value))}>
            {viajes.map(v => <option key={v.viaje} value={v.viaje}>Viaje {v.numero}</option>)}
          </select>}
          <a className="boton" href={enlacePunto(p)} target="_blank" rel="noopener noreferrer" aria-label={`Ver en el mapa la parada ${i + 1} ${prefijo}`.trim()}>Mapa</a>
          <button type="button" aria-label={`Sacar de la ruta el pedido ${p.pedido_id}`} disabled={guardando} onClick={() => onSacar(p.pedido_id)}>Sacar</button>
        </span>
      </li>)}
    </ol>
  </>;
}
