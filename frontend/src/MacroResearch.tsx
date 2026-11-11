import { useEffect, useRef, useState } from 'react';
import { askMacroAnalysis, type MacroAnalysis, type MacroIndicator, type ResearchClaim } from './macro-api';
import { QuestionComposer, type QuestionSubmission } from './QuestionComposer';

function Narrative({ claims, evidence }: { claims: ResearchClaim[]; evidence: string[] }) {
  return claims.map((claim, index) => <p key={index}>
    {claim.text}{claim.citations.map((id) => {
      const reference = evidence.indexOf(id);
      return reference >= 0 ? <a key={id} href={`#macro-source-${reference + 1}`}> [{reference + 1}]</a> : null;
    })}
  </p>);
}

export function MacroResearch({ enabled, indicatorId, indicators, onIndicatorChange }: {
  enabled: boolean; indicatorId: string; indicators: MacroIndicator[]; onIndicatorChange: (id: string) => void;
}) {
  const [request, setRequest] = useState<{
    indicatorId: string; controller: AbortController; pending: boolean; failed: boolean; result?: MacroAnalysis;
  }>();
  const active = useRef<AbortController>(null);
  useEffect(() => () => active.current?.abort(), [indicatorId, enabled]);
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
      { indicator_id: submission.indicatorId, event_id: submission.eventId }).then((data) => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? { ...current, result: data } : current);
    }, () => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? { ...current, failed: true } : current);
    }).finally(() => {
      if (!controller.signal.aborted) setRequest((current) => current?.controller === controller ? { ...current, pending: false } : current);
    });
  }

  const evidence = result?.evidence.map(({ id }) => id) ?? [];
  const documents = result?.evidence.filter(({ kind }) => kind === 'document') ?? [];
  const series = result?.evidence.filter(({ kind }) => kind === 'series') ?? [];
  return <section className="panel" aria-labelledby="macro-research-title">
    <h2 id="macro-research-title">RAG-Powered Macro Research</h2>
    {enabled ? <QuestionComposer enabled={enabled} pending={pending} indicators={indicators}
      indicatorId={indicatorId} onIndicatorChange={onIndicatorChange} onSubmit={submit} />
      : <p className="muted">AI research is available only in an enabled local workspace.</p>}
    {pending && <output className="notice">Checking official evidence…</output>}
    {failed && <p role="alert" className="notice error">Local macro research is currently unavailable.</p>}
    {result && <div className="analysis-result">
      <h3>LLM Model</h3>
      <p className="muted">{result.start} to {result.end}{result.model_id && ` · ${result.model_id}`}</p>
      {result.status === 'insufficient_evidence' && <p className="notice">There is insufficient evidence for an answer.</p>}
      <Narrative claims={result.narrative ?? [...result.facts, ...result.calculations, ...result.interpretation]} evidence={evidence} />
      {result.limitations.length > 0 && <section className="result-section" aria-label="Research limitations">
        {result.limitations.map((text, index) => <p className="muted" key={index}>{text}</p>)}
      </section>}
      <section className="result-section">
        <h3>RAW sources</h3>
        {documents.length === 0 ? <p className="muted">No institutional documents were retrieved for this answer.</p>
          : <ol className="result-list">{documents.map((item) => <li value={evidence.indexOf(item.id) + 1}
            id={`macro-source-${evidence.indexOf(item.id) + 1}`} key={item.id}>
            <a href={item.url}>{item.title}</a> · {item.attribution}
            {item.published_on && <> · Published <time dateTime={item.published_on}>{item.published_on}</time></>}
            {item.similarity_metric === 'cosine' && item.similarity_score != null
              && <> · Cosine similarity {item.similarity_score.toFixed(3)}</>}
            {item.rights_url && <> · <a href={item.rights_url}>Reuse terms</a></>}
          </li>)}</ol>}
      </section>
      {series.length > 0 && <section className="result-section" aria-label="Calculation provenance">
        {series.map((item) => <p id={`macro-source-${evidence.indexOf(item.id) + 1}`} key={item.id} className="muted">
          [{evidence.indexOf(item.id) + 1}] <a href={item.url}>{item.title}</a> · {item.attribution}
          {item.period_start && item.period_end && <> · {item.period_start} to {item.period_end}</>}
        </p>)}
      </section>}
    </div>}
  </section>;
}
