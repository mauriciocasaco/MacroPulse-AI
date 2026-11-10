// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MacroResearch } from './MacroResearch';

const period = { start: '2022-01-01', end: '2023-12-31' };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const indicators = [
  ['eur-usd', 'EUR/USD rate'], ['m3-stock', 'Eurozone M3'], ['deposit-rate', 'ECB rate'],
  ['real-gdp-qoq', 'Eurozone GDP'], ['hicp-yoy', 'Eurozone Inflation'],
];
const events = [
  ['COVID-19', 'How did the COVID-19 pandemic affect'],
  ['Ukraine War', 'How did the war in Ukraine affect'],
  ['Generative AI', 'How did the rise of generative AI and LLMs affect'],
];

it.each(indicators.flatMap(([id, name]) => events.map(([event, prefix]) => [id, name, event, prefix])))(
  'prepares an editable historical question for %s / %s / %s without sending', (id, name, event, prefix) => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<MacroResearch enabled indicatorId={id} {...period} />);
    expect(screen.getByRole('heading', { name: 'RAG-Powered Macro Research' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: event }));
    const field = screen.getByLabelText('Research question');
    expect(field).toHaveProperty('value', `${prefix} ${name}?`);
    fireEvent.change(field, { target: { value: 'My own macroeconomic question?' } });
    expect(field).toHaveProperty('value', 'My own macroeconomic question?');
    expect(fetch).not.toHaveBeenCalled();
  },
);

it('uses the current indicator after a selection change', () => {
  vi.stubGlobal('fetch', vi.fn());
  const view = render(<MacroResearch enabled indicatorId="eur-usd" {...period} />);
  fireEvent.click(screen.getByRole('button', { name: 'COVID-19' }));
  view.rerender(<MacroResearch enabled indicatorId="hicp-yoy" {...period} />);
  fireEvent.click(screen.getByRole('button', { name: 'Ukraine War' }));
  expect(screen.getByLabelText('Research question')).toHaveProperty('value',
    'How did the war in Ukraine affect Eurozone Inflation?');
});

it('sends a prepared question through the existing endpoint only after Ask', async () => {
  const question = 'How did the war in Ukraine affect Eurozone Inflation?';
  const fetch = vi.fn(async (_url: string, _init: RequestInit) => Response.json({
    status: 'insufficient_evidence', question, ...period, facts: [], calculations: [], interpretation: [],
    limitations: ['Synthetic corpus has no matching context.'], evidence: [], graph_steps: ['abstain'],
  }));
  vi.stubGlobal('fetch', fetch);
  render(<MacroResearch enabled indicatorId="hicp-yoy" {...period} />);
  fireEvent.click(screen.getByRole('button', { name: 'Ukraine War' }));
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('There is insufficient evidence for an answer.');
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe('/api/macro/analysis');
  expect(JSON.parse(String(fetch.mock.calls[0][1].body))).toEqual({ question, ...period });
});

it('keeps disabled research inert', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  render(<MacroResearch enabled={false} {...period} />);
  expect(screen.queryByLabelText('Research question')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Ask' })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it('submits only the selected period and separates facts, calculations, interpretation and sources', async () => {
  const question = 'How did the deposit rate change?';
  const fetch = vi.fn(async (_url: string, _init: RequestInit) => Response.json({
    status: 'answered', question, ...period,
    facts: [{ text: 'The official series records effective dates.', citations: ['series:fixture'] }],
    calculations: [{ text: 'The change was 4 percentage points.', citations: ['series:fixture'] }],
    interpretation: [{ text: 'This describes a tightening cycle.', citations: ['series:fixture'] }],
    limitations: ['The comparison is descriptive.'],
    evidence: [{ id: 'series:fixture', kind: 'series', title: 'ECB deposit facility',
      url: 'https://data.ecb.europa.eu/', attribution: 'ECB statistics',
      period_start: '2022-07-27', period_end: '2023-09-20', published_on: null }],
    graph_steps: ['interpret', 'generate'],
  }));
  vi.stubGlobal('fetch', fetch);
  render(<MacroResearch enabled {...period} />);
  fireEvent.change(screen.getByLabelText('Research question'), { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  expect(screen.getByText('Checking official evidence…')).toBeTruthy();
  await screen.findByRole('heading', { name: 'Interpretation' });
  for (const name of ['Facts', 'Calculations', 'Limitations', 'Sources']) {
    expect(screen.getByRole('heading', { name })).toBeTruthy();
  }
  expect(screen.getByRole('link', { name: 'ECB deposit facility' }).getAttribute('href')).toBe('https://data.ecb.europa.eu/');
  expect(screen.getAllByRole('link', { name: '[1]' })).toHaveLength(3);
  const [url, init] = fetch.mock.calls[0];
  expect(url).toBe('/api/macro/analysis');
  expect(init.method).toBe('POST');
  expect(init.cache).toBe('no-store');
  expect(JSON.parse(String(init.body))).toEqual({ question, ...period });
});

it.each([
  [Response.json({ status: 'insufficient_evidence', question: 'How did rates change?', ...period,
    facts: [], calculations: [], interpretation: [], limitations: ['No matching evidence.'], evidence: [],
    graph_steps: ['interpret', 'select_indicators', 'abstain'] }), 'There is insufficient evidence for an answer.'],
  [new Response(null, { status: 503 }), 'Local macro research is currently unavailable.'],
])('renders abstentions and errors and clears the loading state', async (response, message) => {
  vi.stubGlobal('fetch', vi.fn(async () => response));
  render(<MacroResearch enabled {...period} />);
  fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'How did rates change?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText(message);
  expect(screen.queryByText('Checking official evidence…')).toBeNull();
  expect(screen.getByRole('button', { name: 'Ask' })).toHaveProperty('disabled', false);
});
