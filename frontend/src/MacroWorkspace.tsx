import { useEffect, useState, type FormEvent } from 'react';
import { getMacroHistory, getMacroIndicators, type MacroHistory, type MacroObservation } from './macro-api';

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
const dayMs = 86_400_000;

function unitLabel(unit: string, scale: number) {
  return unit === 'EUR' && scale === 6 ? 'Millions of EUR' : unitLabels[unit] ?? unit;
}

function initialWindow() {
  const now = new Date();
  return {
    start: new Date(now.getTime() - 365 * dayMs).toISOString().slice(0, 10),
    end: now.toISOString().slice(0, 10),
  };
}

function pointDay(row: MacroObservation) {
  return row.effective_date ?? row.period_end;
}

function MacroPlot({ history }: { history: MacroHistory }) {
  const { observations, anchor, coverage_start: start, coverage_end: end } = history;
  const points = observations.flatMap((row) => row.value === null ? [] : [row]);
  const readings = anchor?.value != null ? [anchor, ...points] : points;
  if (!readings.length) {
    return <output className="chart-empty">No numeric observations in this period.</output>;
  }

  const minimumDate = Date.parse(history.start);
  const maximumDate = Math.max(Date.parse(history.end), ...points.map((row) => Date.parse(pointDay(row))));
  const x = (day: string) => maximumDate === minimumDate ? 360
    : 76 + ((Date.parse(day) - minimumDate) / (maximumDate - minimumDate)) * 602;
  const values = readings.map((row) => Number(row.value));
  const lower = Math.min(...values);
  const upper = Math.max(...values);
  const y = (value: number) => upper === lower ? 103 : 178 - ((value - lower) / (upper - lower)) * 150;
  const format = new Intl.NumberFormat('en', { maximumSignificantDigits: 6 });
  const effective = history.indicator.frequency === 'effective_date';
  const events = anchor ? [anchor, ...observations] : observations;
  const segments = effective && start && end ? events.flatMap((row, index) => {
    const first = pointDay(row) < start ? start : pointDay(row);
    const next = events[index + 1];
    const last = next && pointDay(next) < end ? pointDay(next) : end;
    return row.value === null || first >= last ? []
      : [{ first, last, value: Number(row.value), key: row.period_raw }];
  }) : [];

  return <svg className="macro-plot" viewBox="0 0 720 225"
    aria-label={effective ? 'Deposit rate events and verified periods of applicability' : 'Official macro observations by period'}>
    {[upper, lower].filter((value, i, all) => all.indexOf(value) === i).map((value) =>
      <g key={value}>
        <line className="macro-gridline" x1="76" x2="678" y1={y(value)} y2={y(value)} />
        <text x="69" y={y(value) + 4} textAnchor="end">{format.format(value)}</text>
      </g>,
    )}
    <line className="macro-gridline" x1="76" x2="678" y1="188" y2="188" />
    {segments.map(({ first, last, value, key }) =>
      <line key={key} className="macro-segment" x1={x(first)} x2={x(last)} y1={y(value)} y2={y(value)} />,
    )}
    {points.map((row) =>
      <circle key={row.period_raw} className="macro-dot" cx={x(pointDay(row))} cy={y(Number(row.value))}
        r={points.length > 150 ? 1.6 : 3.5}>
        <title>{row.period_raw} · {row.value} · {row.status_kind}</title>
      </circle>,
    )}
    <text x="76" y="211">{history.start}</text>
    <text x="678" y="211" textAnchor="end">{new Date(maximumDate).toISOString().slice(0, 10)}</text>
  </svg>;
}

function MacroHistoryPanel({ id, start, end }: { id: string; start: string; end: string }) {
  const [history, setHistory] = useState<MacroHistory>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getMacroHistory(id, start, end, controller.signal).then((result) => {
      if (!controller.signal.aborted) setHistory(result);
    }, () => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [id, start, end, attempt]);

  if (failed) return <div className="notice error" role="alert">
    <p>Unable to load official observations.</p>
    <button type="button" onClick={() => { setFailed(false); setAttempt(attempt + 1); }}>Retry</button>
  </div>;
  if (!history) return <output className="chart-empty">Loading official observations…</output>;
  const { indicator, observations, anchor, coverage_start, coverage_end, updated_at } = history;
  const missing = observations.filter(({ value }) => value === null).length;
  const rows = anchor ? [anchor, ...observations] : observations;
  return <>
    <MacroPlot history={history} />
    {indicator.frequency === 'effective_date' && <p className="muted">
      {coverage_start && coverage_end
        ? `Verified capture coverage from ${coverage_start} through ${coverage_end}. Lines begin at a known effective rate and stop at the coverage boundary.`
        : 'No verified interval of applicability for this selection. Points show effective events only.'}
    </p>}
    <p className="muted">
      {observations.length} observations{missing > 0 && ` · ${missing} marked missing or unavailable`}
      {updated_at && <> · Last captured <time dateTime={updated_at}>{new Date(updated_at).toISOString()}</time></>}
    </p>
    {rows.length > 0 && <details className="macro-observations">
      <summary>View observations</summary>
      <table>
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

export function MacroWorkspace() {
  const [indicators, setIndicators] = useState<Awaited<ReturnType<typeof getMacroIndicators>>>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [id, setId] = useState('eur-usd');
  const [window, setWindow] = useState(initialWindow);
  const [draft, setDraft] = useState(window);
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
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWindow(draft);
  }

  if (failed) return <div className="notice error" role="alert">
    <p>Unable to load the official indicator catalog.</p>
    <button type="button" onClick={() => { setFailed(false); setAttempt(attempt + 1); }}>Retry</button>
  </div>;
  if (!selected) return <output className="notice">Loading the official indicator catalog…</output>;

  return <>
    <section className="overview" aria-label="Macroeconomic indicator">
      <div>
        <p className="eyebrow">Macro explorer</p>
        <h1>{selected.title}</h1>
        <p className="coverage muted">{unitLabel(selected.unit, selected.multiplier)} · {frequencyLabels[selected.frequency]}</p>
      </div>
      <div className="asset-select">
        <label htmlFor="macro-indicator">Indicator</label>
        <select id="macro-indicator" value={id} onChange={(event) => setId(event.target.value)}>
          {indicators?.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </div>
    </section>
    <section className="panel" aria-labelledby="macro-history-title">
      <h2 id="macro-history-title">Official history</h2>
      <form className="macro-window" onSubmit={submit}>
        <label>Start <input required type="date" max={draft.end}
          value={draft.start} onChange={(event) => setDraft({ ...draft, start: event.target.value })} /></label>
        <label>End <input required type="date" min={draft.start}
          value={draft.end} onChange={(event) => setDraft({ ...draft, end: event.target.value })} /></label>
        <button type="submit">Apply</button>
      </form>
      <MacroHistoryPanel key={`${id}:${window.start}:${window.end}`} id={id} {...window} />
      <p className="macro-source muted">
        Source <a href={selected.source_url}>{selected.attribution}</a> · <a href={selected.reuse_url}>Reuse terms</a>
        <br /><span className="macro-series-key">{selected.series_key}</span>
        <br />{selected.geography.replaceAll('_', ' ')} · {selected.adjustment.replaceAll('_', ' ')}
      </p>
    </section>
    <section className="panel" aria-labelledby="macro-research-title">
      <h2 id="macro-research-title">Ask about macro data</h2>
      <p className="muted">Natural-language research is unavailable in this build.</p>
      <label className="question">Research question <textarea disabled rows={2} /></label>
    </section>
  </>;
}
