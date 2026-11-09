import { useEffect, useState } from 'react';
import { Analysis } from './Analysis';
import { getAssets, getHistory, type Asset, type HistoryResponse } from './api';
import { type PriceField } from './chart-data';
import { formatDate, formatWindow, historyWindow, PERIODS, type PeriodId } from './dates';
import { PriceChart } from './PriceChart';

type Load<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T };

function RequestError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="notice error" role="alert">
    <div><strong>No se pudo cargar</strong><p>{message}</p></div>
    <button className="secondary" type="button" onClick={onRetry}>Reintentar</button>
  </div>;
}

function History({ asset, period, field }: { asset: Asset; period: PeriodId; field: PriceField }) {
  const { first_date: first, last_date: last, asset_source_id: id } = asset;
  const [load, setLoad] = useState<Load<HistoryResponse>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!first || !last) return;
    const controller = new AbortController();
    getHistory(id, historyWindow(first, last, period), controller.signal).then((data) => {
      if (!controller.signal.aborted) setLoad({ status: 'ready', data });
    }, (error: Error) => {
      if (!controller.signal.aborted) setLoad({ status: 'error', message: error.message });
    });
    return () => controller.abort();
  }, [first, last, id, period, attempt]);

  if (!first || !last) return <output className="chart-empty">El catálogo no registra cobertura histórica para este activo.</output>;
  if (load.status === 'loading') return <output className="chart-empty">Cargando observaciones…</output>;
  if (load.status === 'error') return <RequestError message={load.message} onRetry={() => {
    setLoad({ status: 'loading' });
    setAttempt((value) => value + 1);
  }} />;
  const { bars } = load.data;
  if (!bars.length) return <output className="chart-empty">No hay observaciones para este período.</output>;

  return <>
    <PriceChart key={field} asset={load.data.asset} bars={bars} field={field} />
    <div className="chart-source muted">
      <span>Fuente {load.data.asset.provider_name} · {load.data.asset.source_id} · {bars.length} observaciones</span>
      <span>{bars[0].source_convention}{bars[0].rolling_method && ` · Rollover ${bars[0].rolling_method}`}</span>
    </div>
  </>;
}

function Workspace({ assets }: { assets: Asset[] }) {
  const [id, setId] = useState(() => assets.find(({ symbol }) => symbol === 'GC=F')?.asset_source_id ?? assets[0].asset_source_id);
  const [period, setPeriod] = useState<PeriodId>('1y');
  const [field, setField] = useState<PriceField>('close');
  const asset = assets.find(({ asset_source_id }) => asset_source_id === id)!;
  const { first_date: first, last_date: last } = asset;

  return <>
    <section className="overview" aria-label="Activo y cobertura">
      <div>
        <p className="eyebrow">Explorador de mercado</p>
        <div className="instrument"><h1>{asset.name}</h1><span className="ticker">{asset.symbol}</span></div>
        <p className="coverage muted">{asset.economic_unit} · {asset.calendar_code}{first && last && <>
          {' · '}Cobertura <time dateTime={first}>{formatDate(first)}</time> — <time dateTime={last}>{formatDate(last)}</time>
        </>}</p>
      </div>
      <div className="asset-select">
        <label htmlFor="asset">Activo financiero</label>
        <select id="asset" value={id} onChange={(e) => { setId(Number(e.target.value)); setField('close'); }}>
          {assets.map((item) => <option key={item.asset_source_id} value={item.asset_source_id}>{item.name} · {item.symbol}</option>)}
        </select>
      </div>
    </section>

    <section className="panel chart-panel" aria-labelledby="history-title">
      <div className="panel-header">
        <div>
          <p className="eyebrow">01 / Serie diaria</p>
          <h2 id="history-title">Histórico de precios</h2>
          {first && last && <p className="muted">Ventana del gráfico {formatWindow(historyWindow(first, last, period))}</p>}
        </div>
        <div className="field-select">
          <label htmlFor="price-field">Serie</label>
          <select id="price-field" value={field} onChange={(e) => setField(e.target.value as PriceField)}>
            <option value="close">Close</option><option value="adjusted_close">Adj Close</option>
          </select>
        </div>
      </div>
      <div className="period-row">
        <fieldset className="periods" aria-label="Período del gráfico">
          {PERIODS.map((item) => <button key={item.id} type="button" aria-pressed={item.id === period}
            onClick={() => setPeriod(item.id)}>{item.label}</button>)}
        </fieldset>
        <p className="muted">{field === 'close' ? 'Cierre sin ajuste' : 'Cierre ajustado informado por la fuente'}</p>
      </div>
      <History key={`${id}:${period}`} asset={asset} period={period} field={field} />
    </section>

    <section className="panel analysis-panel" aria-labelledby="analysis-title">
      <div className="panel-header">
        <div>
          <p className="eyebrow">02 / Investigación</p>
          <h2 id="analysis-title">Consulta financiera</h2>
          <p className="muted">Contrastá los precios de {asset.symbol} con la evidencia documental de un acontecimiento.</p>
        </div>
        <span className="badge">Proveedor simulado</span>
      </div>
      <Analysis key={id} asset={asset} />
    </section>
  </>;
}

export function App() {
  const [catalog, setCatalog] = useState<Load<Asset[]>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getAssets(controller.signal).then((data) => {
      if (!controller.signal.aborted) setCatalog({ status: 'ready', data });
    }, (error: Error) => {
      if (!controller.signal.aborted) setCatalog({ status: 'error', message: error.message });
    });
    return () => controller.abort();
  }, [attempt]);

  return <>
    <a className="skip-link" href="#contenido">Saltar al contenido</a>
    <header className="topbar">
      <div className="shell topbar-inner">
        <div className="brand">
          <svg aria-hidden="true" viewBox="0 0 32 32" fill="none"><path d="M4 18h6l4-8 5 13 4-7h5" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span>MarketPulse <em>AI</em></span>
        </div>
        <span className="local"><i aria-hidden="true" /> Explorador local</span>
      </div>
    </header>
    <main className="shell" id="contenido">
      {catalog.status === 'loading' && <output className="notice">Cargando catálogo de activos…</output>}
      {catalog.status === 'error' && <RequestError message={catalog.message} onRetry={() => {
        setCatalog({ status: 'loading' });
        setAttempt((value) => value + 1);
      }} />}
      {catalog.status === 'ready' && (catalog.data.length > 0
        ? <Workspace assets={catalog.data} />
        : <output className="notice">FastAPI no devolvió activos disponibles.</output>)}
    </main>
    <footer className="shell footer"><span>MarketPulse AI</span><span>Observaciones históricas · Investigación local</span></footer>
  </>;
}
