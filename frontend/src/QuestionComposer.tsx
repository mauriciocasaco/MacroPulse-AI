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
}

export function QuestionComposer({ indicators, indicatorId, onIndicatorChange, pending, enabled,
  onSubmit, verification, canSubmit = true }: QuestionComposerProps) {
  const fieldId = useId();
  const [eventId, setEventId] = useState<HistoricalEventId>('covid');
  const [custom, setCustom] = useState<{ question: string; indicatorId: string; eventId: HistoricalEventId }>();
  const selectedEvent = historicalEvents.find((item) => item.id === eventId)!;
  const indicator = indicators.find((item) => item.id === indicatorId);
  const generated = indicator ? `${selectedEvent.prefix} ${indicatorPresentation(indicator).label}?` : '';
  const question = custom?.question ?? generated;
  const changedContext = custom && (custom.indicatorId !== indicatorId || custom.eventId !== eventId);
  const disabled = !enabled || pending;
  const valid = question.trim().length >= 5 && question.length <= 500
    && !Array.from(question).some((character) => character.charCodeAt(0) < 32);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || !indicator || !valid || !canSubmit) return;
    onSubmit({ question: question.trim(), indicatorId, eventId, start: selectedEvent.start, end: selectedEvent.end });
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
      </div>
    </fieldset>
    <p id={`${fieldId}-context`} className="muted composer-context">
      {indicator ? indicatorPresentation(indicator).label : 'Select an available indicator'} · {selectedEvent.label}: {selectedEvent.start} to {selectedEvent.end}
    </p>
    <label htmlFor={fieldId}>Research question</label>
    <textarea id={fieldId} rows={3} required minLength={5} maxLength={500}
      value={question} disabled={disabled || !indicator}
      aria-describedby={`${fieldId}-context${changedContext ? ` ${fieldId}-review` : ''}`}
      onChange={(change) => setCustom({ question: change.target.value, indicatorId, eventId })} />
    {changedContext && <output id={`${fieldId}-review`} className="muted">
      Your custom question is preserved. Review it for the selected indicator and historical event before submitting.
    </output>}
    {verification}
    <div className="composer-actions">
      {custom && <button type="button" disabled={disabled || !indicator} onClick={() => setCustom(undefined)}>
        Use generated question
      </button>}
      <button type="submit" className="primary" disabled={disabled || !indicator || !valid || !canSubmit}>Ask LLM Model</button>
    </div>
  </form>;
}
