import { useEffect, useState } from 'react';
import { getMacroIndicators } from './macro-api';
import { MacroPlot } from './MacroPlot';
import { unitLabel, numeric, loadChartHistory, selectHistory, indicatorPresentation, chartPeriods, type ChartHistory, type ChartPeriod } from './macro-chart';
import { RippleButton } from './RippleButton';
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
  const localResearchV2 = researchV2 && ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
  const [sitekey, setSitekey] = useState('');
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [id, setId] = useState('eur-usd');
  const [period, setPeriod] = useState<ChartPeriod>('1Y');
  const [loaded, setLoaded] = useState<{ id: string; history: ChartHistory }>();
  const [historyFailed, setHistoryFailed] = useState(false);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const selected = indicators?.find((item) => item.id === id);
  const ready = !publicDemo || selected !== undefined;
  const availableFrom = selected?.available_from ?? undefined;
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    loadChartHistory(id, controller.signal, availableFrom).then((history) => {
      if (!controller.signal.aborted) setLoaded({ id, history });
    }, () => {
      if (!controller.signal.aborted) setHistoryFailed(true);
    });
    return () => controller.abort();
  }, [id, historyAttempt, ready, availableFrom]);
  useEffect(() => {
    const controller = new AbortController();
    getMacroIndicators(controller.signal, setResearchV2, publicDemo ? setSitekey : undefined).then((data) => {
      if (!controller.signal.aborted) setIndicators(data);
    }, () => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [attempt, publicDemo]);

  const history = loaded?.id === id ? selectHistory(loaded.history, period) : undefined;
  const presentation = selected ? indicatorPresentation(selected) : undefined;

  const latest = history?.observations.filter(numeric).at(-1);
  function changeIndicator(next: string) {
    if (next === id) return;
    setLoaded(undefined);
    setHistoryFailed(false);
    setId(next);
  }

  return <>
    <section className="content-section" id="data" tabIndex={-1} aria-labelledby="data-title">
      <div className="section-intro">
        <p className="eyebrow">Macro data</p>
        <h2 id="data-title">Signals over time</h2>
        <p className="muted">Explore five official ECB and Eurostat indicators.</p>
      </div>
      {failed ? <div className="notice error" role="alert">
        <p>Unable to load the official indicator catalog.</p>
        <RippleButton onClick={() => { setFailed(false); setAttempt((value) => value + 1); }}>Retry</RippleButton>
      </div> : selected && presentation ? <div className="panel data-panel">
        <div className="overview">
          <div>
            <h3 className="indicator-label">{presentation.label}</h3>
            {latest && <p className="chart-latest">
              <strong>{latest.value}</strong> <span>{unitLabel(selected.unit, selected.multiplier)}</span>
            </p>}
            <p className="coverage muted">{latest ? <>{latest.period_raw} · </> : <>{unitLabel(selected.unit, selected.multiplier)} · </>}
              {frequencyLabels[selected.frequency]}</p>
          </div>
          <div className="asset-select">
            <label htmlFor="macro-indicator">Indicator</label>
            <select id="macro-indicator" value={id} onChange={(event) => changeIndicator(event.target.value)}>
              {indicators?.map((item) => <option key={item.id} value={item.id}>{indicatorPresentation(item).label}</option>)}
            </select>
          </div>
        </div>
        <h3 className="sr-only">Official history</h3>
        <fieldset className="macro-presets" aria-label="History period">
          {(publicDemo && availableFrom ? [...chartPeriods, 'All' as const] : chartPeriods).map((value) =>
            <RippleButton key={value} aria-pressed={period === value}
              onClick={() => {
                if (historyFailed) setHistoryAttempt((attempt) => attempt + 1);
                setHistoryFailed(false);
                setPeriod(value);
              }}>{value}</RippleButton>)}
        </fieldset>
        {historyFailed ? <div className="notice error" role="alert">
          <p>Unable to load official observations.</p>
          <RippleButton onClick={() => { setHistoryFailed(false); setHistoryAttempt((value) => value + 1); }}>Retry</RippleButton>
        </div> : history ? <MacroPlot history={history} /> : <output className="chart-empty">Loading official observations…</output>}
        <div className="chart-footer muted">
          <p className="macro-source">Source <a href={selected.source_url}>{selected.attribution}</a> · <a href={selected.reuse_url}>Reuse terms</a></p>
          {history?.updated_at && <p>Updated <time dateTime={history.updated_at}>{history.updated_at}</time></p>}
        </div>
      </div> : <output className="notice">Loading the official indicator catalog…</output>}
    </section>
    <section className="content-section ask-section" id="ask" tabIndex={-1} aria-labelledby="ask-title">
      <div className="section-intro">
        <p className="eyebrow">Research</p>
        <h2 id="ask-title">Ask LLM Model</h2>
        <p className="muted">Ask about a macroeconomic indicator or historical event and inspect the evidence behind the answer.</p>
      </div>
      {selected ? <MacroResearch enabled={publicDemo ? Boolean(sitekey) : analysisEnabled} indicatorId={id} indicators={indicators ?? []}
        publicDemo={publicDemo} sitekey={sitekey} researchV2Available={localResearchV2}
        onIndicatorChange={changeIndicator} />
        : <output className="notice">{failed ? 'Research requires the official indicator catalog.'
          : 'Loading research indicators…'}</output>}
    </section>
  </>;
}
