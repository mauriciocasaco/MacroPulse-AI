import { useEffect, useRef, useState } from 'react';
import { askMacroAnalysis, type MacroAnalysis, type MacroIndicator, type ObservedChange, type ResearchEvidence } from './macro-api';
import { QuestionComposer, type QuestionSubmission } from './QuestionComposer';

const displayDate = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
});
const measureLabels: Record<string, string> = {
  'm3-stock': 'Broad money (M3)', 'deposit-rate': 'ECB deposit rate',
  'real-gdp-qoq': 'Quarterly real GDP growth', 'hicp-yoy': 'Annual euro area inflation',
};
const categoryLabels = {
  'official observation': 'Official data',
  'calculated from official observations': 'Calculated change',
  'documentary context': 'Documentary context',
  'institutional interpretation': 'Institutional interpretation',
  'proxy context': 'Proxy context',
};

function ChangeHeadline({ change }: { change: ObservedChange }) {
  const { direction, value, unit, indicator_id: id } = change;
  const sign = direction === 'up' ? '+' : direction === 'down' ? '−' : '';
  const arrow = direction === 'up' ? '↑' : direction === 'down' ? '↓' : '→';
  const unitLabel = unit === 'percent' ? '%' : ' pp';
  const tiny = value === '0.00' && direction !== 'flat';
  const label = id === 'eur-usd'
    ? direction === 'flat' ? 'The euro was unchanged against the US dollar'
      : direction === 'up' ? 'The euro gained value against the US dollar' : 'The euro lost value against the US dollar'
    : `${measureLabels[id]} ${direction === 'up' ? 'rose' : direction === 'down' ? 'fell' : 'was unchanged'}`;
  return <div className="research-change">
    <div className={`research-change-value ${direction}`}>
      {tiny ? `<0.01${unitLabel}` : `${sign}${value.replace(/^-/, '')}${unitLabel}`} <span aria-hidden="true">{arrow}</span>
    </div>
    <p>{label}</p>
    <p className="muted">
      <time dateTime={change.first_observed_on}>{displayDate.format(new Date(change.first_observed_on))}</time>
      {' – '}
      <time dateTime={change.last_observed_on}>{displayDate.format(new Date(change.last_observed_on))}</time>
      {unit === 'percentage_points' && ' · percentage points'}
    </p>
  </div>;
}

function uniqueDocuments(items: ResearchEvidence[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (item.kind !== 'document' || seen.has(item.url)) return false;
    seen.add(item.url);
    return true;
  });
}

