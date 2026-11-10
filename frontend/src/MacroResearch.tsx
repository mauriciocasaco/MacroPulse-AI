import { useEffect, useRef, useState, type FormEvent } from 'react';
import { askMacroAnalysis, type MacroAnalysis, type ResearchClaim } from './macro-api';

function ClaimList({ title, items, evidence }: { title: string; items: ResearchClaim[]; evidence: string[] }) {
  if (items.length === 0) return null;
  return <section className="result-section">
    <h3>{title}</h3>
    <ul className="result-list">{items.map((claim, index) => <li key={index}>
      {claim.text}{claim.citations.map((id) => {
        const reference = evidence.indexOf(id);
        return reference >= 0
          ? <a key={id} href={`#macro-source-${reference + 1}`}> [{reference + 1}]</a> : null;
      })}
    </li>)}</ul>
  </section>;
}

export function MacroResearch({ enabled, start, end }: { enabled: boolean; start: string; end: string }) {
  const [question, setQuestion] = useState('');
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const [result, setResult] = useState<MacroAnalysis>();
  const active = useRef<AbortController>(null);
  useEffect(() => () => active.current?.abort(), []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enabled || pending || !question.trim()) return;
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setFailed(false);
    setResult(undefined);
    askMacroAnalysis(question.trim(), start, end, controller.signal).then((data) => {
      if (!controller.signal.aborted) setResult(data);
    }, () => {
      if (!controller.signal.aborted) setFailed(true);
    }).finally(() => {
      if (!controller.signal.aborted) setPending(false);
    });
  }

  const evidence = result?.evidence.map(({ id }) => id) ?? [];
  return <section className="panel" aria-labelledby="macro-research-title">
    <h2 id="macro-research-title">Ask about macro data</h2>
    <p className="muted">{enabled
      ? `Period ${start} to ${end}. Gemini receives the recognized topic and selected public evidence.`
      : 'AI research is available only in an enabled local workspace.'}</p>
    {enabled && <form onSubmit={submit}>
      <label className="question">Research question
        <textarea disabled={!enabled || pending} value={question} required rows={2} maxLength={500}
          onChange={(event) => setQuestion(event.target.value)} />
      </label>
      <div className="analysis-actions">
        <button type="submit" className="primary" disabled={pending || !question.trim()}>Ask</button>
      </div>
    </form>}
    {pending && <output className="notice">Checking official evidence…</output>}
    {failed && <p role="alert" className="notice error">Local macro research is currently unavailable.</p>}
    {result && <div className="analysis-result">
      {result.status === 'insufficient_evidence' && <p className="notice">There is insufficient evidence for an answer.</p>}
      <ClaimList title="Facts" items={result.facts} evidence={evidence} />
      <ClaimList title="Calculations" items={result.calculations} evidence={evidence} />
      <ClaimList title="Interpretation" items={result.interpretation} evidence={evidence} />
      {result.limitations.length > 0 && <section className="result-section">
        <h3>Limitations</h3>
        <ul className="result-list">{result.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul>
      </section>}
      {result.evidence.length > 0 && <section className="result-section">
        <h3>Sources</h3>
        <ol className="result-list">{result.evidence.map((item, index) => <li id={`macro-source-${index + 1}`} key={item.id}>
          <a href={item.url}>{item.title}</a> · {item.attribution}
          {item.published_on && <> · Published <time dateTime={item.published_on}>{item.published_on}</time></>}
          {item.period_start && item.period_end && <> · {item.period_start} to {item.period_end}</>}
        </li>)}</ol>
      </section>}
    </div>}
  </section>;
}
