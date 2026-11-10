import type { DateWindow } from './api';

export const DAY_SECONDS = 86_400;

export const PERIODS = [
  { id: '1m', label: '1M', months: 1 },
  { id: '3m', label: '3M', months: 3 },
  { id: '6m', label: '6M', months: 6 },
  { id: '1y', label: '1Y', months: 12 },
  { id: '3y', label: '3Y', months: 36 },
  { id: 'max', label: 'Max', months: null },
] as const;

export type PeriodId = typeof PERIODS[number]['id'];

export const dateToSeconds = (date: string) => Date.parse(`${date}T00:00:00Z`) / 1000;
export const secondsToDate = (seconds: number) => new Date(seconds * 1000).toISOString().slice(0, 10);

const dayLabel = new Intl.DateTimeFormat('en-US', {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
});

export function formatDate(date: string): string {
  return dayLabel.format(dateToSeconds(date) * 1000);
}

export function formatWindow(window: DateWindow): string {
  const last = secondsToDate(dateToSeconds(window.end_exclusive) - DAY_SECONDS);
  return `${formatDate(window.start)} — ${formatDate(last)}`;
}

export function historyWindow(first: string, last: string, period: PeriodId): DateWindow {
  const months = PERIODS.find(({ id }) => id === period)?.months ?? null;
  const end_exclusive = secondsToDate(dateToSeconds(last) + DAY_SECONDS);
  if (months === null) return { start: first, end_exclusive };

  const start = new Date(dateToSeconds(last) * 1000);
  const originalDay = start.getUTCDate();
  start.setUTCDate(1);
  start.setUTCMonth(start.getUTCMonth() - months);
  const monthEnd = new Date(start);
  monthEnd.setUTCMonth(monthEnd.getUTCMonth() + 1, 0);
  start.setUTCDate(Math.min(originalDay, monthEnd.getUTCDate()));
  const relativeStart = start.toISOString().slice(0, 10);
  return { start: relativeStart < first ? first : relativeStart, end_exclusive };
}
