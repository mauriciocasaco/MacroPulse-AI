export const historicalEvents = [
  { id: 'covid', label: 'COVID-19', start: '2020-02-01', end: '2020-12-31',
    period: 'the COVID-19 pandemic' },
  { id: 'ukraine', label: 'Ukraine War', start: '2022-02-01', end: '2024-02-29',
    period: 'the war in Ukraine' },
  { id: 'generative-ai', label: 'Generative AI Boom', start: '2023-09-01', end: '2026-09-30',
    period: 'the rise of generative AI and LLMs' },
] as const;

export type HistoricalEventId = typeof historicalEvents[number]['id'];

export interface QuestionSubmission {
  question: string;
  indicatorId: string;
  eventId?: HistoricalEventId;
  start: string;
  end: string;
}