export function MacroResearch({ enabled, indicatorId, indicators, onIndicatorChange, researchV2Available = false }: {
  enabled: boolean; indicatorId: string; indicators: MacroIndicator[]; onIndicatorChange: (id: string) => void;
  researchV2Available?: boolean;
}) {
  const [experimental, setExperimental] = useState(false);
  const researchV2 = experimental && researchV2Available;
  const [request, setRequest] = useState<{
    indicatorId: string; controller: AbortController; pending: boolean; failed: boolean; result?: MacroAnalysis;
  }>();
  const active = useRef<AbortController>(null);
  useEffect(() => () => active.current?.abort(), [indicatorId, enabled, researchV2]);
  const current = enabled && request?.indicatorId === indicatorId && !request.controller.signal.aborted ? request : undefined;
  const pending = current?.pending ?? false;
  const failed = current?.failed ?? false;
  const result = current?.result;

  function submit(submission: QuestionSubmission) {
    if (!enabled || pending || submission.indicatorId !== indicatorId) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setRequest({ indicatorId, controller, pending: true, failed: false });
    askMacroAnalysis(submission.question, submission.start, submission.end, controller.signal,
      { indicator_id: submission.indicatorId, event_id: submission.eventId,
        ...(researchV2 ? { research_version: 'v2' as const } : {}) }).then((data) => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? { ...current, result: data } : current);
    }, () => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? { ...current, failed: true } : current);
    }).finally(() => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? { ...current, pending: false } : current);
    });
  }

  const documents = result?.research_v2 ? result.evidence.filter(({ kind }) => kind === 'document')
    : uniqueDocuments(result?.evidence ?? []);
  const series = result?.evidence.filter(({ kind }) => kind === 'series') ?? [];
  return <section className="panel" aria-labelledby="macro-research-title">
    <h2 id="macro-research-title">RAG-Powered Macro Research</h2>
    {enabled && researchV2Available && <div>
      <label>
        <input type="checkbox" checked={researchV2} disabled={pending}
          onChange={(event) => { setExperimental(event.target.checked); setRequest(undefined); }} />
        {' '}Use experimental Research v2
      </label>
      <p className="muted">
        Research v2 sends your original question to Google Gemini. Keep secrets and credentials out of your question.
      </p>
    </div>}
    {enabled ? <QuestionComposer enabled={enabled} pending={pending} indicators={indicators}
      indicatorId={indicatorId} onIndicatorChange={onIndicatorChange} onSubmit={submit} />
      : <p className="muted">AI research is available only in an enabled local workspace.</p>}
    {pending && <output className="notice">Checking official evidence…</output>}
    {failed && <p role="alert" className="notice error">Local macro research is currently unavailable.</p>}
    {result && <div className="analysis-result" aria-live="polite">
      {result.observed_changes?.map((change) => <ChangeHeadline key={change.citation} change={change} />)}
      <section className="result-section" aria-labelledby="macro-analysis-title">
        <h3 id="macro-analysis-title">{result.narrative_kind === 'deterministic' ? 'Data summary' : 'AI Analysis'}</h3>
        {result.research_v2 ? <>
          {result.support === 'partial' && <p className="muted">Partial answer</p>}
          {result.status === 'insufficient_evidence' && <p>There is insufficient evidence for an answer.</p>}
          {result.narrative?.map((claim, index) => <p className="research-narrative" key={index}>
            {claim.category && <><strong>{categoryLabels[claim.category]}</strong>{' · '}</>}
            {claim.text}{claim.citations.map((id) => {
              const sourceIndex = result.evidence.findIndex((source) => source.id === id);
              const source = result.evidence[sourceIndex];
              return <a key={id} href={source.url} aria-label={source.title}> [{sourceIndex + 1}]</a>;
            })}
          </p>)}
          {result.limitations.map((note) => <p className="muted" key={note}>{note}</p>)}
        </> : result.status === 'insufficient_evidence'
          ? <><p>There is insufficient evidence for an answer.</p>
            {result.limitations[0] && <p className="muted">{result.limitations[0]}</p>}</>
          : <>
            {result.narrative_kind === 'deterministic' && <p className="muted">Generated from official data without Gemini.</p>}
            <p className="research-narrative">{result.narrative?.map(({ text }) => text).join(' ')
              || 'No concise analysis is available for this result.'}</p>
          </>}
      </section>
      <section className="result-section">
        <h3>RAG Sources &amp; Evidence</h3>
        {documents.length === 0 ? <p className="muted">No institutional documents were retrieved for this answer.</p>
          : <ul className="result-list research-sources">{documents.map((item) => <li key={item.id}>
            <a href={item.url}>{item.title}</a>
            <div className="muted">{item.attribution}
              {item.published_on && <> · <time dateTime={item.published_on}>{displayDate.format(new Date(item.published_on))}</time></>}
            </div>
            {item.claim_id && <div className="muted">
              {item.claim_type === 'institutional_interpretation' ? 'Institutional interpretation'
                : item.relation === 'proxy' ? 'Proxy context' : 'Documentary context'}
              {' · '}{item.geography}{' · '}{item.locator}
              <div>{item.qualifiers}</div>
            </div>}
          </li>)}</ul>}
        {series.length > 0 && <>
          <h4>Statistical series</h4>
          <ul className="result-list research-sources">{series.map((item) => <li key={item.id}>
            <a href={item.url}>{item.title}</a><div className="muted">{item.attribution}</div>
          </li>)}</ul>
        </>}
        <details className="research-details">
          <summary>Technical details</summary>
          <p className="muted">{result.start} to {result.end}{result.model_id && ` · ${result.model_id}`}</p>
          {result.publication_cutoff && <p className="muted">Document publication cutoff {result.publication_cutoff}</p>}
          {[...documents, ...series].map((item) => <p className="muted" key={item.id}>
            {item.title}
            {item.period_start && item.period_end && <> · {item.period_start} to {item.period_end}</>}
            {item.updated_at && <> · Data updated <time dateTime={item.updated_at}>{item.updated_at}</time></>}
            {item.similarity_metric === 'cosine' && item.similarity_score != null
              && <> · Cosine similarity {item.similarity_score.toFixed(3)}</>}
            {item.rights_url && <> · <a href={item.rights_url}>Reuse terms</a></>}
            {item.series_key && <> · {item.series_key} · {item.unit} · {item.adjustment}</>}
            {item.claim_id && <> · Claim {item.claim_id} · Document {item.document_id} · Relation {item.relation}</>}
          </p>)}
          {!result.research_v2 && result.limitations.map((note) => <p className="muted" key={note}>{note}</p>)}
        </details>
      </section>
    </div>}
  </section>;
}
