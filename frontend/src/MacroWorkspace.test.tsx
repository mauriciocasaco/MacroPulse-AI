// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  vi.stubEnv('MARKETPULSE_DEMO_MACRO_ENABLED', undefined);
  render(<App macroAiEnabled />);
  await screen.findByText('Public AI research is currently unavailable.');
  expect(screen.queryByLabelText('Research question')).toBeNull();
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});

it.each([
  ['eur-usd', 'EUR/USD rate'], ['m3-stock', 'Eurozone M3'], ['deposit-rate', 'ECB rate'],
  ['real-gdp-qoq', 'Eurozone GDP'], ['hicp-yoy', 'Eurozone Inflation'],
].flatMap(([id, label]) => [
  [id, label, 'COVID-19', 'the COVID-19 pandemic'],
  [id, label, 'Ukraine War', 'the war in Ukraine'],
  [id, label, 'Generative AI Boom', 'the rise of generative AI and LLMs'],
]))('passes the selected indicator to research without submitting: %s / %s / %s', async (id, label, event, period) => {
  const fetch = stubMacroApi([point('2026-06', '2026-06-01', '2026-06-30', '2')]);
  render(<App macroAiEnabled />);
  const selector = await screen.findByLabelText('Indicator');
  fireEvent.change(selector, { target: { value: id } });
  fireEvent.click(await screen.findByRole('button', { name: event }));
  expect(screen.getByLabelText('Research question')).toHaveProperty('value', `How did ${label} evolve during ${period}?`);
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});

it('submits the event window and chart indicator after Ask LLM Model, independently of chart period', async () => {
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
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  await screen.findByText('There is insufficient evidence to answer this question.');
  expect(analysis).toHaveBeenCalledTimes(1);
  expect(JSON.parse(String(analysis.mock.calls[0][1].body))).toEqual({
    question: 'How did the Federal Reserve change interest rates?', start: '2020-02-01', end: '2020-12-31',
    indicator_id: 'eur-usd', event_id: 'covid',
  });
});

it('opts into Research v2 only after the catalog advertises it and renders cited partial results', async () => {
  const historyFetch = stubMacroApi([]);
  const analysis = vi.fn(async (_url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    const citation = 'series:eur-usd';
    return Response.json({
      ...body, research_v2: true, support: 'partial', publication_cutoff: body.end,
      status: 'answered', narrative_kind: 'deterministic', facts: [], calculations: [], interpretation: [],
      narrative: [{ text: 'The euro rose over the selected observations.', citations: [citation], category: 'official observation' }],
      limitations: ['The approved corpus has no applicable documentary context.'], graph_steps: ['finish'],
      evidence: [{
        id: citation, kind: 'series', title: 'ECB reference rate', url: 'https://data.ecb.europa.eu/',
        attribution: 'ECB statistics', period_start: '2020-02-03', period_end: '2020-12-31', published_on: null,
        series_key: 'EXR.D.USD.EUR.SP00.A', unit: 'USD/EUR', multiplier: 0, geography: 'bilateral',
        adjustment: 'not_applicable',
      }],
    });
  });
  vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
    if (url === '/api/macro/indicators') {
      return Response.json(catalog, { headers: { 'X-MarketPulse-Research-V2': 'true' } });
    }
    return url === '/api/macro/analysis' ? analysis(url, init) : historyFetch(url);
  });
  render(<App macroAiEnabled />);
  const selector = await screen.findByRole('checkbox', { name: 'Use experimental Research v2' });
  expect(selector).toHaveProperty('checked', false);
  expect(screen.getByText(/sends your original question to Google Gemini/)).toBeTruthy();
  expect(analysis).not.toHaveBeenCalled();
  fireEvent.click(selector);
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  await screen.findByText('Partial descriptive answer');
  expect(JSON.parse(String(analysis.mock.calls[0][1].body)).research_version).toBe('v2');
  const summary = screen.getByRole('region', { name: 'Data summary' });
  expect(screen.getByText(/Paragraph 1 · Official data/).closest('details')?.open).toBe(false);
  expect(within(summary).getByRole('link', { name: 'Source 1, ECB reference rate' }).getAttribute('href'))
    .toBe('https://data.ecb.europa.eu/');
  expect(within(summary).getByText(/no applicable documentary context/)).toBeTruthy();
  expect(screen.getByLabelText('Indicator')).toHaveProperty('value', 'eur-usd');
  expect(screen.getByRole('heading', { name: 'Official history' })).toBeTruthy();
});

