import { useId, useState, type FormEvent, type ReactNode } from 'react';
import type { MacroIndicator } from './macro-api';
import { indicatorPresentation } from './macro-chart';
import { historicalEvents, type HistoricalEventId, type QuestionSubmission } from './question-events';
import './QuestionComposer.css';

export type { HistoricalEventId, QuestionSubmission } from './question-events';

export interface QuestionComposerProps {
  indicators: MacroIndicator[];
  indicatorId: string;
  onIndicatorChange: (id: string) => void;
  pending: boolean;
  enabled: boolean;
  onSubmit: (submission: QuestionSubmission) => void;
  verification?: ReactNode;
  canSubmit?: boolean;
  allowCustomWindow?: boolean;
}

export function QuestionComposer({ indicators, indicatorId, onIndicatorChange, pending, enabled,
  onSubmit, verification, canSubmit = true, allowCustomWindow = false }: QuestionComposerProps) {
  const fieldId = useId();
  const [eventId, setEventId] = useState<HistoricalEventId | undefined>('covid');
  const [customStart, setCustomStart] = useState('2020-02-01');
  const [customEnd, setCustomEnd] = useState('2020-12-31');
  const [custom, setCustom] = useState<{ question: string; indicatorId: string; eventId?: HistoricalEventId; start: string; end: string }>();
  const selectedEvent = historicalEvents.find((item) => item.id === eventId);
  const indicator = indicators.find((item) => item.id === indicatorId);
  const generated = indicator
    ? `${selectedEvent?.prefix ?? 'What happened to'} ${indicatorPresentation(indicator).label}?` : '';
  const start = selectedEvent?.start ?? customStart;
  const end = selectedEvent?.end ?? customEnd;
  const windowMs = Date.parse(end) - Date.parse(start);
  const validWindow = windowMs >= 0 && windowMs <= 3660 * 86_400_000;
  const question = custom?.question ?? generated;
  const changedContext = custom && (custom.indicatorId !== indicatorId || custom.eventId !== eventId
    || custom.start !== start || custom.end !== end);
  const disabled = !enabled || pending;
  const valid = question.trim().length >= 5 && question.length <= 500
    && !Array.from(question).some((character) => character.charCodeAt(0) < 32);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || !indicator || !valid || !validWindow || !canSubmit) return;
    onSubmit({ question: question.trim(), indicatorId, ...(eventId && { eventId }), start, end });
  }

  return <form className="question-composer" onSubmit={submit} aria-label="Macro research question composer">
    <fieldset disabled={disabled} className="composer-options">
      <legend>Indicator</legend>
      <div className="composer-choices">
        {indicators.map((item) => <button key={item.id} type="button"
          aria-pressed={item.id === indicatorId} onClick={() => onIndicatorChange(item.id)}>
          {indicatorPresentation(item).label}
        </button>)}
      </div>
    </fieldset>
    <fieldset disabled={disabled} className="composer-options">
      <legend>Historical event</legend>
      <div className="composer-choices">
        {historicalEvents.map((item) => <button key={item.id} type="button"
          aria-pressed={item.id === eventId} onClick={() => setEventId(item.id)}>{item.label}</button>)}
        {allowCustomWindow && <button type="button" aria-pressed={!eventId}
          onClick={() => setEventId(undefined)}>Custom dates</button>}
      </div>
      {!selectedEvent && <div className="composer-choices">
        <label>Start <input type="date" required value={customStart} onChange={(event) => setCustomStart(event.target.value)} /></label>
        <label>End <input type="date" required value={customEnd} onChange={(event) => setCustomEnd(event.target.value)} /></label>
      </div>}
    </fieldset>
    <p id={`${fieldId}-context`} className="muted composer-context">
      {indicator ? indicatorPresentation(indicator).label : 'Select an available indicator'} · {selectedEvent?.label ?? 'Custom dates'} · {start} to {end}
    </p>
    {!validWindow && <p className="muted">Choose an ordered date range of up to 3,660 days.</p>}
    <label htmlFor={fieldId}>Research question</label>
    <textarea id={fieldId} rows={3} required minLength={5} maxLength={500}
      value={question} disabled={disabled || !indicator}
      aria-describedby={`${fieldId}-context${changedContext ? ` ${fieldId}-review` : ''}`}
      onChange={(change) => setCustom({ question: change.target.value, indicatorId, eventId, start, end })} />
    {changedContext && <output id={`${fieldId}-review`} className="muted">
      Your custom question is preserved. Review it for the selected indicator and period before submitting.
    </output>}
    {verification}
    <div className="composer-actions">
      {custom && <button type="button" disabled={disabled || !indicator} onClick={() => setCustom(undefined)}>
        Use generated question
      </button>}
      <button type="submit" className="primary" disabled={disabled || !indicator || !valid || !validWindow || !canSubmit}>Ask LLM Model</button>
    </div>
  </form>;
}
