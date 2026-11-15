import { useEffect, useState } from 'react';
import { MacroHero } from './MacroHero';
import { MotionProvider, useMotion } from './motion';
import { MacroWorkspace } from './MacroWorkspace';
import { usePwaUpdate } from './pwa';

export function App({
  macroDemoEnabled = import.meta.env.MARKETPULSE_DEMO_MACRO_ENABLED !== false,
  macroAiEnabled = import.meta.env.MARKETPULSE_MACRO_AI_ENABLED === true,
}: { macroDemoEnabled?: boolean; macroAiEnabled?: boolean } = {}) {
  const publicDemo = import.meta.env.MODE === 'public-demo';
  const launcher = import.meta.env.MODE === 'windows-launcher';
  const lanDev = import.meta.env.DEV && import.meta.env.MARKETPULSE_LAN_DEV === 'true';
  const safariUrl = import.meta.env.MARKETPULSE_SAFARI_URL;
  const isLoopback = ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
  const macroAvailable = publicDemo
    ? macroDemoEnabled
    : isLoopback || lanDev || (launcher && Boolean(safariUrl));
  const [paused, setPaused] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [online, setOnline] = useState(() => navigator.onLine);
  const updateReady = usePwaUpdate();

  useEffect(() => {
    const reconnect = () => {
      setOnline(true);
      setAttempt((value) => value + 1);
    };
    const disconnect = () => setOnline(false);
    window.addEventListener('online', reconnect);
    window.addEventListener('offline', disconnect);
    return () => {
      window.removeEventListener('online', reconnect);
      window.removeEventListener('offline', disconnect);
    };
  }, []);

  const available = online && macroAvailable;
  return <MotionProvider paused={paused}>
    <WorkspaceHeader available={available} />
    <main id="contenido" tabIndex={-1}>
      <MacroHero available={available} paused={paused} toggleMotion={() => setPaused((value) => !value)} />
      <div className="workspace shell">
        {launcher && isLoopback && safariUrl && <p className="notice">iPhone Safari <a href={safariUrl}>{safariUrl}</a></p>}
        {updateReady && <output className="notice">An update is ready. Close all MacroPulse AI tabs and windows, then reopen the app.</output>}
        {available ? <MacroWorkspace key={attempt}
          analysisEnabled={!publicDemo && macroAiEnabled} publicDemo={publicDemo} />
          : <output className="notice">{online ? 'MacroPulse research is unavailable in this environment.'
            : 'You are offline. Live macroeconomic data is unavailable.'}</output>}
      </div>
    </main>
    <WorkspaceFooter />
  </MotionProvider>;
}

function WorkspaceHeader({ available }: { available: boolean }) {
  const { navigate } = useMotion();
  return <>
    <a className="skip-link" href="#contenido" onClick={navigate}>Skip to content</a>
    <header className="topbar">
      <div className="shell topbar-inner">
        <a className="brand" href="#top" onClick={navigate} aria-label="MacroPulse AI home">
          <svg aria-hidden="true" viewBox="0 0 32 32" fill="none"><path d="M4 18h6l4-8 5 13 4-7h5" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span>MacroPulse <em>AI</em></span>
        </a>
        {available && <nav aria-label="Primary">
          <a href="#data" onClick={navigate}>Macro Data</a>
          <a href="#ask" onClick={navigate}>Ask LLM <span aria-hidden="true">↗</span></a>
        </nav>}
      </div>
    </header>
  </>;
}

function WorkspaceFooter() {
  const { navigate } = useMotion();
  return <footer className="footer"><div className="shell">
    <span>MacroPulse AI</span>
    <a href="#top" onClick={navigate}>Back to top <span aria-hidden="true">↑</span></a>
  </div></footer>;
}
