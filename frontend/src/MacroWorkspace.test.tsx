// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from './App';
import type { MacroIndicator, MacroObservation } from './macro-api';

vi.mock('./PriceChart', () => ({ PriceChart: () => null }));

const entries: [string, string, MacroIndicator['frequency'], string, number][] = [
  ['eur-usd', 'EUR/USD', 'daily', 'USD/EUR', 0],
  ['m3-stock', 'M3 stock', 'monthly', 'EUR', 6],
  ['deposit-rate', 'Deposit rate', 'effective_date', 'percent_per_annum', 0],
  ['real-gdp-qoq', 'Real GDP', 'quarterly', 'percent_qoq', 0],
  ['hicp-yoy', 'HICP', 'monthly', 'percent_yoy', 0],
];
const catalog: MacroIndicator[] = entries.map(([id, title, frequency, unit, multiplier]) => ({
  id, title, frequency, unit, multiplier, provider: 'ecb', series_key: 'fixture',
  geography: 'fixture', adjustment: 'fixture', source_url: 'https://data.ecb.europa.eu/',
  reuse_url: 'https://www.ecb.europa.eu/', attribution: 'ECB statistics',
}));

function point(raw: string, start: string, end: string, value: string | null): MacroObservation {
  return {
    period_raw: raw, period_start: start, period_end: end, effective_date: null, value,
    status_kind: value === null ? 'missing' : 'normal', status_raw: null,
  };
}

function event(day: string, value: string): MacroObservation {
  return { ...point(day, day, day, value), effective_date: day };
}

function stubMacroApi(observations: MacroObservation[], anchor: MacroObservation | null = null) {
  const fetch = vi.fn(async (request: string) => {
    const url = new URL(request, location.origin);
    if (url.pathname === '/api/macro/indicators') return Response.json(catalog);
    const indicator = catalog.find(({ id }) => url.pathname === `/api/macro/indicators/${id}/history`);
    if (!indicator) return new Response(null, { status: 404 });
    const deposit = indicator.id === 'deposit-rate';
    return Response.json({
      indicator, start: url.searchParams.get('start'), end: url.searchParams.get('end'),
      updated_at: '2026-06-25T12:00:00Z', observations, anchor: deposit ? anchor : null,
      coverage_start: deposit ? '2025-07-01' : null, coverage_end: deposit ? '2026-06-25' : null,
    });
  });
  vi.stubGlobal('fetch', fetch);
  vi.setSystemTime(new Date('2026-07-01T00:00:00Z'));
  return fetch;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
  window.history.replaceState(null, '', '/');
});

it('opens macro by default in the enabled public build and hides the research form', async () => {
  vi.stubEnv('MODE', 'public-demo');
  const fetch = stubMacroApi([]);
  render(<App macroDemoEnabled macroAiEnabled />);
  await screen.findByText('AI research is available only in an enabled local workspace.');
  expect(screen.queryByLabelText('Research question')).toBeNull();
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});

it('plots monthly observations without filling gaps and preserves source decimals and units', async () => {
  const fetch = stubMacroApi([
    point('2026-04', '2026-04-01', '2026-04-30', null),
    point('2026-06', '2026-06-01', '2026-06-30', '17612461.010'),
  ]);
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'Macroeconomic evidence explorer' })).toBeTruthy();
  fireEvent.change(await screen.findByLabelText('Indicator'), { target: { value: 'm3-stock' } });
  const svg = await screen.findByLabelText('Official macro observations by period');
  expect(svg.querySelectorAll('circle')).toHaveLength(1);
  expect(svg.querySelector('.macro-segment')).toBeNull();
  expect(screen.getByText(/1 marked missing/)).toBeTruthy();
  expect(within(screen.getByRole('table')).getByText('17612461.010')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'ECB statistics' }).getAttribute('href')).toBe('https://data.ecb.europa.eu/');
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});

it('displays deposit events separately from bounded applicability', async () => {
  stubMacroApi([event('2026-06-17', '2.25')], event('2025-06-11', '2'));
  render(<App />);
  fireEvent.change(await screen.findByLabelText('Indicator'), { target: { value: 'deposit-rate' } });
  const svg = await screen.findByLabelText(/Deposit rate events/);
  expect(svg.querySelectorAll('circle')).toHaveLength(1);
  expect(svg.querySelectorAll('.macro-segment')).toHaveLength(2);
  expect(screen.getByText(/through 2026-06-25/)).toBeTruthy();
  expect(within(screen.getByRole('table')).getByText('2025-06-11 (prior event)')).toBeTruthy();
});
