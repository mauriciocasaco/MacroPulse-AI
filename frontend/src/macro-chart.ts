import { getMacroHistory, type MacroHistory, type MacroIndicator, type MacroObservation } from './macro-api';

export const chartPeriods = ['1M', '3M', '6M', '1Y', '3Y', '7Y'] as const;
export type ChartPeriod = typeof chartPeriods[number];
export const indicatorStyles: Record<string, { label: string; color: string }> = {
  'eur-usd': { label: 'EUR/USD rate', color: '#16A34A' },
  'm3-stock': { label: 'Eurozone M3', color: '#2563EB' },
  'deposit-rate': { label: 'ECB rate', color: '#EA580C' },
  'real-gdp-qoq': { label: 'Eurozone GDP', color: '#C58B13' },
  'hicp-yoy': { label: 'Eurozone Inflation', color: '#9333EA' },
};
export function indicatorPresentation(indicator: MacroIndicator) {
  return indicatorStyles[indicator.id] ?? { label: indicator.title, color: '#2563EB' };
}
const unitLabels: Record<string, string> = {
  'USD/EUR': 'USD per EUR', EUR: 'EUR', percent_per_annum: '% per year',
  percent_qoq: '% quarter on quarter', percent_yoy: '% year on year',
};
export function unitLabel(unit: string, scale: number) {
  return unit === 'EUR' && scale === 6 ? 'Millions of EUR' : unitLabels[unit] ?? unit;
}

export type ChartHistory = MacroHistory & { chunks: MacroHistory[] };
const dayMs = 86_400_000;
// The public Worker allows fewer than 2,600 days per request. Use its
// stricter bound locally too, while leaving API and edge safeguards intact.
const historyChunkDays = 2599;
// All five pinned euro-area series postdate 1900. This is a retrieval bound,
// never a claimed date of availability. Keep requests within the existing API cap.
const historyFloor = '1900-01-01';
const iso = (date: Date) => date.toISOString().slice(0, 10);
export const pointDay = (row: MacroObservation) => row.effective_date ?? row.period_end;
export const numeric = (row: MacroObservation) => row.value !== null && Number.isFinite(Number(row.value));

export function subtractMonths(day: string, months: number) {
  const date = new Date(`${day}T00:00:00Z`);
  const originalDay = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() - months);
  const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(originalDay, lastDay));
  return iso(date);
}

export async function loadChartHistory(id: string, signal: AbortSignal): Promise<ChartHistory> {
  const chunks: MacroHistory[] = [];
  let end = iso(new Date());
  while (end >= historyFloor) {
    const start = iso(new Date(Math.max(Date.parse(historyFloor), Date.parse(end) - historyChunkDays * dayMs)));
    const chunk = await getMacroHistory(id, start, end, signal);
    signal.throwIfAborted();
    chunks.unshift(chunk);
    // A recent response contains every finite preset. Search older chunks
    // when a series has no recent numeric data, even for a short preset.
    const latest = chunks.flatMap((item) => item.observations).filter(numeric)
      .map(pointDay).sort().at(-1) ?? chunks.flatMap((item) => item.anchor && numeric(item.anchor) ? [pointDay(item.anchor)] : []).sort().at(-1);
    if (latest && start <= subtractMonths(latest, 84)) break;
    end = iso(new Date(Date.parse(start) - dayMs));
  }
  const first = chunks[0];
  const last = chunks[chunks.length - 1];
  // Monthly/quarterly rows can intersect both sides of a chunk boundary.
  // Keep one official observation per source period, without resampling it.
  const observations = [...new Map(chunks.flatMap((chunk) => chunk.observations)
    .map((row) => [row.period_raw, row])).values()]
    .sort((a, b) => pointDay(a).localeCompare(pointDay(b)));
  const captures = chunks.flatMap((chunk) => chunk.updated_at ? [chunk.updated_at] : []).sort();
  return { ...last, start: first.start, observations, anchor: first.anchor,
    updated_at: captures.at(-1) ?? null, chunks };
}

export function selectHistory(history: ChartHistory, period: ChartPeriod): ChartHistory {
  const readings = history.observations.filter(numeric);
  const latest = readings.at(-1) ?? (history.anchor && numeric(history.anchor) ? history.anchor : undefined);
  // An effective rate remains a reading through its verified coverage boundary.
  const latestReading = latest ? pointDay(latest) : history.end;
  const verifiedEnd = history.chunks.flatMap((chunk) => chunk.coverage_end ? [chunk.coverage_end] : []).sort().at(-1);
  const end = history.indicator.frequency === 'effective_date' && verifiedEnd
    ? [latestReading, verifiedEnd].sort().at(-1)! : latestReading;
  const start = subtractMonths(end, { '1M': 1, '3M': 3, '6M': 6, '1Y': 12, '3Y': 36, '7Y': 84 }[period]);
  const observations = history.observations.filter((row) => pointDay(row) >= start && pointDay(row) <= end);
  const prior = [history.anchor, ...history.observations].filter((row): row is MacroObservation => row !== null && pointDay(row) < start).at(-1);
  return { ...history, start, end, observations,
    anchor: history.indicator.frequency === 'effective_date' ? prior ?? null : null };
}

export function rateSegments(history: ChartHistory) {
  return history.chunks.flatMap((chunk) => {
    if (!chunk.coverage_start || !chunk.coverage_end) return [];
    const start = [chunk.coverage_start, history.start].sort().at(-1)!;
    const end = [chunk.coverage_end, history.end].sort()[0];
    const events = [...(chunk.anchor ? [chunk.anchor] : []), ...chunk.observations]
      .sort((a, b) => pointDay(a).localeCompare(pointDay(b)));
    return events.flatMap((row, index) => {
      const first = pointDay(row) < start ? start : pointDay(row);
      const next = events[index + 1];
      const last = next && pointDay(next) < end ? pointDay(next) : end;
      if (!numeric(row) || first >= last) return [];
      return [{ first, last, value: Number(row.value),
        nextValue: next && numeric(next) && pointDay(next) === last && last <= end ? Number(next.value) : null }];
    });
  });
}

// Keep research calls compatible with the existing 3,660-day API bound.
export function researchWindow(history: MacroHistory) {
  const earliest = iso(new Date(Date.parse(history.end) - 3660 * dayMs));
  return { start: history.start < earliest ? earliest : history.start, end: history.end };
}
