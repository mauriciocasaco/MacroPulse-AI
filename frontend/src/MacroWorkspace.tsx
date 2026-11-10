import { useEffect, useState } from 'react';
import { getMacroIndicators } from './macro-api';
import { MacroPlot } from './MacroPlot';
import { loadChartHistory, selectHistory, researchWindow, indicatorPresentation, chartPeriods, type ChartHistory, type ChartPeriod } from './macro-chart';
import { MacroResearch } from './MacroResearch';

const frequencyLabels = {
  daily: 'Daily reference',
  monthly: 'Monthly',
  quarterly: 'Quarterly',
  effective_date: 'Effective-date events',
};
const unitLabels: Record<string, string> = {
  'USD/EUR': 'USD per EUR', EUR: 'EUR', percent_per_annum: '% per year',
  percent_qoq: '% quarter on quarter', percent_yoy: '% year on year',
};

function unitLabel(unit: string, scale: number) {
  return unit === 'EUR' && scale === 6 ? 'Millions of EUR' : unitLabels[unit] ?? unit;
}

function MacroHistoryPanel({ history }: { history: ChartHistory }) {
  const { indicator, observations, anchor, updated_at } = history;
  const coverage = history.chunks.flatMap((chunk) => {
    if (!chunk.coverage_start || !chunk.coverage_end) return [];
    const start = chunk.coverage_start < history.start ? history.start : chunk.coverage_start;
    const end = chunk.coverage_end > history.end ? history.end : chunk.coverage_end;
    return start <= end ? [`${start} through ${end}`] : [];
  });
  const missing = observations.filter(({ value }) => value === null).length;
  const rows = anchor ? [anchor, ...observations] : observations;
  return <>
    <MacroPlot history={history} />
    {indicator.frequency === 'effective_date' && <p className="muted">
      {coverage.length
        ? `Verified capture coverage: ${coverage.join("; ")}. Lines begin at a known effective rate and stop at each coverage boundary.`
        : 'No verified interval of applicability for this selection. Points show effective events only.'}
    </p>}
    <p className="muted">
      {observations.length} observations{missing > 0 && ` · ${missing} marked missing or unavailable`}
      {updated_at && <> · Last captured <time dateTime={updated_at}>{new Date(updated_at).toISOString()}</time></>}
    </p>
    {rows.length > 0 && <details className="macro-observations">
      <summary>View observations</summary>
      <table aria-label={`${indicatorPresentation(indicator).label} observations`}>
        <thead><tr><th>Period</th><th>{unitLabel(indicator.unit, indicator.multiplier)}</th><th>Status</th></tr></thead>
        <tbody>{[...rows].reverse().map((row) => <tr key={row.period_raw}>
          <th scope="row" title={`${row.period_start} to ${row.period_end}`}>
            {row.period_raw}{row === anchor && ' (prior event)'}
          </th>
          <td>{row.value ?? '—'}</td>
          <td>{row.status_kind}{row.status_raw && ` (${row.status_raw})`}</td>
        </tr>)}</tbody>
      </table>
    </details>}
  </>;
}

export function MacroWorkspace({ analysisEnabled }: { analysisEnabled: boolean }) {
  const [indicators, setIndicators] = useState<Awaited<ReturnType<typeof getMacroIndicators>>>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [id, setId] = useState('eur-usd');
  const [period, setPeriod] = useState<ChartPeriod>('1Y');
  const [loaded, setLoaded] = useState<{ id: string; full: boolean; history: ChartHistory }>();
  const [historyFailed, setHistoryFailed] = useState(false);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const full = period === 'MAX';
  useEffect(() => {
    const controller = new AbortController();
    loadChartHistory(id, full, controller.signal).then((history) => {
      if (!controller.signal.aborted) setLoaded({ id, full, history });
    }, () => {
      if (!controller.signal.aborted) setHistoryFailed(true);
    });
    return () => controller.abort();
  }, [id, full, historyAttempt]);
  useEffect(() => {
    const controller = new AbortController();
    getMacroIndicators(controller.signal).then((data) => {
      if (!controller.signal.aborted) setIndicators(data);
    }, () => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [attempt]);

  const selected = indicators?.find((item) => item.id === id);
  const history = loaded?.id === id && loaded.full === full ? selectHistory(loaded.history, period) : undefined;
  const research = history ? researchWindow(history) : undefined;
  const presentation = selected ? indicatorPresentation(selected) : undefined;

  if (failed) return <div className="notice error" role="alert">
    <p>Unable to load the official indicator catalog.</p>
    <button type="button" onClick={() => { setFailed(false); setAttempt(attempt + 1); }}>Retry</button>
  </div>;
  if (!selected) return <output className="notice">Loading the official indicator catalog…</output>;

  return <>
    <section className="macro-intro" aria-labelledby="macro-title">
      <p className="eyebrow">Euro area research</p>
      <h1 id="macro-title">Macroeconomic evidence explorer</h1>
      <p className="muted">Chart official ECB and Eurostat series and trace each observation to its source.
        Local AI research pairs reproducible calculations with cited institutional context.</p>
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
      </div> : history ? <>
        <p className="muted">{history.start} – {history.end} · {period === 'MAX' ? 'All available history' : 'Window ends at the latest available reading'}</p>
        <MacroHistoryPanel history={history} />
      </> : <output className="chart-empty">Loading official observations…</output>}
      <p className="macro-source muted">
        Source <a href={selected.source_url}>{selected.attribution}</a> · <a href={selected.reuse_url}>Reuse terms</a>
        <br />{selected.title}
        <br /><span className="macro-series-key">{selected.series_key}</span>
        <br />{selected.geography.replaceAll('_', ' ')} · {selected.adjustment.replaceAll('_', ' ')}
      </p>
    </section>
    {history && research && <>
      {analysisEnabled && research.start !== history.start && <p className="muted">Research uses {research.start} to {research.end}; the chart shows the full history.</p>}
      <MacroResearch key={`${research.start}:${research.end}`} enabled={analysisEnabled} indicatorId={id} {...research} />
    </>}
  </>;
}
