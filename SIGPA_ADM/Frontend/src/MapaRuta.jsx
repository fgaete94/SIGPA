import React, { useEffect, useRef, useState } from 'react';
import { capasMapa, capasVisibles, claveEncuadre, sinCoordenadas, tieneCoordenadas } from './capasMapa';

// El mapa dibuja las paradas de la ruta con el orden de la lista: si se arrastra una parada, se actualiza.
// grupos: los viajes de la lista. foco: { id, n } para abrir una parada (n cambia con cada clic).
// Leaflet se carga recién al abrir un mapa, para que la página pública no lo descargue.
export default function MapaRuta({ grupos, foco }) {
  const [L, setL] = useState(null);
  const [fallo, setFallo] = useState(false);
  const [solo, setSolo] = useState(null);
  const lienzo = useRef(null);
  const mapa = useRef(null);
  const dibujo = useRef(null);
  const marcas = useRef(new Map());
  const encuadre = useRef(null);
  const atendido = useRef(null);

  const capas = capasMapa(grupos);
  const visibles = capasVisibles(capas, solo);
  const hayPuntos = capas.some(c => c.puntos.length > 0);
  const faltan = sinCoordenadas(capas);
  // grupos se arma de nuevo en cada render del padre: se redibuja solo si cambió lo que se ve.
  const firma = JSON.stringify(visibles);
  const claveVista = `${visibles.length}|${claveEncuadre(visibles)}`;

  useEffect(() => {
    let vivo = true;
    Promise.all([import('leaflet'), import('leaflet/dist/leaflet.css')])
      .then(([modulo]) => { if (vivo) setL(() => modulo.default); })
      .catch(() => { if (vivo) setFallo(true); });
    return () => { vivo = false; };
  }, []);

  useEffect(() => {
    if (!L || !lienzo.current) return undefined;
    // Sin zoom con la rueda: girarla sobre el mapa debe seguir desplazando la página.
    const m = L.map(lienzo.current, { scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>' }).addTo(m);
    dibujo.current = L.layerGroup().addTo(m);
    mapa.current = m;
    const observador = new ResizeObserver(() => m.invalidateSize());
    observador.observe(lienzo.current);
    return () => { observador.disconnect(); m.remove(); mapa.current = null; encuadre.current = null; };
  }, [L, hayPuntos]);

  useEffect(() => {
    if (!L || !mapa.current) return;
    dibujo.current.clearLayers();
    marcas.current.clear();
    const puntos = visibles.flatMap(c => c.puntos);
    for (const capa of visibles) {
      if (capa.puntos.length > 1) L.polyline(capa.puntos.map(p => [p.latitud, p.longitud]), { color: capa.color, weight: 3, opacity: .75, dashArray: '6 6' }).addTo(dibujo.current);
      for (const p of capa.puntos) {
        const icono = L.divIcon({ className: '', html: `<div class="marca-mapa ${p.tipo}" style="--color-viaje:${capa.color}">${p.numero}</div>`, iconSize: [26, 26], iconAnchor: [13 - p.desfase * 20, 13] });
        const marca = L.marker([p.latitud, p.longitud], { icon: icono, title: `${capa.etiqueta}, parada ${p.numero}`, zIndexOffset: 1000 - p.numero }).addTo(dibujo.current).bindPopup(contenidoPopup(capa, p, capas.length > 1));
        marcas.current.set(p.id, marca);
      }
    }
    if (puntos.length && encuadre.current !== claveVista) {
      mapa.current.fitBounds(L.latLngBounds(puntos.map(p => [p.latitud, p.longitud])).pad(0.08), { maxZoom: 16 });
      encuadre.current = claveVista;
    }
  }, [L, hayPuntos, firma, claveVista]);

  useEffect(() => {
    if (!foco || atendido.current === foco.n || !mapa.current) return;
    const marca = marcas.current.get(foco.id);
    if (!marca && solo !== null) { setSolo(null); return; } // Está en otro viaje: se vuelve a mostrar todos.
    atendido.current = foco.n;
    if (!marca) return;
    mapa.current.setView(marca.getLatLng(), 17);
    marca.openPopup();
    lienzo.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [L, hayPuntos, foco, firma, solo]);

  if (!hayPuntos) return <p className="notice no-print">Ninguna parada tiene coordenadas: no se puede mostrar el mapa.</p>;
  const todos = visibles.length === capas.length;
  return <div className="mapa-ruta no-print">
    {capas.length > 1 && <div className="mapa-filtros" role="group" aria-label="Viajes que se ven en el mapa">
      <button type="button" aria-pressed={todos} onClick={() => setSolo(null)}>Todos</button>
      {capas.map(c => <button type="button" key={c.clave} disabled={!c.puntos.length} aria-pressed={!todos && visibles[0].clave === c.clave} onClick={() => setSolo(c.clave)} style={{ '--color-viaje': c.color }}><i aria-hidden="true"/>{c.etiqueta}</button>)}
    </div>}
    {fallo ? <p role="alert" className="error">No se pudo cargar el mapa. Actualiza la página e inténtalo de nuevo.</p> : <div ref={lienzo} className="mapa-lienzo" role="group" aria-label="Mapa de la ruta"/>}
    <p className="muted mapa-nota">Verde: primera parada{capas.length > 1 ? ' de cada viaje' : ''}. Rojo: última. Las líneas unen las paradas en orden, no siguen las calles. El depósito no se muestra.</p>
    {faltan > 0 && <p className="notice">{faltan === 1 ? '1 parada no tiene coordenadas y no aparece' : `${faltan} paradas no tienen coordenadas y no aparecen`} en el mapa.</p>}
  </div>;
}

// Con textContent: el nombre y la dirección vienen de los clientes y no deben interpretarse como HTML.
function contenidoPopup(capa, p, conViajes) {
  const caja = document.createElement('div');
  const titulo = document.createElement('strong');
  titulo.textContent = `${conViajes ? `${capa.etiqueta} · ` : ''}Parada ${p.numero}`;
  const nombre = document.createElement('div');
  nombre.textContent = `${p.nombre} · #${p.id}`;
  caja.append(titulo, nombre);
  if (p.direccion) { const direccion = document.createElement('small'); direccion.textContent = p.direccion; caja.append(direccion); }
  return caja;
}

// El número de una parada de la lista. Con coordenadas, un clic la muestra en el mapa.
export function NumeroParada({ numero, parada, nombre, onVer }) {
  if (!tieneCoordenadas(parada)) return <b className="number">{numero}</b>;
  return <button type="button" className="number" title="Ver en el mapa" aria-label={`Ver en el mapa la parada ${numero}: ${nombre}`} onClick={onVer}>{numero}</button>;
}
