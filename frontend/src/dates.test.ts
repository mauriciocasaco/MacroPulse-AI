import { describe, expect, it } from 'vitest';
import { chartData, priceDomain, sessionDomain, sessionLabels, sessionTicks } from './chart-data';
import { dateToSeconds, historyWindow, secondsToDate } from './dates';
import type { DailyBar } from './api';
import { formatPercentage, formatPrice } from './presentation';

const sample = (bar_date: string, close: string | null, adjusted_close: string | null): DailyBar => ({
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
  const bars = [sample('2020-04-20', '-37.630001068115234', null), sample('2020-04-17', '18.27', '19.35')];

  it.each([
    ['2020-04-17', '2020-04-20'], // weekend
    ['2020-12-24', '2020-12-28'], // market holiday and weekend
    ['2020-07-14', '2020-07-16'], // missing weekday
    ['2017-01-02', '2026-11-09'], // sparse history
    ['2020-04-18', '2020-04-19'], // daily Bitcoin data on a weekend
  ])('places %s and %s on consecutive real positions with their original dates', (first, last) => {
    const { indices, dates, prices, observations } = chartData([
      sample(last, '-37.630001068115234', null), sample(first, '18.27', '19.35'),
    ], 'close');
    expect(indices).toEqual([0, 1]);
    expect(dates.map(secondsToDate)).toEqual([first, last]);
    expect(prices).toEqual([18.27, -37.630001068115234]);
    expect(observations[1].close).toBe('-37.630001068115234');
  });

  it('distinguishes absent sessions from invalid records without adding plotted observations', () => {
    const result = chartData([...bars, sample('2020-04-21', null, null), sample('2020-04-22', 'invalid', null)], 'close');
    expect(result.prices).toEqual([18.27, -37.630001068115234]);
    expect(result.indexByDate.get('2020-04-18')).toBeUndefined();
    expect(result.indexByDate.get('2020-04-21')).toBeNull();
    expect(result.indexByDate.get('2020-04-22')).toBeNull();
  });

  it('keeps Adjusted Close separate without backfilling and accepts empty series', () => {
    expect(chartData(bars, 'adjusted_close').prices).toEqual([19.35]);
    expect(chartData([bars[0]], 'adjusted_close').prices).toEqual([]);
    expect(chartData([], 'close').dates).toEqual([]);
  });

  it('pads singleton and multi-observation domains and only labels real positions', () => {
    const [min, max] = sessionDomain(0, 100);
    expect(min).toBeLessThan(0);
    expect(max).toBeGreaterThan(100);
    expect(sessionTicks(min, max, 160, 100)).toEqual([0, 100]);
    expect(sessionTicks(min, max, 850, 100)).toEqual([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]);
    expect(sessionTicks(...sessionDomain(0, 0), 160, 0)).toEqual([0]);
    expect(sessionLabels([0, 1], chartData(bars, 'close').dates)).toEqual(['Apr 17', 'Apr 20']);
    expect(sessionLabels([0, 1], ['2020-04-20', '2026-04-20'].map(dateToSeconds)))
      .toEqual(['Apr 20, 20', 'Apr 20, 26']);
  });

  it.each([
    [100, 120], [-50, -37.630001068115234], [-37.630001068115234, 18.27],
    [0, 0], [10, 10], [-20, -20], [100, 100.000001],
  ])('pads %s to %s without clipping or forcing an unrelated zero baseline', (min, max) => {
    const [paddedMin, paddedMax] = priceDomain(min, max);
    expect(paddedMin).toBeLessThan(min);
    expect(paddedMax).toBeGreaterThan(max);
    if (min > 0) expect(paddedMin).toBeGreaterThan(0);
    if (max < 0) expect(paddedMax).toBeLessThan(0);
  });
});

describe('financial presentation', () => {
  it.each([
    ['90.83000183105469', '90.8'], ['-37.630001068115234', '-37.6'],
    ['2410.28369140625', '2,410.3'], ['12345.6789', '12,345.7'], [0, '0.0'],
    ['1.44999999999999999', '1.4'],
  ])('formats %s to one decimal without mutating the stored price', (value, expected) => {
    expect(formatPrice(value)).toBe(expected);
  });

  it.each([[14.249, '+14.2%'], [-8.72, '-8.7%'], [0, '0.0%']])('formats percentage points %s separately', (value, expected) => {
    expect(formatPercentage(value)).toBe(expected);
  });
});
