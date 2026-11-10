// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from './App';
import type { MacroIndicator, MacroObservation } from './macro-api';


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
    const start = url.searchParams.get('start')!;
    const end = url.searchParams.get('end')!;
    const all = deposit && anchor ? [anchor, ...observations] : observations;
    const rows = all.filter((row) => (row.effective_date ?? row.period_end) >= start && (row.effective_date ?? row.period_end) <= end);
    const prior = deposit ? all.filter((row) => (row.effective_date ?? row.period_end) < start).at(-1) : null;
    return Response.json({
      indicator, start: url.searchParams.get('start'), end: url.searchParams.get('end'),
      updated_at: '2026-06-25T12:00:00Z', observations: rows, anchor: prior ?? null,
      coverage_start: deposit && start <= '2026-06-25' && end >= '2025-07-01' ? (start > '2025-07-01' ? start : '2025-07-01') : null,
      coverage_end: deposit && start <= '2026-06-25' && end >= '2025-07-01' ? (end < '2026-06-25' ? end : '2026-06-25') : null,
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

it.each(['/', '/?workspace=market'])('opens macro in the enabled public build at %s and hides the research form', async (path) => {
  window.history.replaceState(null, '', path);
  vi.stubEnv('MODE', 'public-demo');
  const fetch = stubMacroApi([]);
  render(<App macroDemoEnabled macroAiEnabled />);
  await screen.findByText('AI research is available only in an enabled local workspace.');
  expect(screen.queryByLabelText('Research question')).toBeNull();
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});

it.each([
  ['eur-usd', 'EUR/USD rate'], ['m3-stock', 'Eurozone M3'], ['deposit-rate', 'ECB rate'],
  ['real-gdp-qoq', 'Eurozone GDP'], ['hicp-yoy', 'Eurozone Inflation'],
].flatMap(([id, label]) => [
  [id, label, 'COVID-19', 'How did the COVID-19 pandemic affect'],
  [id, label, 'Ukraine War', 'How did the war in Ukraine affect'],
  [id, label, 'Generative AI', 'How did the rise of generative AI and LLMs affect'],
]))('passes the selected indicator to research without submitting: %s / %s / %s', async (id, label, event, prefix) => {
  const fetch = stubMacroApi([point('2026-06', '2026-06-01', '2026-06-30', '2')]);
  render(<App macroAiEnabled />);
  const selector = await screen.findByLabelText('Indicator');
  fireEvent.change(selector, { target: { value: id } });
  fireEvent.click(await screen.findByRole('button', { name: event }));
  expect(screen.getByLabelText('Research question')).toHaveProperty('value', `${prefix} ${label}?`);
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});

it('submits a customized question with the selected chart period only after Ask', async () => {
  const historyFetch = stubMacroApi([point('2026-06', '2026-06-01', '2026-06-30', '2')]);
  const analysis = vi.fn(async (_url: string, init: RequestInit) => Response.json({
    ...JSON.parse(String(init.body)), status: 'insufficient_evidence', facts: [], calculations: [],
    interpretation: [], limitations: ['Synthetic fixture'], evidence: [], graph_steps: ['abstain'],
  }));
  vi.stubGlobal('fetch', (url: string, init: RequestInit) => url === '/api/macro/analysis'
    ? analysis(url, init) : historyFetch(url));
  render(<App macroAiEnabled />);
  await screen.findByRole('button', { name: 'COVID-19' });
  fireEvent.click(screen.getByRole('button', { name: '3M' }));
  fireEvent.click(screen.getByRole('button', { name: 'COVID-19' }));
  fireEvent.change(screen.getByLabelText('Research question'), {
    target: { value: 'How did the Federal Reserve change interest rates?' },
  });
  expect(analysis).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('There is insufficient evidence for an answer.');
  expect(analysis).toHaveBeenCalledTimes(1);
  expect(JSON.parse(String(analysis.mock.calls[0][1].body))).toEqual({
    question: 'How did the Federal Reserve change interest rates?', start: '2026-03-30', end: '2026-06-30',
  });
});

it('plots monthly observations without filling gaps and preserves source decimals and units', async () => {
  const fetch = stubMacroApi([
    point('2026-04', '2026-04-01', '2026-04-30', null),
    point('2026-06', '2026-06-01', '2026-06-30', '17612461.010'),
  ]);
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'Macroeconomic evidence explorer' })).toBeTruthy();
  fireEvent.change(await screen.findByLabelText('Indicator'), { target: { value: 'm3-stock' } });
  const svg = await screen.findByLabelText('Eurozone M3: official observations by period');
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
  const svg = await screen.findByLabelText(/ECB rate: effective events/);
  expect(svg.querySelectorAll('circle')).toHaveLength(1);
  expect(svg.querySelectorAll('.macro-segment')).toHaveLength(2);
  expect(screen.getByText(/through 2026-06-25/)).toBeTruthy();
  expect(within(screen.getByRole('table')).getByText('2025-06-11 (prior event)')).toBeTruthy();
});