it('keeps the chart, composer and custom question synchronized across selection and period changes', async () => {
  stubMacroApi([point('2026-06', '2026-06-01', '2026-06-30', '2')]);
  render(<App macroAiEnabled />);
  await screen.findByLabelText('Research question');
  fireEvent.click(screen.getByRole('button', { name: 'Eurozone M3' }));
  expect(screen.getByLabelText('Indicator')).toHaveProperty('value', 'm3-stock');
  await screen.findByLabelText('Eurozone M3: official observations by period');
  fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'What changed in M3?' } });
  fireEvent.click(screen.getByRole('button', { name: '7Y' }));
  expect(screen.getByLabelText('Research question')).toHaveProperty('value', 'What changed in M3?');
  fireEvent.change(screen.getByLabelText('Indicator'), { target: { value: 'hicp-yoy' } });
  expect(screen.getByRole('button', { name: 'Eurozone Inflation' }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByLabelText('Research question')).toHaveProperty('value', 'What changed in M3?');
  expect(screen.getByText(/Your custom question is preserved\. Review it for the selected indicator/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Use generated question' }));
  expect(screen.getByLabelText('Research question')).toHaveProperty('value',
    'How did Eurozone Inflation evolve during the COVID-19 pandemic?');
});

it('plots monthly observations without filling gaps and preserves source decimals and units', async () => {
  const fetch = stubMacroApi([
    point('2026-04', '2026-04-01', '2026-04-30', null),
    point('2026-06', '2026-06-01', '2026-06-30', '17612461.010'),
  ]);
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'AI-Powered Macroeconomic Intelligence' })).toBeTruthy();
  fireEvent.change(await screen.findByLabelText('Indicator'), { target: { value: 'm3-stock' } });
  const svg = await screen.findByLabelText('Eurozone M3: official observations by period');
  expect(svg.querySelectorAll('circle')).toHaveLength(1);
  expect(svg.querySelector('.macro-segment')).toBeNull();
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByText(/marked missing|Last captured|View observations/)).toBeNull();
  fireEvent.pointerEnter(svg.querySelector('circle')!);
  expect(screen.getByRole('tooltip').textContent).toContain('17612461.010 Millions of EUR');
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
  expect(svg.getAttribute('data-domain-end')).toBe('2026-06-25');
  expect(screen.queryByRole('table')).toBeNull();
});

