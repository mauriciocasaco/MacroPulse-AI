import { useEffect, useRef, useState, type FormEvent } from 'react';
import { getAnalysis, type AnalysisResponse, type Asset, type Citation, type DateWindow, type Event } from './api';
import { formatWindow } from './dates';

const EVENTS = [
  { id: 'covid', title: 'Pandemia de COVID-19', subject: 'la pandemia del COVID-19', start: '2020-02-01', end_exclusive: '2020-07-01' },
  { id: 'ukraine', title: 'Invasión rusa de Ucrania', subject: 'la invasión rusa de Ucrania', start: '2022-02-01', end_exclusive: '2022-07-01' },
  { id: 'genai', title: 'Auge de la IA generativa', subject: 'el auge de la IA generativa', start: '2022-11-01', end_exclusive: '2025-01-01' },
] as const satisfies readonly (DateWindow & { id: Event; title: string; subject: string })[];

const coverageLabel = { sufficient: 'Evidencia directa', partial: 'Evidencia parcial', insufficient: 'Evidencia insuficiente' };

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
  return <li className="source" id={`fuente-${index + 1}`}>
    <span className="source-index">{String(index + 1).padStart(2, '0')}</span>
    <div>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer">{item.source_name} ↗</a> : <strong>{item.source_name}</strong>}
      <p className="muted">{item.source_id} · {item.evidence_id} · {item.relevance === 'direct' ? 'Directa' : 'Contextual'}</p>
      <p className="muted">Publicación {item.published_at ?? item.published_on ?? 'sin fecha'} · Recuperación {item.retrieved_at}</p>
      {item.provider_observed_at && <p className="muted">Observación del proveedor {item.provider_observed_at}</p>}
    </div>
  </li>;
}

function Result({ result }: { result: AnalysisResponse }) {
  const { prices } = result;
  const observations = [
    ['Primer cierre', prices.first], ['Último cierre', prices.last],
    ['Mínimo', prices.minimum_close], ['Máximo', prices.maximum_close],
  ] as const;

  return <article className="analysis-result" aria-label="Resultado de la consulta">
    <header className="result-header">
      <div>
        <p className="eyebrow">Resultado · {result.asset}</p>
        <h3>{EVENTS.find(({ id }) => id === result.event)?.title}</h3>
        <p className="muted">{formatWindow(result)}{result.as_of_exclusive && ` · Corte exclusivo ${result.as_of_exclusive}`}</p>
      </div>
      <div className="result-badges">
        <span className={`badge ${result.coverage === 'sufficient' ? 'mint' : 'amber'}`}>{coverageLabel[result.coverage]}</span>
        {result.provider === 'simulated' && <span className="badge">Proveedor simulado</span>}
      </div>
    </header>
    <section className="result-section">
      <h4>Precios observados</h4>
      <p className="muted">{prices.observations} cierres sin ajuste · {prices.economic_unit} · {prices.source_id}</p>
      {prices.observations > 0 && <dl className="summary-grid">
        {observations.map(([label, item]) => item && <div key={label}>
          <dt>{label}</dt><dd>{item.close}</dd>
          <dd className="muted"><time dateTime={item.date}>{item.date}</time></dd>
          <dd className="muted">Recuperación <time dateTime={item.retrieved_at}>{item.retrieved_at}</time></dd>
        </div>)}
      </dl>}
      {prices.close_change !== null && <p className="delta">Variación del cierre <strong>{prices.close_change} {prices.economic_unit}</strong>
        {prices.close_change_pct !== null && <> · <strong>{prices.close_change_pct}%</strong></>}
      </p>}
      <p className="muted">Convención de origen {prices.source_convention}{prices.rolling_method && ` · Rollover ${prices.rolling_method}`}</p>
    </section>
    <section className="result-section">
      <h4>Interpretación económica</h4>
      <p lang="en">{result.economic_interpretation}</p>
      {result.economic_mechanisms.length > 0 && <ul className="result-list">
        {result.economic_mechanisms.map((mechanism) => {
          const index = result.citations.findIndex((item) => item.evidence_id === mechanism.citation_id);
          return <li key={mechanism.citation_id}>
            <span lang="en">{mechanism.summary}</span>{index >= 0 && <> <a href={`#fuente-${index + 1}`}>Fuente {index + 1}</a></>}
          </li>;
        })}
      </ul>}
    </section>
    <section className="result-section">
      <h4>Evidencia y fuentes</h4>
      {result.citations.length ? <ol className="sources">{result.citations.map((item, index) =>
        <Source key={item.evidence_id} item={item} index={index} />)}</ol> : <p className="muted">La respuesta no incluye citas documentales.</p>}
    </section>
    <section className="result-section">
      <h4>Incertidumbre y limitaciones</h4>
      <ul className="result-list muted" lang="en">{result.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul>
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
  const preset = `¿Cómo afectó ${selected.subject} a ${asset.symbol}?`;

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
      setQuery({ kind: 'error', message: 'Escribí una consulta de entre 1 y 48 términos.' });
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
        <legend>Acontecimiento</legend>
        <div className="events">
          {EVENTS.map((item, index) => <label className="event" key={item.id} htmlFor={`event-${item.id}`} aria-label={item.title}>
            <input id={`event-${item.id}`} type="radio" name="event" value={item.id} checked={item.id === event}
              onChange={() => { setEvent(item.id); setQuestion(''); clearResult(); }} />
            <span className="event-body">
              <span className="event-number">0{index + 1}</span>
              <span>{item.title}</span>
              <span className="muted">{formatWindow(item)}</span>
            </span>
          </label>)}
        </div>
      </fieldset>
      <div className="question">
        <label htmlFor="question">Pregunta personalizada <span className="muted">· opcional</span></label>
        <textarea id="question" aria-describedby="question-help" value={question} maxLength={1024}
          rows={2} placeholder={preset} onChange={(e) => { setQuestion(e.target.value); clearResult(); }} />
        <p className="muted" id="question-help">Si dejás el campo vacío, se usa «{preset}». El análisis mantiene el acontecimiento y sus fechas.</p>
      </div>
      <div className="analysis-actions">
        <p className="muted">Ventana de análisis <strong>{formatWindow(selected)}</strong></p>
        <button className="primary" type="submit" disabled={query.kind === 'loading'}>
          {query.kind === 'loading' ? 'Analizando…' : 'Analizar acontecimiento'}
        </button>
      </div>
    </form>
    {query.kind === 'loading' && <output className="notice">Consultando precios y evidencia en FastAPI…</output>}
    {query.kind === 'error' && <p className="notice error" role="alert">{query.message}</p>}
    {query.kind === 'ready' && <Result result={query.data} />}
  </>;
}
