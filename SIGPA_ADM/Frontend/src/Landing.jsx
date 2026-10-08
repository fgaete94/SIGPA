import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { BIDONES, COMUNAS, DISPENSADORES, PRECIOS_RESPALDO, PROMOS, REPARTO, SITIO, WHATSAPP_VISIBLE, enlaceWhatsApp, pesos } from './marca';
import { cargarPrecios, preciosGuardados } from './catalogo';
import { Qr, VentanaQr } from './QrWhatsApp';
import './landing.css';

const mensajePedido = producto => `Hola, quiero pedir: ${producto}`;
// En computador no hay WhatsApp a mano: se muestra un QR para escanear con el celular.
const esEscritorio = () => window.matchMedia?.('(hover: hover) and (pointer: fine)').matches;

// Burbujas decorativas de la sección de reparto: posición, tamaño, duración y desfase.
const BURBUJAS = [[6, 14, 13, 0], [18, 8, 10, 3], [31, 22, 16, 6], [47, 10, 12, 1], [62, 16, 15, 8], [74, 9, 11, 4], [86, 20, 17, 2], [94, 12, 13, 7]];

// Muestra con una transición los elementos [data-revelar] cuando entran en pantalla.
function useRevelar(raiz) {
  useLayoutEffect(() => {
    const nodo = raiz.current;
    if (!nodo || !('IntersectionObserver' in window)) return;
    nodo.classList.add('lp-js'); // sin JS todo queda visible
    const observador = new IntersectionObserver(entradas => entradas.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add('visible'); observador.unobserve(e.target); }
    }), { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    // Solo se ocultan los que se observan: lo que aparezca después (p. ej. al llegar los precios) se ve de inmediato.
    nodo.querySelectorAll('[data-revelar]').forEach(el => { el.classList.add('por-revelar'); observador.observe(el); });
    return () => observador.disconnect();
  }, [raiz]);
}

function Pedir({ producto, onQr, children }) {
  return <a className="lp-pedir" href={enlaceWhatsApp(mensajePedido(producto))} target="_blank" rel="noopener noreferrer"
    onClick={e => { if (esEscritorio()) { e.preventDefault(); onQr(producto); } }}>{children}</a>;
}

function WhatsAppIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2Zm0 18.2a8.2 8.2 0 0 1-4.2-1.2l-.3-.2-3 .8.8-2.9-.2-.3A8.2 8.2 0 1 1 12 20.2Zm4.5-6.1c-.2-.1-1.5-.7-1.7-.8-.2-.1-.4-.1-.6.1l-.8 1c-.1.2-.3.2-.5.1a6.7 6.7 0 0 1-3.3-2.9c-.2-.4.2-.4.7-1.3.1-.2 0-.3 0-.4l-.8-1.8c-.2-.5-.4-.4-.6-.4h-.5a1 1 0 0 0-.7.3 3 3 0 0 0-.9 2.2 5.2 5.2 0 0 0 1.1 2.7 11.9 11.9 0 0 0 4.6 4c1.7.7 2.4.8 3.2.7.5-.1 1.5-.6 1.8-1.2.2-.6.2-1.1.1-1.2l-.5-.3Z"/></svg>;
}

function Ola({ className }) {
  // La ola verde sobre azul de las piezas gráficas de Agua DM. Los trazos son más anchos
  // que el lienzo para poder mecerse de lado a lado sin dejar bordes vacíos.
  return <svg className={className} aria-hidden="true" viewBox="0 0 1440 140" preserveAspectRatio="none">
    <path className="ola-verde" d="M-160 72C80 12 300 8 560 50s480 70 1040-20v138H-160Z"/>
    <path className="ola-azul" d="M-160 100C100 44 320 42 600 78s460 50 1000-24v86H-160Z"/>
  </svg>;
}

function Precio({ valor, children }) {
  return <span className="lp-precio"><small>{children}</small>{pesos(valor)}</span>;
}

function Foto({ item, className }) {
  // Fotos de producto con fondo transparente: se ven completas sobre el pastel de la tarjeta.
  return <div className={`lp-foto${className ? ` ${className}` : ''}`}><img src={item.imagen} alt={item.nombre} loading="lazy"/></div>;
}