it('shows five friendly names and colors and preserves the selected period across indicators', async () => {
  const fetch = stubMacroApi([
    point('2026-01', '2026-01-01', '2026-01-31', '1'),
    point('2026-06', '2026-06-01', '2026-06-30', '2'),
  ]);
  render(<App />);
  await screen.findByLabelText('EUR/USD rate: official observations by period');
  const periods = within(screen.getByRole('group', { name: 'History period' }));
  expect(periods.getAllByRole('button').map((button) => button.textContent)).toEqual(['1M', '3M', '6M', '1Y', '3Y', '7Y']);
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
    expect(svg.querySelectorAll('circle')).toHaveLength(1);
    expect(screen.queryByRole('table')).toBeNull();
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

it('limits the longest preset to seven years and preserves attribution without redundant metadata', async () => {
  const fetch = stubMacroApi([
    point('2010-01-04', '2010-01-04', '2010-01-04', '1.43'),
    point('2020-01-04', '2020-01-04', '2020-01-04', '1.23'),
    point('2026-06-30', '2026-06-30', '2026-06-30', '1.10'),
  ]);
  render(<App />);
  await screen.findByLabelText('EUR/USD rate: official observations by period');
  fireEvent.click(screen.getByRole('button', { name: '7Y' }));
  const svg = await screen.findByLabelText('EUR/USD rate: official observations by period');
  expect(svg.getAttribute('data-domain-start')).toBe('2020-01-04');
  expect(svg.getAttribute('data-domain-end')).toBe('2026-06-30');
  expect(svg.querySelectorAll('circle')).toHaveLength(2);
  expect(screen.getByRole('button', { name: '7Y' }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.queryByRole('button', { name: 'MAX' })).toBeNull();
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.queryByText('fixture')).toBeNull();
  expect(screen.getByRole('link', { name: 'Reuse terms' })).toBeTruthy();
  expect(screen.getByText('Powered by LLMs, RAG, LangChain and LangGraph.')).toBeTruthy();
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/macro/indicators'))).toBe(true);
});


it.each(['insufficient', 'partial'] as const)('renders %s public Research v2 after consent and verification', async (support) => {
  vi.stubEnv('MODE', 'public-demo');
  const historyFetch = stubMacroApi([]);
  const renderWidget = vi.fn<NonNullable<Window['turnstile']>['render']>(() => 'widget-id');
  const remove = vi.fn();
  window.turnstile = { render: renderWidget, remove };
  const partial = support === 'partial';
  const series = {
    id: 'series:eur-usd', kind: 'series', title: 'ECB reference rate', url: 'https://data.ecb.europa.eu/',
    attribution: 'ECB statistics', series_key: 'fixture', unit: 'USD/EUR', geography: 'euro_area',
    adjustment: 'none', multiplier: 0, period_start: '2020-02-03', period_end: '2020-12-31', published_on: null,
  };
  const claim = { text: 'The euro gained value over the observed period.', citations: [series.id],
    category: 'calculated from official observations' };
  try {
    const analysis = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      return Response.json({
        ...body, status: partial ? 'answered' : 'insufficient_evidence', research_v2: true, support,
        publication_cutoff: body.end, evidence: partial ? [series] : [],
        narrative: partial ? [claim] : [], facts: [], calculations: partial ? [claim] : [], interpretation: [],
        observed_changes: partial ? [{ indicator_id: 'eur-usd', direction: 'up', value: '5.00', unit: 'percent',
          first_observed_on: series.period_start, last_observed_on: series.period_end, citation: series.id }] : [],
        limitations: [partial ? 'No applicable documentary context.' : 'Required observations are missing.'],
        graph_steps: ['finish'],
      });
    });
    vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
      if (url === '/api/macro/indicators') {
        return Response.json(catalog, { headers: { 'X-MacroPulse-Turnstile-Sitekey': 'fixture-site-key-01' } });
      }
      return url === '/api/macro/analysis' ? analysis(url, init) : historyFetch(url);
    });
    render(<App macroDemoEnabled />);
    const submit = await screen.findByRole('button', { name: 'Ask LLM Model' });
    expect(screen.queryByText('Use experimental Research v2')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Official history' })).toBeTruthy();
    expect(submit).toHaveProperty('disabled', true);
    await waitFor(() => expect(renderWidget).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('checkbox', { name: /send my question to Google Gemini/ }));
    expect(submit).toHaveProperty('disabled', true);
    const options = renderWidget.mock.calls[0][1];
    expect(options.action).toBe('research');
    act(() => options.callback('synthetic-token'));
    expect(submit).toHaveProperty('disabled', false);
    fireEvent.click(submit);
    const result = await screen.findByRole('region', { name: 'Economic analysis' });
    const missingEvidence = 'There is insufficient evidence to answer this question.';
    if (partial) {
      expect(within(result).getByText('Partial descriptive answer')).toBeTruthy();
      expect(within(result).queryByText(missingEvidence)).toBeNull();
      expect(within(result).getByText(claim.text).textContent).toBe(`${claim.text} [1]`);
      expect(within(result).getByRole('link', { name: 'Source 1, ECB reference rate' }).getAttribute('href')).toBe(series.url);
      expect(screen.getByText('+5.00%').compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    } else {
      expect(within(result).getByText(missingEvidence)).toBeTruthy();
      expect(within(result).queryByText('Partial descriptive answer')).toBeNull();
      expect(within(result).queryByRole('link')).toBeNull();
    }
    expect(analysis).toHaveBeenCalledTimes(1);
    const init = analysis.mock.calls[0][1];
    expect(new Headers(init.headers).get('X-MacroPulse-Turnstile')).toBe('synthetic-token');
    expect(JSON.parse(String(init.body))).toMatchObject({
      research_version: 'v2', event_id: 'covid', indicator_id: 'eur-usd',
      question: 'How did EUR/USD rate evolve during the COVID-19 pandemic?',
    });
    expect(submit).toHaveProperty('disabled', true);
    expect(remove).toHaveBeenCalledWith('widget-id');
  } finally {
    delete window.turnstile;
  }
});
