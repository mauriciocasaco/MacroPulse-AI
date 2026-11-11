// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { QuestionComposer, type QuestionComposerProps } from './QuestionComposer';
import { historicalEvents } from './question-events';
import type { MacroIndicator } from './macro-api';

const names = [
  ['eur-usd', 'EUR/USD rate'], ['m3-stock', 'Eurozone M3'], ['deposit-rate', 'ECB rate'],
  ['real-gdp-qoq', 'Eurozone GDP'], ['hicp-yoy', 'Eurozone Inflation'],
] as const;
const indicators: MacroIndicator[] = names.map(([id, title]) => ({
  id, title, frequency: 'monthly', unit: 'synthetic', multiplier: 0, provider: 'Synthetic fixture',
  series_key: id, geography: 'euro_area', adjustment: 'none',
  source_url: 'https://data.ecb.europa.eu/', reuse_url: 'https://www.ecb.europa.eu/', attribution: 'Synthetic fixture',
}));
const setup = (overrides: Partial<QuestionComposerProps> = {}) => ({
  indicators, indicatorId: 'eur-usd', enabled: true, pending: false,
  onIndicatorChange: vi.fn(), onSubmit: vi.fn(), ...overrides,
});

afterEach(cleanup);

it.each(names.flatMap(([id, name]) => historicalEvents.map((event) => ({ id, name, event }))))(
  'generates an editable question for $id / $event.label', ({ id, name, event }) => {
    const props = setup({ indicatorId: id });
    render(<QuestionComposer {...props} />);
    fireEvent.click(screen.getByRole('button', { name: event.label }));
    const field = screen.getByRole('textbox', { name: 'Research question' });
    expect(field).toHaveProperty('value', `${event.prefix} ${name}?`);
    expect(props.onSubmit).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: 'My own research question?' } });
    expect(field).toHaveProperty('value', 'My own research question?');
    expect(props.onSubmit).not.toHaveBeenCalled();
  },
);

it.each([
  ['COVID-19', 'covid', '2020-02-01', '2020-12-31'],
  ['Ukraine War', 'ukraine', '2022-02-01', '2024-02-29'],
  ['Generative AI Boom', 'generative-ai', '2023-09-01', '2026-09-30'],
])('submits the complete %s event window only on form submission', (label, eventId, start, end) => {
  const props = setup();
  render(<QuestionComposer {...props} />);
  fireEvent.click(screen.getByRole('button', { name: label }));
  expect(props.onSubmit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  expect(props.onSubmit).toHaveBeenCalledOnce();
  expect(props.onSubmit).toHaveBeenCalledWith({
    question: (screen.getByLabelText('Research question') as HTMLTextAreaElement).value,
    indicatorId: 'eur-usd', eventId, start, end,
  });
});

it('keeps the chart as the controlled selection and regenerates for external indicator changes', () => {
  const props = setup();
  const view = render(<QuestionComposer {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Eurozone Inflation' }));
  expect(props.onIndicatorChange).toHaveBeenCalledWith('hicp-yoy');
  expect(screen.getByRole('button', { name: 'EUR/USD rate' }).getAttribute('aria-pressed')).toBe('true');
  // A click requests a parent update. Until that update, submission stays on the chart's selected series.
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  expect(vi.mocked(props.onSubmit).mock.calls[0][0].indicatorId).toBe('eur-usd');
  view.rerender(<QuestionComposer {...props} indicatorId="hicp-yoy" />);
  expect(screen.getByRole('button', { name: 'Eurozone Inflation' }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByLabelText('Research question')).toHaveProperty('value',
    'How did the COVID-19 pandemic affect Eurozone Inflation?');
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  expect(vi.mocked(props.onSubmit).mock.calls[1][0].indicatorId).toBe('hicp-yoy');
});

it('preserves custom edits across context changes and offers explicit regeneration', () => {
  const props = setup();
  const view = render(<QuestionComposer {...props} />);
  fireEvent.change(screen.getByLabelText('Research question'), { target: { value: 'My custom question?' } });
  view.rerender(<QuestionComposer {...props} indicatorId="m3-stock" />);
  fireEvent.click(screen.getByRole('button', { name: 'Ukraine War' }));
  expect(screen.getByLabelText('Research question')).toHaveProperty('value', 'My custom question?');
  expect(screen.getByRole('status').textContent).toContain('Review it for the selected indicator');
  fireEvent.click(screen.getByRole('button', { name: 'Ask LLM Model' }));
  expect(props.onSubmit).toHaveBeenCalledWith({ question: 'My custom question?', indicatorId: 'm3-stock',
    eventId: 'ukraine', start: '2022-02-01', end: '2024-02-29' });
  fireEvent.click(screen.getByRole('button', { name: 'Use generated question' }));
  expect(screen.getByLabelText('Research question')).toHaveProperty('value',
    'How did the war in Ukraine affect Eurozone M3?');
  expect(screen.queryByRole('status')).toBeNull();
});

it.each([{ enabled: false }, { pending: true }])('is inert when disabled: %o', (state) => {
  const props = setup(state);
  render(<QuestionComposer {...props} />);
  expect(screen.getByLabelText('Research question')).toHaveProperty('disabled', true);
  expect(screen.getByRole('button', { name: 'Ask LLM Model' })).toHaveProperty('disabled', true);
  expect(screen.getByRole('group', { name: 'Indicator' })).toHaveProperty('disabled', true);
  expect(screen.getByRole('group', { name: 'Historical event' })).toHaveProperty('disabled', true);
  fireEvent.submit(screen.getByRole('form'));
  expect(props.onSubmit).not.toHaveBeenCalled();
});

it.each(['', '   ', 'abcd', 'x'.repeat(501), 'Question with\nnewline?'])('rejects an invalid custom question', (question) => {
  const props = setup();
  render(<QuestionComposer {...props} />);
  fireEvent.change(screen.getByLabelText('Research question'), { target: { value: question } });
  expect(screen.getByRole('button', { name: 'Ask LLM Model' })).toHaveProperty('disabled', true);
  fireEvent.submit(screen.getByRole('form'));
  expect(props.onSubmit).not.toHaveBeenCalled();
});

it('blocks an unavailable indicator instead of silently selecting another', () => {
  const props = setup({ indicatorId: 'unknown-series' });
  render(<QuestionComposer {...props} />);
  expect(screen.getByRole('button', { name: 'Ask LLM Model' })).toHaveProperty('disabled', true);
  fireEvent.submit(screen.getByRole('form'));
  expect(props.onSubmit).not.toHaveBeenCalled();
});

it('uses native keyboard-focusable controls, one submit button and instance-unique label IDs', () => {
  render(<><QuestionComposer {...setup()} /><QuestionComposer {...setup()} /></>);
  const forms = screen.getAllByRole('form');
  const fields = screen.getAllByLabelText('Research question');
  expect(fields[0].id).not.toBe(fields[1].id);
  for (const form of forms) {
    const local = within(form);
    expect(local.getAllByRole('button').filter((button) => button.getAttribute('type') === 'submit')).toHaveLength(1);
    const indicator = local.getByRole('button', { name: 'EUR/USD rate' });
    indicator.focus();
    expect(document.activeElement).toBe(indicator);
    expect(indicator.tabIndex).toBe(0);
    expect(local.getByRole('group', { name: 'Historical event' })).toBeTruthy();
    expect(local.getByLabelText('Research question').getAttribute('aria-describedby')).toBeTruthy();
  }
});
