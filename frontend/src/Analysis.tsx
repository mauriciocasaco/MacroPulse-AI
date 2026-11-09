import { useEffect, useRef, useState, type FormEvent } from 'react';
import { getAnalysis, type AnalysisResponse, type Asset, type Citation, type DateWindow, type Event } from './api';
import { formatDate, formatWindow } from './dates';

const EVENTS = [
  { id: 'covid', title: 'COVID-19 pandemic', subject: 'the COVID-19 pandemic', start: '2020-02-01', end_exclusive: '2020-07-01' },
  { id: 'ukraine', title: "Russia's invasion of Ukraine", subject: 'Russia’s invasion of Ukraine', start: '2022-02-01', end_exclusive: '2022-07-01' },
  { id: 'genai', title: 'Rise of generative AI', subject: 'the rise of generative AI', start: '2022-11-01', end_exclusive: '2025-01-01' },
] as const satisfies readonly (DateWindow & { id: Event; title: string; subject: string })[];

const percent = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: 'exceptZero',
});

function sourceUrl(raw: string): string | undefined {
  try {
    const url = new URL(raw);
    if ((url.protocol === 'https:' || url.protocol === 'http:') && !url.username && !url.password) {
      return url.href;
    }
  } catch {
    // A malformed URL is never rendered as a link.
  }
}

function Source({ item, index }: { item: Citation; index: number }) {
  const url = sourceUrl(item.original_url);
  const date = item.published_at ? new Date(item.published_at) : null;
  const published = date && Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : item.published_on;
  const publisher = item.publisher || item.source_name;
  const label = item.title || publisher;
  return <li className="source" id={`document-${index + 1}`}>
    <div>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer">{label}</a> : <strong>{label}</strong>}
      <p className="muted">
        {publisher !== label && <>{publisher} · </>}
        {item.relevance === 'direct' ? 'Direct evidence' : 'Contextual'}
        {published && <> · <time dateTime={published}>{formatDate(published)}</time></>}
      </p>
    </div>
  </li>;
}

function Result({ result }: { result: AnalysisResponse }) {
  const { first, last, close_change_pct } = result.prices;
  const change = close_change_pct === null ? NaN : Number(close_change_pct);
  const available = Number.isFinite(change);
  const tone = available && change > 0 ? 'positive' : available && change < 0 ? 'negative' : 'neutral';

  return <article className="analysis-result" aria-label="Analysis result">
    <section className="price-change" aria-labelledby="price-change-title">
      <h3 id="price-change-title">Price Change</h3>
      <p className={`change-value ${tone}`}>{available ? `${percent.format(change)}%` : 'N/A'}</p>
      {first && last && <p className="muted">
        <time dateTime={first.date}>{formatDate(first.date)}</time>
        {' – '}
        <time dateTime={last.date}>{formatDate(last.date)}</time>
      </p>}
    </section>
    <section className="result-section" aria-labelledby="ai-analysis-title">
      <h3 id="ai-analysis-title">AI Analysis</h3>
      {result.coverage === 'insufficient' && <p className="evidence-status">Insufficient evidence</p>}
      <p>{result.economic_interpretation}</p>
      {result.economic_mechanisms.length > 0 && <ul className="result-list">
        {result.economic_mechanisms.map((mechanism) => {
          const index = result.citations.findIndex((item) => item.evidence_id === mechanism.citation_id);
          return <li key={mechanism.citation_id}>
            {mechanism.summary}{index >= 0 && <> <a href={`#document-${index + 1}`}>Document {index + 1}</a></>}
          </li>;
        })}
      </ul>}
    </section>
    <section className="result-section" aria-labelledby="rag-documents-title">
      <h3 id="rag-documents-title">RAG Documents</h3>
      {result.citations.length ? <ol className="sources">{result.citations.map((item, index) =>
        <Source key={item.evidence_id} item={item} index={index} />)}</ol> : <p className="muted">No relevant documents found.</p>}
    </section>
  </article>;
}

type QueryState = { kind: 'idle' } | { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; data: AnalysisResponse };

export function Analysis({ asset }: { asset: Asset }) {
  const [event, setEvent] = useState<Event>('covid');
  const [question, setQuestion] = useState('');
  const [query, setQuery] = useState<QueryState>({ kind: 'idle' });
  const controller = useRef<AbortController>(null);
  const selected = EVENTS.find(({ id }) => id === event)!;
  const preset = `How did ${selected.subject} affect ${asset.symbol}?`;

  useEffect(() => () => controller.current?.abort(), []);

  function clearResult() {
    controller.current?.abort();
    setQuery({ kind: 'idle' });
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const text = question.trim() || preset;
    const words = text.match(/[\p{L}\p{N}]+/gu)?.length ?? 0;
    if (words < 1 || words > 48) {
      setQuery({ kind: 'error', message: 'Enter a question with 1 to 48 terms.' });
      return;
    }
    controller.current?.abort();
    const attempt = new AbortController();
    controller.current = attempt;
    setQuery({ kind: 'loading' });
    try {
      const data = await getAnalysis({
        asset: asset.asset_id, event, start: selected.start, end_exclusive: selected.end_exclusive, question: text,
      }, attempt.signal);
      if (!attempt.signal.aborted) setQuery({ kind: 'ready', data });
    } catch (error) {
      if (!attempt.signal.aborted) setQuery({ kind: 'error', message: (error as Error).message });
    }
  }

  return <>
    <form onSubmit={submit} className="analysis-form">
      <fieldset>
        <legend>Event</legend>
        <div className="events">
          {EVENTS.map((item) => <label className="event" key={item.id} htmlFor={`event-${item.id}`} aria-label={item.title}>
            <input id={`event-${item.id}`} type="radio" name="event" value={item.id} checked={item.id === event}
              onChange={() => { setEvent(item.id); setQuestion(''); clearResult(); }} />
            <span className="event-body">
              <span>{item.title}</span>
              <span className="muted">{formatWindow(item)}</span>
            </span>
          </label>)}
        </div>
      </fieldset>
      <div className="question">
        <label htmlFor="question">Custom question <span className="muted">· optional</span></label>
        <textarea id="question" value={question} maxLength={1024}
          rows={2} placeholder={preset} onChange={(e) => { setQuestion(e.target.value); clearResult(); }} />
      </div>
      <div className="analysis-actions">
        <p className="muted">Analysis period <strong>{formatWindow(selected)}</strong></p>
        <button className="primary" type="submit" disabled={query.kind === 'loading'}>
          {query.kind === 'loading' ? 'Analyzing…' : 'Run analysis'}
        </button>
      </div>
    </form>
    {query.kind === 'loading' && <output className="notice">Looking up prices and evidence…</output>}
    {query.kind === 'error' && <p className="notice error" role="alert">{query.message}</p>}
    {query.kind === 'ready' && <Result result={query.data} />}
  </>;
}
