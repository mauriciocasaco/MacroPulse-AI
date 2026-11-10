import { useEffect, useState } from 'react';
import { MacroWorkspace } from './MacroWorkspace';
import { usePwaUpdate } from './pwa';

export function App({
  macroDemoEnabled = import.meta.env.MARKETPULSE_DEMO_MACRO_ENABLED === true,
  macroAiEnabled = import.meta.env.MARKETPULSE_MACRO_AI_ENABLED === true,
}: { macroDemoEnabled?: boolean; macroAiEnabled?: boolean } = {}) {
  const publicDemo = import.meta.env.MODE === 'public-demo';
  const macroAvailable = publicDemo
    ? macroDemoEnabled
    : ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
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

  return <>
    <a className="skip-link" href="#contenido">Skip to content</a>
    <header className="topbar">
      <div className="shell topbar-inner">
        <div className="brand">
          <svg aria-hidden="true" viewBox="0 0 32 32" fill="none"><path d="M4 18h6l4-8 5 13 4-7h5" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span>MacroPulse <em>AI</em></span>
        </div>
        <span className="local"><i aria-hidden="true" /> {publicDemo ? 'Public demo' : 'Local workspace'}</span>
      </div>
    </header>
    <main className="shell" id="contenido">
      {updateReady && <output className="notice">An update is ready. Close all MacroPulse AI tabs and windows, then reopen the app.</output>}
      {online ? macroAvailable ? <MacroWorkspace key={attempt}
        analysisEnabled={!publicDemo && macroAiEnabled} />
        : <output className="notice">MacroPulse research is unavailable in this environment.</output>
        : <output className="notice">You are offline. Live macroeconomic data is unavailable.</output>}
    </main>
    <footer className="shell footer"><span>MacroPulse AI</span><span>Historical macroeconomic research</span></footer>
  </>;
}
