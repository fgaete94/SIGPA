import React, { useState } from 'react';
import { useCerrarAlSalir } from './cerrarAlSalir';
import { lunesDe } from './datosPeriodo';
import { aniosCalendario, etiquetaSemana, limitarMes, mesContiguo, semanasDelMes } from './datosRutas';
import { hoyChile } from './fechas';
import RutasEntregadas from './RutasEntregadas';

const DIAS_CALENDARIO = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie'];
const MESES_CALENDARIO = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];

// Pestaña Despachos: los despachos realizados de la semana elegida (por defecto, la actual).
export default function Despacho({ pedidos, clientes }) {
  const hoy = hoyChile();
  const [semana, setSemana] = useState(() => lunesDe(hoy));
  return <>
    <div className="filtro-despachos">
      <span id="etiqueta-semana">Semana</span>
      <CalendarioSemana semana={semana} hoy={hoy} onElegir={setSemana}/>
      {semana !== lunesDe(hoy) && <button type="button" className="enlace" onClick={() => setSemana(lunesDe(hoy))}>Volver a esta semana</button>}
    </div>
    <RutasEntregadas pedidos={pedidos} clientes={clientes} semana={semana} hoy={hoy}/>
  </>;
}

// Calendario con columnas fijas de lunes a viernes: cada fila es una semana y se elige completa.
function CalendarioSemana({ semana, hoy, onElegir }) {
  const [abierto, setAbierto] = useState(false);
  const [mes, setMes] = useState(semana.slice(0, 7));
  const caja = useCerrarAlSalir(abierto, () => setAbierto(false));
  const actual = lunesDe(hoy);
  const alternar = () => { setMes(semana.slice(0, 7)); setAbierto(a => !a); };
  return <div className="calendario-semana" ref={caja}>
    <button type="button" className="calendario-boton" aria-haspopup="dialog" aria-expanded={abierto} aria-labelledby="etiqueta-semana calendario-valor" onClick={alternar}>
      <span id="calendario-valor">{etiquetaSemana(semana)}</span><span aria-hidden="true">📅</span>
    </button>
    {abierto && <div className="calendario-panel" role="dialog" aria-label="Elegir semana">
      <div className="calendario-mes">
        <button type="button" aria-label="Mes anterior" onClick={() => setMes(m => mesContiguo(m, -1))}>‹</button>
        <span className="calendario-selectores">
          <select aria-label="Mes" value={mes.slice(5)} onChange={e => setMes(limitarMes(`${mes.slice(0, 4)}-${e.target.value}`, hoy))}>
            {MESES_CALENDARIO.map((nombre, i) => { const valor = String(i + 1).padStart(2, '0'); return <option key={valor} value={valor} disabled={`${mes.slice(0, 4)}-${valor}` > hoy.slice(0, 7)}>{nombre}</option>; })}
          </select>
          <select aria-label="Año" value={mes.slice(0, 4)} onChange={e => setMes(limitarMes(`${e.target.value}-${mes.slice(5)}`, hoy))}>
            {aniosCalendario(hoy, mes).map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </span>
        <button type="button" aria-label="Mes siguiente" disabled={mes >= hoy.slice(0, 7)} onClick={() => setMes(m => mesContiguo(m, 1))}>›</button>
      </div>
      <div className="calendario-fila calendario-dias" aria-hidden="true">{DIAS_CALENDARIO.map(d => <span key={d}>{d}</span>)}</div>
      {semanasDelMes(mes).map(s => <button type="button" key={s.lunes} className={`calendario-fila calendario-semana-fila${s.lunes === semana ? ' elegida' : ''}`} disabled={s.lunes > actual} aria-label={etiquetaSemana(s.lunes)} aria-pressed={s.lunes === semana} onClick={() => { onElegir(s.lunes); setAbierto(false); }}>
        {s.dias.map(d => <span key={d.dia} className={`${d.delMes ? '' : 'fuera-mes'}${d.dia === hoy ? ' es-hoy' : ''}`}>{d.numero}</span>)}
      </button>)}
    </div>}
  </div>;
}
