import Lenis from 'lenis';
import { gsap } from 'gsap';
import { createContext, useContext, useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';

const reducedMotion = '(prefers-reduced-motion: reduce)';
const MotionContext = createContext<{
  enabled: boolean;
  reduced: boolean;
  navigate: (event: MouseEvent<HTMLAnchorElement>) => void;
}>({
  enabled: false,
  reduced: true,
  navigate: (_event: MouseEvent<HTMLAnchorElement>) => undefined,
});

export function MotionProvider({ paused, children }: { paused: boolean; children: ReactNode }) {
  const [reduced, setReduced] = useState(() => window.matchMedia?.(reducedMotion).matches ?? true);
  const scroll = useRef<((target: HTMLElement) => void) | null>(null);
  const enabled = !paused && !reduced;
  useEffect(() => {
    const query = window.matchMedia?.(reducedMotion);
    const change = () => setReduced(query?.matches ?? true);
    query?.addEventListener('change', change);
    return () => query?.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    if (!enabled) return;
    // Lenis handles explicit anchor transitions; touch, wheel and keyboard scrolling stay native.
    const instance = new Lenis({ smoothWheel: false, syncTouch: false, autoRaf: false, stopInertiaOnNavigate: true });
    const tick = () => {
      instance.raf(performance.now());
      if (instance.isScrolling !== 'smooth') gsap.ticker.remove(tick);
    };
    scroll.current = (target) => {
      instance.raf(performance.now());
      instance.scrollTo(target);
      if (instance.isScrolling === 'smooth') gsap.ticker.add(tick);
    };
    return () => {
      gsap.ticker.remove(tick);
      instance.destroy();
      scroll.current = null;
    };
  }, [enabled]);

  function navigate(event: MouseEvent<HTMLAnchorElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const hash = event.currentTarget.hash;
    const target = document.getElementById(hash.slice(1));
    if (!target) return;
    event.preventDefault();
    if (window.location.hash !== hash) window.history.pushState(null, '', hash);
    target.focus({ preventScroll: true });
    if (scroll.current) scroll.current(target);
    else target.scrollIntoView?.({ block: 'start', behavior: 'instant' });
  }

  return <MotionContext.Provider value={{ enabled, reduced, navigate }}>
    <div data-motion={enabled ? 'on' : 'off'}>{children}</div>
  </MotionContext.Provider>;
}

export const useMotion = () => useContext(MotionContext);