it('shows five friendly names and colors and preserves the selected period across indicators', async () => {
  const fetch = stubMacroApi([
    point('2026-01', '2026-01-01', '2026-01-31', '1'),
    point('2026-06', '2026-06-01', '2026-06-30', '2'),
  ]);
  render(<App />);
  await screen.findByLabelText('EUR/USD rate: official observations by period');
  const periods = within(screen.getByRole('group', { name: 'History period' }));
  expect(periods.getAllByRole('button').map((button) => button.textContent)).toEqual(['1M', '3M', '6M', '1Y', '3Y', 'MAX']);
  fireEvent.click(periods.getByRole('button', { name: '3M' }));
  const expected = [
    ['eur-usd', 'EUR/USD rate', '#16A34A'], ['m3-stock', 'Eurozone M3', '#2563EB'],
    ['deposit-rate', 'ECB rate', '#EA580C'], ['real-gdp-qoq', 'Eurozone GDP', '#C58B13'],
    ['hicp-yoy', 'Eurozone Inflation', '#9333EA'],
  ];
  for (const [id, label, color] of expected) {
    fireEvent.change(screen.getByLabelText('Indicator'), { target: { value: id } });
    const svg = await screen.findByLabelText(new RegExp(`${label}:`));
    expect(svg.getAttribute('style')).toContain(color);
    expect(screen.getByRole('heading', { name: label })).toBeTruthy();
    expect(screen.getByRole('option', { name: label }).getAttribute('value')).toBe(id);
    expect(periods.getByRole('button', { name: '3M' }).getAttribute('aria-pressed')).toBe('true');
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(id === 'deposit-rate' ? 3 : 2);
  }
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});

it('recovers a failed history request and changes finite periods without refetching', async () => {
  const fixture = stubMacroApi([point('2026-06', '2026-06-01', '2026-06-30', '2')]);
  let fail = true;
  vi.stubGlobal('fetch', vi.fn(async (request: string) => {
    if (fail && request.includes('/history?')) return new Response(null, { status: 503 });
    return fixture(request);
  }));
  render(<App />);
  await screen.findByText('Unable to load official observations.');
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByLabelText('EUR/USD rate: official observations by period');
  const before = fixture.mock.calls.length;
  for (const period of ['1M', '3M', '6M', '1Y', '3Y']) {
    fireEvent.click(screen.getByRole('button', { name: period }));
    expect(screen.getByRole('button', { name: period }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByLabelText('EUR/USD rate: official observations by period')).toBeTruthy();
  }
  expect(fixture.mock.calls).toHaveLength(before);
});

it('selects MAX and updates the table and chart to the complete history without an analysis call', async () => {
  const fetch = stubMacroApi([
    point('2010-01-04', '2010-01-04', '2010-01-04', '1.43'),
    point('2026-06-30', '2026-06-30', '2026-06-30', '1.10'),
  ]);
  render(<App />);
  await screen.findByLabelText('EUR/USD rate: official observations by period');
  fireEvent.click(screen.getByRole('button', { name: 'MAX' }));
  const svg = await screen.findByLabelText('EUR/USD rate: official observations by period');
  expect(svg.getAttribute('data-domain-start')).toBe('2010-01-04');
  expect(svg.getAttribute('data-domain-end')).toBe('2026-06-30');
  expect(svg.querySelectorAll('circle')).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'MAX' }).getAttribute('aria-pressed')).toBe('true');
  expect(within(screen.getByRole('table')).getByText('1.43')).toBeTruthy();
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});
