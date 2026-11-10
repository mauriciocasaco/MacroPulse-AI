import type { DailyBar } from './api';
import { DAY_SECONDS, dateToSeconds } from './dates';

export type PriceField = 'close' | 'adjusted_close';

export function chartData(bars: DailyBar[], field: PriceField) {
  const indices: number[] = [];
  const dates: number[] = [];
  const prices: number[] = [];
  const observations: DailyBar[] = [];
  const indexByDate = new Map<string, number | null>();
  const barsByDate = new Map(bars.map((bar) => [bar.bar_date, bar]));
  let firstDate: string | undefined;
  let lastDate: string | undefined;

  for (const date of [...barsByDate.keys()].sort()) {
    const bar = barsByDate.get(date)!;
    const price = bar[field];
    const seconds = dateToSeconds(date);
    const value = price == null || (typeof price === 'string' && !price.trim()) ? NaN : Number(price);
    if (!Number.isFinite(seconds)) continue;
    firstDate ??= date;
    lastDate = date;
    if (!Number.isFinite(value)) {
      indexByDate.set(date, null);
      continue;
    }
    indexByDate.set(date, indices.length);
    indices.push(indices.length);
    dates.push(seconds);
    prices.push(value);
    observations.push(bar);
  }

  return { indices, dates, prices, observations, indexByDate, firstDate, lastDate };
}

export function priceDomain(min: number | null, max: number | null): [number, number] {
  if (min === null || max === null) return [-1, 1];
  const magnitude = Math.max(Math.abs(min), Math.abs(max));
  const pad = min === max
    ? Math.max(magnitude * .005, .2)
    : Math.max((max - min) * .08, magnitude * Number.EPSILON * 4);
  return [min - pad, max + pad];
}

export function sessionDomain(min: number | null, max: number | null): [number, number] {
  // Integer positions always represent observations. Leave space for endpoint strokes and markers.
  return [Math.round(min ?? 0) - .4, Math.round(max ?? 0) + .4];
}

export function sessionTicks(min: number, max: number, width: number, last: number): number[] {
  const first = Math.max(0, Math.ceil(min));
  const end = Math.min(last, Math.floor(max));
  if (end < first) return [];
  if (end === first) return [first];

  const intervals = Math.min(end - first, Math.max(1, Math.floor(width / 82)));
  return Array.from({ length: intervals + 1 }, (_, i) => first + Math.round((end - first) * i / intervals));
}

const axisDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const axisFull = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: '2-digit', timeZone: 'UTC' });

export function sessionLabels(ticks: number[], dates: number[]): string[] {
  if (ticks.length === 0) return [];
  const first = dates[ticks[0]];
  const last = dates[ticks[ticks.length - 1]];
  const crossesYear = new Date(first * 1000).getUTCFullYear() !== new Date(last * 1000).getUTCFullYear();
  const formatter = crossesYear || last - first > 180 * DAY_SECONDS ? axisFull : axisDay;
  return ticks.map((index) => formatter.format(dates[index] * 1000));
}
