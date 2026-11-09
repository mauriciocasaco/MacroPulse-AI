import { describe, expect, it } from 'vitest';
import { chartData } from './chart-data';
import { dateToSeconds, historyWindow, secondsToDate } from './dates';
import type { DailyBar } from './api';

const sample = (bar_date: string, close: string, adjusted_close: string | null): DailyBar => ({
  bar_date, close, adjusted_close, retrieved_at: '2026-11-09T14:20:00Z', availability_time: null,
  session_status: 'closed', quality_status: 'complete', source_convention: 'fixture', rolling_method: null,
});

describe('calendar windows', () => {
  it('clamps February and includes the latest stored day with an exclusive upper bound', () => {
    expect(historyWindow('2017-01-01', '2024-03-31', '1m')).toEqual({
      start: '2024-02-29', end_exclusive: '2024-04-01',
    });
    expect(historyWindow('2017-01-01', '2024-02-29', '1y')).toEqual({
      start: '2023-02-28', end_exclusive: '2024-03-01',
    });
  });

  it('clips windows to the stored coverage and does not depend on the system clock', () => {
    expect(historyWindow('2024-03-20', '2024-03-31', '3y')).toEqual({
      start: '2024-03-20', end_exclusive: '2024-04-01',
    });
    expect(historyWindow('2020-01-01', '2024-12-31', 'max')).toEqual({
      start: '2020-01-01', end_exclusive: '2025-01-01',
    });
    expect(secondsToDate(dateToSeconds('2024-11-03'))).toBe('2024-11-03');
  });
});

describe('chart data', () => {
  const bars = [sample('2020-04-20', '-37.630000', null), sample('2020-04-17', '18.27', '19.35')];

  it('orders dates, inserts unpriced calendar gaps, and preserves negative and exact values', () => {
    const { dates, prices, byDate, isolated } = chartData(bars, 'close');
    expect(dates.map(secondsToDate)).toEqual(['2020-04-17', '2020-04-18', '2020-04-19', '2020-04-20']);
    expect(prices).toEqual([18.27, null, null, -37.63]);
    expect(byDate.get(dateToSeconds('2020-04-20'))?.close).toBe('-37.630000');
    expect(byDate.has(dateToSeconds('2020-04-19'))).toBe(false);
    expect(isolated).toEqual([0, 3]);
  });

  it('keeps Adjusted Close separate without backfilling with Close', () => {
    expect(chartData(bars, 'adjusted_close').prices).toEqual([19.35, null, null, null]);
    expect(chartData([], 'close').dates).toEqual([]);
  });
});
