import { useEffect, useState } from 'react';
import { getMacroIndicators } from './macro-api';
import { MacroPlot } from './MacroPlot';
import { unitLabel, loadChartHistory, selectHistory, indicatorPresentation, chartPeriods, type ChartHistory, type ChartPeriod } from './macro-chart';
import { MacroResearch } from './MacroResearch';

const frequencyLabels = {
  daily: 'Daily reference',
  monthly: 'Monthly',
  quarterly: 'Quarterly',
  effective_date: 'Effective-date events',
};
export function MacroWorkspace({ analysisEnabled, publicDemo = false }: { analysisEnabled: boolean; publicDemo?: boolean }) {
  const [indicators, setIndicators] = useState<Awaited<ReturnType<typeof getMacroIndicators>>>();
  const [researchV2, setResearchV2] = useState(false);
  const [sitekey, setSitekey] = useState('');
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [id, setId] = useState('eur-usd');
  const [period, setPeriod] = useState<ChartPeriod>('1Y');
  const [loaded, setLoaded] = useState<{ id: string; history: ChartHistory }>();
  const [historyFailed, setHistoryFailed] = useState(false);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    loadChartHistory(id, controller.signal).then((history) => {
      if (!controller.signal.aborted) setLoaded({ id, history });
    }, () => {
      if (!controller.signal.aborted) setHistoryFailed(true);
    });
    return () => controller.abort();
  }, [id, historyAttempt]);
  useEffect(() => {
    const controller = new AbortController();
    getMacroIndicators(controller.signal, setResearchV2, publicDemo ? setSitekey : undefined).then((data) => {
      if (!controller.signal.aborted) setIndicators(data);
    }, () => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [attempt, publicDemo]);

  const selected = indicators?.find((item) => item.id === id);
  const history = loaded?.id === id ? selectHistory(loaded.history, period) : undefined;
  const presentation = selected ? indicatorPresentation(selected) : undefined;

  if (failed) return <div className="notice error" role="alert">
    <p>Unable to load the official indicator catalog.</p>
    <button type="button" onClick={() => { setFailed(false); setAttempt(attempt + 1); }}>Retry</button>
  </div>;
  if (!selected) return <output className="notice">Loading the official indicator catalog…</output>;

  return <>
    <section className="macro-intro" aria-labelledby="macro-title">
      <p className="eyebrow">Euro area research</p>
      <h1 id="macro-title">AI-Powered Macroeconomic Intelligence</h1>
      <p className="muted">Powered by LLMs, RAG, LangChain and LangGraph.</p>
    </section>
    <section className="overview" aria-label="Macroeconomic indicator">
      <div>
        <h2>{presentation?.label}</h2>
        <p className="coverage muted">{unitLabel(selected.unit, selected.multiplier)} · {frequencyLabels[selected.frequency]}</p>
      </div>
      <div className="asset-select">
        <label htmlFor="macro-indicator">Indicator</label>
        <select id="macro-indicator" value={id} onChange={(event) => { setHistoryFailed(false); setId(event.target.value); }}>
          {indicators?.map((item) => <option key={item.id} value={item.id}>{indicatorPresentation(item).label}</option>)}
        </select>
      </div>
    </section>
    <section className="panel" aria-labelledby="macro-history-title">
      <h2 id="macro-history-title">Official history</h2>
      <fieldset className="macro-presets" aria-label="History period">
        {chartPeriods.map((value) => <button key={value} type="button" aria-pressed={period === value}
          onClick={() => { if (historyFailed) setHistoryAttempt((attempt) => attempt + 1); setHistoryFailed(false); setPeriod(value); }}>{value}</button>)}
      </fieldset>
      {historyFailed ? <div className="notice error" role="alert">
        <p>Unable to load official observations.</p>
        <button type="button" onClick={() => { setHistoryFailed(false); setHistoryAttempt((value) => value + 1); }}>Retry</button>
      </div> : history ? <MacroPlot history={history} /> : <output className="chart-empty">Loading official observations…</output>}
      <p className="macro-source muted">
        Source <a href={selected.source_url}>{selected.attribution}</a> · <a href={selected.reuse_url}>Reuse terms</a>
      </p>
    </section>
    <MacroResearch enabled={publicDemo ? Boolean(sitekey) : analysisEnabled} indicatorId={id} indicators={indicators ?? []}
      publicDemo={publicDemo} sitekey={sitekey}
      researchV2Available={researchV2 && ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname)}
      onIndicatorChange={(next) => { setHistoryFailed(false); setId(next); }} />
  </>;
}
