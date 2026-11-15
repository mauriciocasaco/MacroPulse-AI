import { useEffect, useRef, useState } from 'react';
import { RippleButton } from './RippleButton';
import { useMotion } from './motion';
import { drawStill } from './ocean-grid';
import type { mountOcean } from './ocean';

export function MacroHero({ available, paused, toggleMotion }: {
  available: boolean; paused: boolean; toggleMotion: () => void;
}) {
  const { enabled, reduced } = useMotion();
  const still = useRef<HTMLDivElement>(null);
  const live = useRef<HTMLDivElement>(null);
  const ocean = useRef<ReturnType<typeof mountOcean>>(null);
  const [rendering, setRendering] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (still.current && typeof ResizeObserver !== 'undefined') return drawStill(still.current);
  }, []);

  useEffect(() => {
    const container = live.current;
    if (reduced || unavailable || !container || typeof ResizeObserver === 'undefined' || typeof IntersectionObserver === 'undefined') return;
    let cancelled = false;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      observer.disconnect();
      void import('./ocean').then(({ mountOcean }) => {
        if (cancelled) return;
        ocean.current = mountOcean(container, () => setUnavailable(true));
        if (ocean.current) setRendering(true);
        else setUnavailable(true);
      }).catch(() => { if (!cancelled) setUnavailable(true); });
    });
    observer.observe(container);
    return () => {
      cancelled = true;
      observer.disconnect();
      ocean.current?.dispose();
      ocean.current = null;
      setRendering(false);
    };
  }, [reduced, unavailable]);

  useEffect(() => {
    ocean.current?.setActive(enabled && rendering && !unavailable);
  }, [enabled, rendering, unavailable]);

  const oceanMode = rendering && !reduced && !unavailable ? (paused ? 'paused' : 'webgl') : 'static';

  return <section className="hero" id="top" tabIndex={-1} aria-labelledby="hero-title">
    <div className="ocean" aria-hidden="true" data-mode={oceanMode}>
      <div ref={still} className="ocean-still" />
      <div ref={live} className="ocean-live" />
    </div>
    <div className="hero-shade" aria-hidden="true" />
    <div className="hero-content shell">
      <p className="eyebrow hero-eyebrow">Macro intelligence</p>
      <h1 id="hero-title">Macro <em>Analyst</em></h1>
      <p className="hero-intro">Explore and analyze macro data with RAG, LangGraph, LangChain, Gemini API LLM.</p>
      {available && <div className="hero-actions">
        <RippleButton className="action primary" href="#data">Explore Macro Data <span aria-hidden="true">↓</span></RippleButton>
        <RippleButton className="action secondary" href="#ask">Ask LLM Model <span aria-hidden="true">↗</span></RippleButton>
      </div>}
    </div>
    <div className="hero-baseline shell">
      {!reduced && <RippleButton className="motion-control" onClick={toggleMotion}>
        {paused ? 'Resume motion' : 'Pause motion'}
      </RippleButton>}
    </div>
  </section>;
}