export default function Landing({ onAdmin }) {
  const hoy = new Date().getDay();
  const [qr, setQr] = useState(null);
  // Se muestra algo al tiro (lo guardado o el respaldo) y se reemplaza por lo de la base apenas responde.
  const [precios, setPrecios] = useState(() => preciosGuardados() || PRECIOS_RESPALDO);
  useEffect(() => {
    const controlador = new AbortController();
    cargarPrecios(controlador.signal).then(setPrecios).catch(() => { /* se mantienen los precios mostrados */ });
    return () => controlador.abort();
  }, []);
  const hay = producto => producto in precios;
  const cerrarQr = useCallback(() => setQr(null), []);
  const raiz = useRef(null);
  useRevelar(raiz);
  return <div className="lp" ref={raiz}>
    <header className="lp-top">
      <a className="lp-logo" href="#inicio" aria-label="Agua DM, inicio"><img src="/marca/logo.svg" alt="DM Agua Purificada" width="2320" height="2280"/></a>
      <nav aria-label="Secciones"><a href="#precios">Precios</a><a href="#reparto">Días de reparto</a><a href="#contacto">Contacto</a></nav>
      <a className="lp-btn lp-btn-wsp lp-top-cta" href={enlaceWhatsApp()} target="_blank" rel="noopener noreferrer"><WhatsAppIcon/>Pedir</a>
      <button type="button" className="lp-admin" onClick={onAdmin}>Admin</button>
    </header>

    <main>
      <section className="lp-hero" id="inicio">
        <div className="lp-hero-texto">
          <p className="lp-kicker" style={{ '--d': 0 }}>Agua purificada · Despacho gratuito</p>
          <h1 style={{ '--d': 1 }}>Tu agua, en la puerta de tu casa.</h1>
          <p className="lp-lead" style={{ '--d': 2 }}>Llevamos bidones de 12 y 20 litros de lunes a viernes a {COMUNAS.slice(0, -1).join(', ')} y {COMUNAS.at(-1)}.</p>
          <div className="lp-acciones" style={{ '--d': 3 }}>
            <a className="lp-btn lp-btn-wsp" href={enlaceWhatsApp('Hola, quiero hacer un pedido de agua')} target="_blank" rel="noopener noreferrer"><WhatsAppIcon/>Pedir por WhatsApp</a>
            <a className="lp-btn lp-btn-linea" href="#precios">Ver precios</a>
          </div>
        </div>
        <div className="lp-hero-visual">
          <img className="lp-hero-foto" src="/marca/productos.webp" alt="Dispensador USB, bidones de 12 y 20 litros y dispensador básico de Agua DM" width="1400" height="804" fetchPriority="high"/>
        </div>
      </section>

      <Ola className="lp-ola"/>

      <section className="lp-reparto" id="reparto" aria-labelledby="reparto-titulo">
        <div className="lp-burbujas" aria-hidden="true">{BURBUJAS.map(([x, s, t, d]) => <span key={x} style={{ '--x': `${x}%`, '--s': `${s}px`, '--t': `${t}s`, '--d': `-${d}s` }}/>)}</div>
        <div className="lp-reparto-texto">
          <h2 id="reparto-titulo" data-revelar>¿Qué día pasamos por tu sector?</h2>
          <ol className="lp-semana">
            {REPARTO.map((r, i) => <li key={r.dia} className={r.dia === hoy ? 'hoy' : ''} data-revelar style={{ '--i': i }}>
              <span className="lp-dia">{r.nombre}{r.dia === hoy && <em>Hoy</em>}</span>
              <span className="lp-zona">{r.zona}</span>
            </li>)}
          </ol>
          <p className="lp-nota" data-revelar style={{ '--i': REPARTO.length }}>¿Estás en Reñaca o en otro sector? <a href={enlaceWhatsApp('Hola, ¿qué día reparten en mi sector?')} target="_blank" rel="noopener noreferrer">Escríbenos</a> y te confirmamos el día.</p>
        </div>
        <figure className="lp-repartidor" data-revelar style={{ '--i': 2 }}>
          <img src="/marca/repartidor.webp" alt="Repartidor de Agua DM con un bidón de 20 litros al hombro" width="780" height="1180" loading="lazy"/>
          <figcaption>El despacho a domicilio no tiene costo.</figcaption>
        </figure>
      </section>

      <section className="lp-precios" id="precios" aria-labelledby="precios-titulo">
        <h2 id="precios-titulo" data-revelar>Precios</h2>
        <p className="lp-sub" data-revelar style={{ '--i': 1 }}>Valores referenciales: te los confirmamos al tomar tu pedido.</p>

        <h3 data-revelar>Bidones</h3>
        <div className="lp-grid lp-grid-2">
          {BIDONES.filter(b => b.precios.some(([, p]) => hay(p))).map((b, i) => <article key={b.id} className="lp-card lp-card-foto" data-revelar style={{ '--i': i }}>
            <Foto item={b}/>
            <div><h4>{b.nombre}</h4>{b.precios.filter(([, p]) => hay(p)).map(([t, p]) => <Precio key={t} valor={precios[p]}>{t}</Precio>)}
              <Pedir producto={b.nombre} onQr={setQr}>Pedir {b.nombre} →</Pedir></div>
          </article>)}
        </div>

        <h3 data-revelar>Promociones <span>incluyen despacho</span></h3>
        <div className="lp-grid lp-grid-2">
          {PROMOS.filter(p => hay(p.producto)).map((p, i) => <article key={p.id} className="lp-card lp-card-pack" data-revelar style={{ '--i': i }}>
            <Foto item={p}/>
            <h4>{p.nombre}</h4><p>{p.detalle}</p><Precio valor={precios[p.producto]}/>
            <Pedir producto={p.nombre} onQr={setQr}>Pedir esta promoción →</Pedir>
          </article>)}
        </div>

        <h3 data-revelar>Dispensadores</h3>
        <div className="lp-grid lp-grid-2">
          {DISPENSADORES.filter(d => hay(d.producto)).map((d, i) => <article key={d.id} className="lp-card lp-card-foto" data-revelar style={{ '--i': i }}>
            <Foto item={d}/>
            <div><h4>{d.nombre}</h4><p>{d.detalle}</p><Precio valor={precios[d.producto]}/>
              <Pedir producto={d.nombre} onQr={setQr}>Pedir {d.nombre} →</Pedir></div>
          </article>)}
        </div>
      </section>

      <section className="lp-contacto" id="contacto" aria-labelledby="contacto-titulo">
        <h2 id="contacto-titulo" data-revelar>Pide por WhatsApp</h2>
        <p data-revelar style={{ '--i': 1 }}>Cuéntanos qué necesitas y tu dirección. Te confirmamos el día de reparto de tu sector.</p>
        <div className="lp-contacto-cta" data-revelar style={{ '--i': 2 }}><a className="lp-numero" href={enlaceWhatsApp()} target="_blank" rel="noopener noreferrer"><WhatsAppIcon/>{WHATSAPP_VISIBLE}</a><figure className="lp-qr-contacto"><Qr texto={enlaceWhatsApp('Hola, quiero hacer un pedido de agua')} titulo="Código QR para escribirnos por WhatsApp"/><figcaption>Escanéalo con tu celular</figcaption></figure></div>
      </section>
    </main>

    <footer className="lp-pie">
      <img src="/marca/logo-blanco.svg" alt="DM Agua Purificada" width="2320" height="2280" loading="lazy"/>
      <div><strong>Zonas de reparto</strong><p>{COMUNAS.join(' · ')}</p></div>
      <div><strong>Contacto</strong><p><a href={enlaceWhatsApp()} target="_blank" rel="noopener noreferrer">{WHATSAPP_VISIBLE}</a><br/><a href={`https://${SITIO}`} target="_blank" rel="noopener noreferrer">{SITIO}</a></p></div>
      <button type="button" className="lp-admin" onClick={onAdmin}>Acceso administrativo</button>
    </footer>
    {qr && <VentanaQr producto={qr} mensaje={mensajePedido(qr)} enlace={enlaceWhatsApp(mensajePedido(qr))} onClose={cerrarQr}/>}
  </div>;
}
