// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
import type { MacroHistory, MacroIndicator, MacroObservation } from './macro-api';
import { MacroPlot } from './MacroPlot';
import { chartPeriods, indicatorStyles, loadChartHistory, researchWindow, rateSegments, selectHistory, subtractMonths, type ChartHistory } from './macro-chart';

const indicator: MacroIndicator = {
  id: 'eur-usd', title: 'Institutional original title', frequency: 'daily', unit: 'USD/EUR', multiplier: 0,
  provider: 'ecb', series_key: 'fixture', geography: 'fixture', adjustment: 'fixture',
  source_url: 'https://data.ecb.europa.eu/', reuse_url: 'https://www.ecb.europa.eu/', attribution: 'ECB statistics',
};
const point = (day: string, value: string | null): MacroObservation => ({
  period_raw: day, period_start: day, period_end: day, effective_date: null,
  value, status_kind: value === null ? 'missing' : 'normal', status_raw: null,
});
function history(observations: MacroObservation[], overrides: Partial<MacroHistory> = {}): ChartHistory {
  const chunk: MacroHistory = { indicator, start: '2016-07-01', end: '2026-07-01',
    observations, anchor: null, updated_at: null, coverage_start: null, coverage_end: null, ...overrides };
  return { ...chunk, chunks: [chunk] };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([
  ['1M', '2026-05-30'], ['3M', '2026-03-30'], ['6M', '2025-12-30'],
  ['1Y', '2025-06-30'], ['3Y', '2023-06-30'], ['7Y', '2019-06-30'],
] as const)('anchors %s to the latest numeric reading rather than today', (period, start) => {
  const result = selectHistory(history([point('2020-01-01', '1'), point('2026-06-30', '2'), point('2026-07-01', null)]), period);
  expect(result.start).toBe(start);
  expect(result.end).toBe('2026-06-30');
  expect(result.observations.every((row) => row.period_end >= start && row.period_end <= result.end)).toBe(true);
});

it('clamps calendar months at leap days and month ends', () => {
  expect(subtractMonths('2024-03-31', 1)).toBe('2024-02-29');
  expect(subtractMonths('2024-02-29', 12)).toBe('2023-02-28');
  expect(chartPeriods).toHaveLength(6);
});

it('connects real daily readings across weekends with proportional temporal spacing', () => {
  const rows = [point('2026-06-05', '1'), point('2026-06-08', '1.2'), point('2026-06-09', '1.1')];
  const { container } = render(<MacroPlot history={history(rows)} />);
  const svg = container.querySelector('svg')!;
  expect(svg.dataset.domainStart).toBe('2026-06-05');
  expect(svg.dataset.domainEnd).toBe('2026-06-09');
  const dots = [...svg.querySelectorAll('circle')];
  expect(dots).toHaveLength(3);
  const xs = dots.map((dot) => Number(dot.getAttribute('cx')));
  expect((xs[1] - xs[0]) / (xs[2] - xs[1])).toBeCloseTo(3);
  expect(svg.querySelector('.macro-line')?.getAttribute('points')?.split(' ')).toHaveLength(3);
  expect(dots[1].textContent).toContain('2026-06-08 · 1.2 USD per EUR');
});

it.each([
  ['m3-stock', 'monthly', '2026-02-28', '2026-06-30', 'Jun 2026'],
  ['hicp-yoy', 'monthly', '2026-02-28', '2026-06-30', 'Jun 2026'],
  ['real-gdp-qoq', 'quarterly', '2025-12-31', '2026-06-30', '2026 Q2'],
] as const)('keeps %s observations sparse and uses frequency-aware ticks', (id, frequency, first, last, tick) => {
  const data = history([point(first, '1'), point(last, '2')], { indicator: { ...indicator, id, frequency } });
  const { container, unmount } = render(<MacroPlot history={selectHistory(data, '1Y')} />);
  expect(container.querySelectorAll('circle')).toHaveLength(2);
  expect(container.querySelector('.macro-outline')).toBeTruthy();
  expect(screen.getByText(tick)).toBeTruthy();
  unmount();
  const sparse = render(<MacroPlot history={selectHistory(data, '1M')} />);
  expect(sparse.container.querySelectorAll('circle')).toHaveLength(1);
  expect(sparse.container.querySelector('polyline')).toBeNull();
});

it('draws vertical BCE changes and holds rates only within verified coverage', () => {
  const data = history([point('2026-06-10', '2'), point('2026-06-17', '2.25')], {
    indicator: { ...indicator, id: 'deposit-rate', frequency: 'effective_date' },
    coverage_start: '2026-06-01', coverage_end: '2026-06-25', anchor: point('2026-05-10', '1.75'),
  });
  const selected = selectHistory(data, '1M');
  expect(selected.end).toBe('2026-06-25');
  expect(rateSegments(selected).map(({ first, last }) => [first, last])).toEqual([
    ['2026-06-01', '2026-06-10'], ['2026-06-10', '2026-06-17'], ['2026-06-17', '2026-06-25'],
  ]);
  const { container } = render(<MacroPlot history={selected} />);
  const steps = [...container.querySelectorAll('.macro-step')];
  expect(steps).toHaveLength(2);
  expect(steps.every((line) => line.getAttribute('x1') === line.getAttribute('x2'))).toBe(true);
  expect(container.querySelector('polyline')).toBeNull();
  expect(container.querySelectorAll('circle')).toHaveLength(2);
});

it('does not extend a BCE step over missing values or unverified intervals', () => {
  const data = history([point('2026-06-10', '2'), point('2026-06-15', null), point('2026-06-20', '2.25')], {
    indicator: { ...indicator, id: 'deposit-rate', frequency: 'effective_date' },
    coverage_start: '2026-06-01', coverage_end: '2026-06-25',
  });
  expect(rateSegments(data).map(({ first, last, nextValue }) => [first, last, nextValue])).toEqual([
    ['2026-06-10', '2026-06-15', null], ['2026-06-20', '2026-06-25', null],
  ]);
  data.chunks = [{ ...data.chunks[0], coverage_start: null, coverage_end: null }];
  expect(rateSegments(data)).toEqual([]);
});

it('loads seven years through API-compatible windows without unbounded history', async () => {
  vi.setSystemTime(new Date('2026-07-01T00:00:00Z'));
  const fetch = vi.fn(async (request: string) => {
    const url = new URL(request, 'http://localhost');
    const start = url.searchParams.get('start')!;
    const end = url.searchParams.get('end')!;
    const rows = [point('2019-06-30', '1.17'), point('2026-06-30', '1.1')]
      .filter((row) => row.period_end >= start && row.period_end <= end);
    return Response.json({ ...history(rows), start, end });
  });
  vi.stubGlobal('fetch', fetch);
  const result = selectHistory(await loadChartHistory('eur-usd', new AbortController().signal), '7Y');
  expect(result.observations.map((row) => row.period_raw)).toEqual(['2019-06-30', '2026-06-30']);
  expect(result.start).toBe('2019-06-30');
  const windows = fetch.mock.calls.map(([request]) => {
    const url = new URL(request, 'http://localhost');
    return [url.searchParams.get('start')!, url.searchParams.get('end')!];
  });
  expect(windows).toHaveLength(1);
  expect(windows[0][0] <= '2019-06-30').toBe(true);
  windows.forEach(([start, end], index) => {
    expect((Date.parse(end) - Date.parse(start)) / 86400000).toBeLessThan(2600);
    if (index) expect(Date.parse(windows[index - 1][0]) - Date.parse(end)).toBe(86400000);
  });
});

it('provides 3:1 contrast using the exact data color or its neutral edge on light and dark surfaces', () => {
  const luminance = (hex: string) => {
    const values = hex.slice(1).match(/../g)!.map((part) => {
      const value = parseInt(part, 16) / 255;
      return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
    });
    return values[0] * .2126 + values[1] * .7152 + values[2] * .0722;
  };
  for (const { color } of Object.values(indicatorStyles)) {
    for (const background of ['#111214', '#FFFFFF']) {
      const values = [luminance(color), luminance(background)].sort((a, b) => a - b);
      const contrast = (values[1] + .05) / (values[0] + .05);
      const edge = [luminance('#111214'), luminance(background)].sort((a, b) => a - b);
      expect(Math.max(contrast, (edge[1] + .05) / (edge[0] + .05))).toBeGreaterThanOrEqual(3);
    }
  }
});

it('keeps the seven-year research range compatible with the unchanged bounded API', () => {
  const data = history([point('2019-06-30', '1.17'), point('2026-06-30', '1.1')]);
  const selected = selectHistory(data, '7Y');
  const research = researchWindow(selected);
  expect(selected.start).toBe('2019-06-30');
  expect(research).toEqual({ start: '2019-06-30', end: '2026-06-30' });
  expect(researchWindow(selectHistory(data, '3Y'))).toEqual({ start: '2023-06-30', end: '2026-06-30' });
});

it('handles all-missing and unverified anchor-only data without inventing plot points', () => {
  const { container, unmount } = render(<MacroPlot history={history([point('2026-06-30', null)])} />);
  expect(container.querySelector('svg')).toBeNull();
  expect(screen.getByText('No numeric observations in this period.')).toBeTruthy();
  unmount();
  const data = history([], { anchor: point('2020-01-01', '2'),
    indicator: { ...indicator, id: 'deposit-rate', frequency: 'effective_date' } });
  const rendered = render(<MacroPlot history={selectHistory(data, '1M')} />);
  expect(rendered.container.querySelectorAll('circle, line')).toHaveLength(0);
});

it('searches older chunks for stale series and loads enough history for the seven-year preset', async () => {
  vi.setSystemTime(new Date('2026-07-01T00:00:00Z'));
  const fetch = vi.fn(async (request: string) => {
    const url = new URL(request, 'http://localhost');
    const start = url.searchParams.get('start')!;
    const end = url.searchParams.get('end')!;
    const rows = [point('2005-01-01', '1'), point('2007-01-01', '2')]
      .filter((row) => row.period_end >= start && row.period_end <= end);
    return Response.json({ ...history(rows), start, end });
  });
  vi.stubGlobal('fetch', fetch);
  const result = selectHistory(await loadChartHistory('eur-usd', new AbortController().signal), '3Y');
  expect(result.start).toBe('2004-01-01');
  expect(result.end).toBe('2007-01-01');
  expect(result.observations).toHaveLength(2);
  expect(fetch).toHaveBeenCalledTimes(4);
});

it('stops chunk retrieval when aborted', async () => {
  const controller = new AbortController();
  const fetch = vi.fn(async (request: string) => {
    const url = new URL(request, 'http://localhost');
    controller.abort();
    return Response.json({ ...history([]), start: url.searchParams.get('start'), end: url.searchParams.get('end') });
  });
  vi.stubGlobal('fetch', fetch);
  await expect(loadChartHistory('eur-usd', controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('loads all five indicators and six presets through the actual public Worker with a mocked origin', async () => {
  const workerPath = '../worker/public-demo.mjs';
  const { default: worker } = await import(workerPath);
  const upstream = vi.fn(async (target: URL) => {
    const start = target.searchParams.get('start')!;
    const end = target.searchParams.get('end')!;
    const id = target.pathname.split('/')[3];
    const frequency = id === 'deposit-rate' ? 'effective_date'
      : id === 'real-gdp-qoq' ? 'quarterly' : id === 'eur-usd' ? 'daily' : 'monthly';
    const rows = [point('2000-01-01', '1'), point('2026-06-30', '2')]
      .filter((row) => row.period_end >= start && row.period_end <= end);
    return Response.json({ ...history(rows), indicator: { ...indicator, id, frequency }, start, end });
  });
  const env = {
    MARKETPULSE_DEMO_MACRO_ENABLED: 'true',
    MARKETPULSE_DEMO_ORIGIN_URL: 'https://marketpulse-fixture.onrender.com',
    MARKETPULSE_DEMO_ORIGIN_SECRET: createHash('sha256').update('synthetic integration fixture').digest('hex'),
    HISTORY_RATE_LIMIT: { limit: async () => ({ success: true }) },
  };
  vi.stubGlobal('fetch', (target: string | URL) => typeof target === 'string'
    ? worker.fetch(new Request(`https://portfolio.example${target}`, {
      headers: { 'CF-Connecting-IP': '192.0.2.3', Origin: 'https://portfolio.example' },
    }), env) : upstream(target));
  vi.setSystemTime(new Date('2026-07-01T00:00:00Z'));
  for (const id of Object.keys(indicatorStyles)) {
    const loaded = await loadChartHistory(id, new AbortController().signal);
    expect(loaded.observations).toHaveLength(1);
    for (const period of chartPeriods) {
      expect(selectHistory(loaded, period).observations.at(-1)?.value).toBe('2');
    }
  }
  expect(upstream.mock.calls.length).toBe(5);
});

it('deduplicates monthly source periods that intersect adjacent retrieval windows', async () => {
  vi.setSystemTime(new Date('2026-07-01T00:00:00Z'));
  const boundary = new Date(Date.parse('2026-07-01') - 2599 * 86400000).toISOString().slice(0, 7);
  const row = { ...point(`${boundary}-31`, '17612461.010'),
    period_raw: boundary, period_start: `${boundary}-01` };
  vi.stubGlobal('fetch', vi.fn(async (request: string) => {
    const url = new URL(request, 'http://localhost');
    const start = url.searchParams.get('start')!;
    const end = url.searchParams.get('end')!;
    const observations = row.period_end >= start && row.period_start <= end ? [row] : [];
    return Response.json({ ...history(observations), indicator: { ...indicator, id: 'm3-stock', frequency: 'monthly' }, start, end });
  }));
  const result = await loadChartHistory('m3-stock', new AbortController().signal);
  expect(result.observations).toEqual([row]);
  expect(result.chunks.length).toBeGreaterThan(1);
});

it('shows the exact decimal in a colored tooltip for pointer and keyboard interaction', () => {
  const rows = [point('2026-06-05', '1.000000000001'), point('2026-06-08', '1.250000000002')];
  const { container } = render(<MacroPlot history={history(rows)} />);
  const svg = container.querySelector('svg')!;
  fireEvent.pointerEnter(svg.querySelector('circle')!);
  expect(screen.getByRole('tooltip').textContent).toContain('2026-06-05 \u00b7 1.000000000001 USD per EUR');
  expect(screen.getByRole('tooltip').getAttribute('style')).toContain('#16A34A');
  fireEvent.pointerLeave(svg);
  expect(screen.queryByRole('tooltip')).toBeNull();
  fireEvent.focus(svg);
  expect(screen.getByRole('tooltip').textContent).toContain('1.250000000002');
  fireEvent.keyDown(svg, { key: 'ArrowLeft' });
  expect(screen.getByRole('tooltip').textContent).toContain('1.000000000001');
  fireEvent.keyDown(svg, { key: 'End' });
  expect(screen.getByRole('tooltip').textContent).toContain('1.250000000002');
  fireEvent.keyDown(svg, { key: 'Home' });
  expect(screen.getByRole('tooltip').textContent).toContain('1.000000000001');
  fireEvent.keyDown(svg, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect(svg.getAttribute('aria-describedby')).not.toContain('undefined');
});

it('keeps keyboard selection valid when changing from a long series to a short series', () => {
  const { container, rerender } = render(<MacroPlot history={history([
    point('2026-06-01', '1'), point('2026-06-02', '2'), point('2026-06-03', '3'),
  ])} />);
  fireEvent.focus(container.querySelector('svg')!);
  rerender(<MacroPlot history={history([point('2026-06-03', '9')])} />);
  const svg = container.querySelector('svg')!;
  expect(svg.getAttribute('aria-valuenow')).toBe('0');
  expect(screen.getByRole('tooltip').textContent).toContain('9 USD per EUR');
  fireEvent.keyDown(svg, { key: 'ArrowLeft' });
  expect(svg.getAttribute('aria-valuenow')).toBe('0');
});

it.each([
  ['m3-stock', 'EUR', 6, 'Millions of EUR'],
  ['eur-usd', 'USD/EUR', 0, 'USD per EUR'],
  ['deposit-rate', 'percent_per_annum', 0, '% per year'],
  ['real-gdp-qoq', 'percent_qoq', 0, '% quarter on quarter'],
  ['hicp-yoy', 'percent_yoy', 0, '% year on year'],
] as const)('preserves the source magnitude and readable unit for %s in tooltip and accessible text', (id, unit, multiplier, label) => {
  const { container } = render(<MacroPlot history={history([point('2026-06-30', '17612461.010')], {
    indicator: { ...indicator, id, unit, multiplier },
  })} />);
  const svg = container.querySelector('svg')!;
  fireEvent.focus(svg);
  const exactReading = `17612461.010 ${label}`;
  expect(screen.getByRole('tooltip').textContent).toContain(exactReading);
  expect(svg.getAttribute('aria-valuetext')).toContain(exactReading);
  expect(svg.querySelector('title')?.textContent).toContain(label);
  expect(svg.querySelector('circle title')?.textContent).toContain(exactReading);
});
