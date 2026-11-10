// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MacroResearch } from './MacroResearch';

const period = { start: '2022-01-01', end: '2023-12-31' };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
