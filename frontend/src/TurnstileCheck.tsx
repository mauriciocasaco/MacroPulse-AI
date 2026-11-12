import { useEffect, useRef, useState } from 'react';

type Turnstile = {
  render: (target: HTMLElement, options: {
    sitekey: string; action: string; callback: (token: string) => void;
    'error-callback': () => void; 'expired-callback': () => void; 'timeout-callback': () => void;
  }) => string;
  remove: (id: string) => void;
};
declare global {
  interface Window { turnstile?: Turnstile }
}

let loading: Promise<Turnstile> | undefined;

function loadTurnstile(): Promise<Turnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise<Turnstile>((resolve, reject) => {
    const script = document.createElement('script');
    const fail = () => {
      window.clearTimeout(deadline);
      script.remove();
      reject(new Error('Turnstile unavailable'));
    };
    const deadline = window.setTimeout(fail, 12_000);
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async = true;
    script.onload = () => {
      if (!window.turnstile) { fail(); return; }
      window.clearTimeout(deadline);
      resolve(window.turnstile);
    };
    script.onerror = fail;
    document.head.append(script);
  }).catch((cause: unknown) => {
    loading = undefined;
    throw cause;
  });
  return loading;
}

export function TurnstileCheck({ sitekey, onToken }: { sitekey: string; onToken: (token: string) => void }) {
  const target = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    let widget: string | undefined;
    let api: Turnstile | undefined;
    loadTurnstile().then((loaded) => {
      if (!active || !target.current) return;
      api = loaded;
      widget = api.render(target.current, {
        sitekey, action: 'research',
        callback: (token) => { if (active) { setFailed(false); onToken(token); } },
        'expired-callback': () => { if (active) onToken(''); },
        'error-callback': () => { if (active) { onToken(''); setFailed(true); } },
        'timeout-callback': () => { if (active) { onToken(''); setFailed(true); } },
      });
    }).catch(() => { if (active) setFailed(true); });
    return () => {
      active = false;
      if (widget !== undefined) api?.remove(widget);
    };
  }, [sitekey, attempt, onToken]);

  return <>
    <div ref={target} aria-label="Turnstile verification" />
    {failed && <p role="alert" className="muted">Turnstile is currently unavailable.{' '}
      <button type="button" onClick={() => { setFailed(false); setAttempt((value) => value + 1); }}>Retry verification</button>
    </p>}
  </>;
}
