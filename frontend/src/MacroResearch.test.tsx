// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MacroResearch } from './MacroResearch';
import type { MacroIndicator } from './macro-api';

const catalog: MacroIndicator[] = ['eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy'].map((id) => ({
  id, title: id, frequency: 'daily', unit: 'USD/EUR', multiplier: 0, provider: 'ecb', series_key: 'fixture',
  geography: 'fixture', adjustment: 'fixture', source_url: 'https://data.ecb.europa.eu/',
  reuse_url: 'https://www.ecb.europa.eu/', attribution: 'ECB statistics',
}));
const props = { enabled: true, indicators: catalog, indicatorId: 'eur-usd', onIndicatorChange: vi.fn() };
const covid = { start: '2020-02-01', end: '2020-12-31' };
const events = [
  ['COVID-19', 'covid', '2020-02-01', '2020-12-31'],
  ['Ukraine War', 'ukraine', '2022-02-01', '2024-02-29'],
  ['Generative AI Boom', 'generative-ai', '2023-09-01', '2026-09-30'],
];
function response(question: string, window = covid) {
  return { status: 'insufficient_evidence', question, ...window, facts: [], calculations: [], interpretation: [],
    narrative: [], model_id: null, limitations: ['Synthetic corpus has no matching context.'], evidence: [], graph_steps: ['abstain'] };
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it.each(events)('submits %s dates and explicit chart context only after Ask LLM Model', async (label, eventId, start, end) => {
  const fetch = vi.fn(async (_url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    return Response.json(response(body.question, { start: body.start, end: body.end }));
  });
  vi.stubGlobal('fetch', fetch);
  render(<MacroResearch {...props} />);
  fireEvent.click(screen.getByRole('button', { name: label }));
  fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'How did EUR/USD change?' } });
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  await screen.findByText('There is insufficient evidence to answer this question.');
  expect(JSON.parse(String(fetch.mock.calls[0][1].body))).toEqual({
    question: 'How did EUR/USD change?', start, end, indicator_id: 'eur-usd', event_id: eventId,
  });
  expect(fetch.mock.calls[0][0]).toBe('/api/macro/analysis');
  expect(fetch.mock.calls[0][1].cache).toBe('no-store');
  expect(screen.getByRole('heading', { name: 'Economic analysis' })).toBeTruthy();
  expect(screen.getByText('No institutional documents were retrieved for this answer.')).toBeTruthy();
});

it('renders a cited KPI and narrative above deduplicated institutional sources', async () => {
  const question = 'How did EUR/USD rate evolve during the COVID-19 pandemic?';
  const series = { id: 'series:eur-usd', kind: 'series', title: 'ECB reference rate',
    url: 'https://data.ecb.europa.eu/', attribution: 'ECB statistics',
    period_start: '2020-02-03', period_end: '2020-12-31', published_on: null };
  const sourceDocument = (id: string, title: string, score?: number) => ({
    id, title, kind: 'document', url: `https://www.ecb.europa.eu/${id.replace(/:[^:]+$/, '')}`, attribution: 'European Central Bank',
    published_on: '2020-03-15', period_start: null, period_end: null,
    ...(score == null ? {} : { similarity_score: score, similarity_metric: 'cosine' }),
  });
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({
    ...response(question), status: 'answered', narrative_kind: 'gemini', model_id: 'gemini-test-fixture',
    observed_changes: [{ indicator_id: 'eur-usd', direction: 'up', value: '10.89', unit: 'percent',
      first_observed_on: '2020-02-03', last_observed_on: '2020-12-31', citation: series.id }],
    narrative: [{ text: 'The observed change is descriptive and does not establish causality.',
      citations: ['series:eur-usd', 'high:1'] }],
    facts: [{ text: 'Legacy fact should not render twice.', citations: ['series:eur-usd'] }],
    evidence: [series, sourceDocument('high:1', 'High relevance source', 0.81256),
      sourceDocument('low:1', 'Lower relevance source', 0.23444),
      sourceDocument('low:2', 'Duplicate chunk', 0.12345)],
  })));
  render(<MacroResearch {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  await screen.findByText('The observed change is descriptive and does not establish causality.');
  expect(screen.queryByText('Legacy fact should not render twice.')).toBeNull();
  for (const heading of ['Facts', 'Calculations', 'Interpretation', 'Sources']) {
    expect(screen.queryByRole('heading', { name: heading })).toBeNull();
  }
  const kpi = screen.getByText('+10.89%');
  expect(kpi.classList.contains('up')).toBe(true);
  expect(screen.getByText('3 Feb 2020').getAttribute('datetime')).toBe('2020-02-03');
  const analysis = screen.getByRole('region', { name: 'Economic analysis' });
  expect(kpi.compareDocumentPosition(analysis) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(analysis).getAllByRole('link').map((link) => link.getAttribute('href')))
    .toEqual([series.url, 'https://www.ecb.europa.eu/high']);
  expect(within(kpi.parentElement!).getByRole('link', { name: 'Source 1, ECB reference rate' }).getAttribute('href'))
    .toBe(series.url);
  const sources = screen.getByRole('heading', { name: 'Institutional sources' }).parentElement!;
  expect(analysis.compareDocumentPosition(sources) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(sources).getAllByRole('link').map((link) => link.textContent))
    .toEqual(['High relevance source', 'Lower relevance source', 'ECB reference rate']);
  expect(sources.querySelector('details')?.open).toBe(false);
  expect(sources.textContent).toContain('Cosine similarity 0.813');
  expect(sources.textContent).toContain('Cosine similarity 0.234');
  expect(sources.textContent).not.toContain('Duplicate chunk');
});

it('cancels old requests on chart indicator change and prevents stale rendering', async () => {
  let complete!: (value: Response) => void;
  const fetch = vi.fn((_url: string, _init: RequestInit) => new Promise<Response>((resolve) => { complete = resolve; }));
  vi.stubGlobal('fetch', fetch);
  const view = render(<MacroResearch {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  expect(screen.getByText('Checking official evidence…')).toBeTruthy();
  view.rerender(<MacroResearch {...props} indicatorId="hicp-yoy" />);
  expect(fetch.mock.calls[0][1].signal?.aborted).toBe(true);
  complete(Response.json(response('How did EUR/USD rate evolve during the COVID-19 pandemic?')));
  await Promise.resolve();
  expect(screen.queryByText('Checking official evidence…')).toBeNull();
  expect(screen.queryByRole('heading', { name: 'Economic analysis' })).toBeNull();
  expect(screen.getByLabelText('Research question')).toHaveProperty('value',
    'How did Eurozone Inflation evolve during the COVID-19 pandemic?');
  view.rerender(<MacroResearch {...props} />);
  expect(screen.queryByRole('heading', { name: 'Economic analysis' })).toBeNull();
  expect(screen.queryByText('Checking official evidence…')).toBeNull();
});

it('keeps public/disabled research inert and has no redundant heading subtitles', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  render(<MacroResearch {...props} enabled={false} />);
  expect(screen.queryByLabelText('Research question')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Ask LLM Model' })).toBeNull();
  expect(screen.getByText('AI research is available only in an enabled local workspace.')).toBeTruthy();
  expect(screen.queryByText(/Powered by LangGraph/)).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it('renders network errors and clears the loading state', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 503 })));
  render(<MacroResearch {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  await screen.findByRole('alert');
  expect(screen.getByText('Local macro research is currently unavailable.')).toBeTruthy();
  expect(screen.queryByText('Checking official evidence…')).toBeNull();
  expect(screen.getByRole('button', { name: 'Ask LLM Model' })).toHaveProperty('disabled', false);
});


it('labels a deterministic fallback without showing raw calculations', async () => {
  const series = { id: 'series:eur-usd', kind: 'series', title: 'ECB reference rate',
    url: 'https://data.ecb.europa.eu/', attribution: 'ECB statistics',
    period_start: '2020-02-03', period_end: '2020-12-31', published_on: null };
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({
    ...response('How did EUR/USD rate evolve during the COVID-19 pandemic?'),
    status: 'answered', narrative_kind: 'deterministic',
    narrative: [{ text: 'The euro rose over the selected period.', citations: [series.id] }],
    calculations: [{ text: 'Raw calculation.', citations: [series.id] }], evidence: [series],
  })));
  render(<MacroResearch {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  const summary = await screen.findByRole('region', { name: 'Data summary' });
  expect(screen.getByText(/Summary generated from verified evidence without Gemini/).closest('details')?.open).toBe(false);
  expect(within(summary).getByText('The euro rose over the selected period.')).toBeTruthy();
  expect(screen.queryByText('Raw calculation.')).toBeNull();
});
