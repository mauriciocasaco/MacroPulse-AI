// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type uPlot from 'uplot';
import type { AnalysisRequest, AnalysisResponse, Asset, DailyBar } from './api';
import { App } from './App';

const plot = vi.hoisted(() => ({
  create: vi.fn<(options: uPlot.Options, data: uPlot.AlignedData, element: HTMLElement) => void>(),
  setSize: vi.fn(), setScale: vi.fn(), setData: vi.fn(), setCursor: vi.fn(), destroy: vi.fn(),
}));
vi.mock('uplot', () => ({
  default: class {
    over = document.createElement('div');
    setSize = plot.setSize;
    setScale = plot.setScale;
    setData = plot.setData;
    setCursor = plot.setCursor;
    destroy = plot.destroy;
    valToPos(value: number) { return value; }
    constructor(options: uPlot.Options, data: uPlot.AlignedData, element: HTMLElement) { plot.create(options, data, element); }
  },
}));

const gold: Asset = {
  asset_source_id: 781, asset_id: 'gold_future', name: 'Gold futures', symbol: 'GC=F', source_id: 'sample',
  provider_name: 'Synthetic source', economic_unit: 'USD/oz', calendar_code: 'CMES',
  first_date: '2020-01-02', last_date: '2026-10-31', bar_count: 2,
};
const wti: Asset = { ...gold, asset_source_id: 234, asset_id: 'wti_future', name: 'WTI crude oil', symbol: 'CL=F', economic_unit: 'USD/bbl' };
const bar: DailyBar = {
  bar_date: '2020-04-20', close: '-37.630001068115234', adjusted_close: null,
  retrieved_at: '2026-11-09T14:20:00Z', availability_time: null, session_status: 'closed', quality_status: 'complete',
  source_convention: 'fixture', rolling_method: 'fixture roll',
};
const insufficient: AnalysisResponse = {
  asset: 'GC=F', asset_id: gold.asset_id, event: 'covid', start: '2020-02-01', end_exclusive: '2020-07-01', as_of_exclusive: null,
  provider: 'simulated', coverage: 'insufficient', citations: [], economic_mechanisms: [],
  economic_interpretation: 'Available evidence is insufficient.', limitations: ['Fixture limitation'],
  prices: {
    source_id: gold.source_id, asset_source_id: gold.asset_source_id, economic_unit: gold.economic_unit,
    price_basis: 'provider_unadjusted', source_convention: 'fixture', rolling_method: null, observations: 0,
    first: null, last: null, minimum_close: null, maximum_close: null, close_change: null, close_change_pct: null,
  },
};

const http = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
const json = (body: unknown) => Promise.resolve(Response.json(body));
let observeWidth: (width: number) => void;
let resizeFrame: FrameRequestCallback | undefined;

function flushResize() {
  const callback = resizeFrame;
  resizeFrame = undefined;
  act(() => callback?.(0));
}

beforeEach(() => {
  http.mockReset();
  for (const mock of Object.values(plot)) mock.mockClear();
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    .mockReturnValue({ width: 262.75 } as DOMRect);
  vi.stubGlobal('fetch', http);
  resizeFrame = undefined;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { resizeFrame = callback; return 1; });
  vi.stubGlobal('cancelAnimationFrame', () => { resizeFrame = undefined; });
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: (entries: { contentRect: { width: number } }[]) => void) {
      observeWidth = (width) => callback([{ contentRect: { width } }]);
    }
    observe() {}
    disconnect() {}
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function start(result = insufficient) {
  http.mockImplementation((url, init) => {
    if (url === '/api/assets') return json([gold, wti]);
    if (url === '/api/analysis') {
      const { event, start, end_exclusive } = JSON.parse(String(init.body)) as AnalysisRequest;
      return json({ ...result, event, start, end_exclusive });
    }
    return json({ asset: url.includes('/234/') ? wti : gold, bars: [] });
  });
  render(<App />);
  return screen.findByRole('combobox', { name: 'Asset' });
}

describe('market workspace', () => {
  it('shows the shell offline and requests fresh catalog data after reconnecting', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    http.mockImplementation((url) => url === '/api/assets' ? json([gold]) : json({ asset: gold, bars: [] }));
    render(<App />);

    expect(screen.getByText('You are offline. Live financial data is unavailable.')).toBeTruthy();
    expect(http).not.toHaveBeenCalled();

    fireEvent(window, new Event('online'));
    await screen.findByRole('combobox', { name: 'Asset' });
    expect(http).toHaveBeenCalledWith('/api/assets', expect.objectContaining({ cache: 'no-store' }));

    fireEvent(window, new Event('offline'));
    expect(screen.getByText('You are offline. Live financial data is unavailable.')).toBeTruthy();
    expect(screen.queryByRole('combobox', { name: 'Asset' })).toBeNull();
  });

  it('loads the real mappings and updates the window, coverage, and preset with the selected asset', async () => {
    const select = await start();
    expect(screen.getByRole('heading', { name: 'Gold' })).toBeDefined();
    expect(await screen.findByText('No prices are available for this period.')).toBeDefined();
    fireEvent.change(select, { target: { value: '234' } });
    fireEvent.click(screen.getByRole('button', { name: '1M' }));
    await waitFor(() => expect(http.mock.calls.map(([url]) => url)).toContain('/api/assets/234/history?start=2026-09-30&end_exclusive=2026-11-01'));
    expect(screen.getByRole('heading', { name: 'Crude Oil' })).toBeDefined();
    expect(screen.getByText(/USD\/bbl/)).toBeDefined();
    expect(screen.getByPlaceholderText(/affect Crude Oil\?/)).toBeDefined();
  });

  it('handles catalog HTTP failures and retries into the empty catalog state', async () => {
    http.mockResolvedValueOnce(new Response('', { status: 503 })).mockImplementationOnce(() => json([]));
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('HTTP 503');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No assets available.')).toBeDefined();
  });

  it('rejects a successful catalog response with an incompatible shape', async () => {
    http.mockImplementationOnce(() => json([null]));
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('unexpected response');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeDefined();
  });

  it('rejects a successful history response with malformed bars', async () => {
    http.mockImplementation((url) => url === '/api/assets' ? json([gold]) : json({ asset: gold, bars: [{ ...bar, bar_date: 'yesterday' }] }));
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('unexpected response');
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('plots the original negative Close, formats its reading, and resizes the same chart instance', async () => {
    http.mockImplementation((url) => url === '/api/assets' ? json([wti])
      : json({ asset: wti, bars: [{ ...bar, quality_status: 'partial' }] }));
    render(<App />);
    expect(await screen.findByText('-37.6')).toBeDefined();
    expect(screen.getByText('Partial data')).toBeDefined();
    expect(screen.getByRole('img', { name: 'Daily Close for Crude Oil' })).toBeDefined();
    expect(plot.create.mock.calls[0][0]).toMatchObject({ width: 262, height: 264 });
    expect(plot.create.mock.calls[0][1]).toEqual([[0], [-37.630001068115234]]);
    act(() => observeWidth(945));
    expect(plot.setSize).not.toHaveBeenCalled();
    flushResize();
    expect(plot.setSize).toHaveBeenLastCalledWith({ width: 945, height: 342 });
    act(() => observeWidth(945));
    expect(resizeFrame).toBeUndefined();
    act(() => observeWidth(332));
    flushResize();
    expect(plot.setSize).toHaveBeenLastCalledWith({ width: 332, height: 264 });
    expect(plot.create).toHaveBeenCalledTimes(1);
    act(() => observeWidth(990));
    fireEvent.change(screen.getByRole('combobox', { name: 'Price series' }), { target: { value: 'adjusted_close' } });
    expect(screen.getByText('No Adj Close prices are available for this period.')).toBeDefined();
    expect(screen.queryByText('-37.6')).toBeNull();
    expect(plot.destroy).toHaveBeenCalledTimes(1);
    expect(resizeFrame).toBeUndefined();
  });

  it('inspects exact observations and distinguishes missing sessions from null, malformed, and omitted prices', async () => {
    http.mockImplementation((url) => url === '/api/assets' ? json([wti]) : json({ asset: wti, bars: [
      { ...bar, bar_date: '2020-04-16', close: null },
      { ...bar, bar_date: '2020-04-17', close: '18.270000' },
      bar,
      { ...bar, bar_date: '2020-04-21', close: null },
      { ...bar, bar_date: '2020-04-22', close: '0x0', adjusted_close: '11.123' },
      { ...bar, bar_date: '2020-04-23', close: undefined },
      { ...bar, bar_date: '2020-04-24', close: '12.3456' },
      { ...bar, bar_date: '2020-04-25', close: [] },
    ] }));
    render(<App />);
    const input = await screen.findByLabelText('Inspect date');
    expect(input.getAttribute('min')).toBe('2020-04-16');
    expect(input.getAttribute('max')).toBe('2020-04-25');
    expect(screen.getByText('12.3')).toBeDefined();
    fireEvent.change(input, { target: { value: '2020-04-20' } });
    expect(screen.getByText('-37.6')).toBeDefined();
    expect(screen.getByText('Apr 20, 2020')).toBeDefined();
    expect(plot.setScale).toHaveBeenCalledWith('x', { min: 0, max: 2 });
    flushResize();
    expect(plot.setCursor).toHaveBeenLastCalledWith({ left: 1, top: -37.630001068115234 });
    fireEvent.change(input, { target: { value: '2020-04-19' } });
    expect(screen.getByText('No observation is available for this date.')).toBeDefined();
    expect(screen.queryByText('-37.6')).toBeNull();
    for (const date of ['2020-04-16', '2020-04-21', '2020-04-22', '2020-04-23', '2020-04-25']) {
      fireEvent.change(input, { target: { value: date } });
      expect(screen.getByText('No Close price is available for this date.')).toBeDefined();
    }
    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }));
    expect(screen.getByText('12.3')).toBeDefined();
    expect(plot.setData).toHaveBeenCalledWith([[0, 1, 2], [18.27, -37.630001068115234, 12.3456]]);
    fireEvent.change(screen.getByRole('combobox', { name: 'Price series' }), { target: { value: 'adjusted_close' } });
    expect(screen.getByText('11.1')).toBeDefined();
    expect(screen.getByText('Apr 22, 2020')).toBeDefined();
  });

  it.each([
    ['GC=F', 'Gold', '#D4AF37'], ['CL=F', 'Crude Oil', '#F5F5F5'], ['BTC-USD', 'Bitcoin', '#F7931A'],
    ['VWO', 'Emerging ETF', '#A78BFA'], ['^GSPC', 'S&P 500', '#7DD3FC'], ['VT', 'Global ETF', '#34D399'],
  ])('shows the friendly name and actual series color for %s across periods', async (symbol, name, color) => {
    const asset = { ...wti, symbol };
    http.mockImplementation((url) => url === '/api/assets' ? json([gold, asset])
      : json({ asset: url.includes('/234/') ? asset : gold, bars: [bar] }));
    render(<App />);
    const select = await screen.findByRole('combobox', { name: 'Asset' });
    fireEvent.change(select, { target: { value: String(asset.asset_source_id) } });
    expect(screen.getByRole('heading', { name })).toBeDefined();
    expect(screen.getByPlaceholderText(`How did the COVID-19 pandemic affect ${name}?`)).toBeDefined();
    for (const period of ['1M', '3M', '6M', '1Y', '3Y', 'Max']) {
      fireEvent.click(screen.getByRole('button', { name: period }));
      expect(await screen.findByRole('img', { name: `Daily Close for ${name}` })).toBeDefined();
      expect(screen.getByRole('button', { name: period }).getAttribute('aria-pressed')).toBe('true');
      expect(plot.create.mock.lastCall?.[0].series[1]).toMatchObject({ label: name, stroke: color });
      expect(document.body.textContent).not.toContain(symbol);
    }
  });
});

describe('financial analysis', () => {
  it.each([
    ['COVID-19 pandemic', 'covid', '2020-02-01', '2020-07-01', 'the COVID-19 pandemic'],
    ["Russia's invasion of Ukraine", 'ukraine', '2022-02-01', '2022-07-01', 'Russia’s invasion of Ukraine'],
    ['Rise of generative AI', 'genai', '2022-11-01', '2025-01-01', 'the rise of generative AI'],
  ])('submits the preset and independent analysis window for %s', async (title, event, startDate, endDate, subject) => {
    await start();
    fireEvent.click(screen.getByRole('button', { name: '3Y' }));
    fireEvent.click(screen.getByRole('radio', { name: new RegExp(title) }));
    fireEvent.click(screen.getByRole('button', { name: 'Run analysis' }));
    const analysis = http.mock.calls.find(([url]) => url === '/api/analysis');
    expect(JSON.parse(String(analysis?.[1].body))).toEqual({
      asset: gold.asset_id, event, start: startDate, end_exclusive: endDate, question: `How did ${subject} affect GC=F?`,
    });
    const result = await screen.findByRole('article', { name: 'Analysis result' });
    expect(within(result).getByText('Insufficient evidence')).toBeDefined();
    expect(within(result).getByText(insufficient.economic_interpretation)).toBeDefined();
    expect(within(result).getByText('No relevant documents found.')).toBeDefined();
    expect(within(result).getByText('N/A')).toBeDefined();
    expect(within(result).getAllByRole('heading').map((heading) => heading.textContent))
      .toEqual(['Price Change', 'AI Analysis', 'RAG Documents']);
  });

  it.each([
    ['8.249', '+8.2%', 'positive'],
    ['-12.44', '-12.4%', 'negative'],
    ['0', '0.0%', 'neutral'],
    [null, 'N/A', 'neutral'],
  ])('renders backend change %s and actual observation dates', async (percentage, label, tone) => {
    const first = { date: '2020-04-17', close: '18.270000', retrieved_at: bar.retrieved_at };
    const last = { date: '2020-04-20', close: percentage === null ? '-37.630001068115234' : '17.00000', retrieved_at: bar.retrieved_at };
    await start({ ...insufficient, prices: {
      ...insufficient.prices, observations: 2, first, last, close_change: '-1.270000',
      close_change_pct: percentage, source_convention: 'verified:fixture', rolling_method: 'unknown',
    } });
    fireEvent.click(screen.getByRole('button', { name: 'Run analysis' }));
    const summary = await screen.findByRole('region', { name: 'Price Change' });
    expect(within(summary).getByText(label).classList.contains(tone)).toBe(true);
    expect(Array.from(summary.querySelectorAll('time'), (time) => time.textContent))
      .toEqual(['Apr 17, 2020', 'Apr 20, 2020']);
    expect(summary.textContent).not.toMatch(/verified:fixture|unknown|-1\.270000/);
  });

  it('sends a custom question, opens safe original links, and displays untrusted text inertly', async () => {
    const citation = {
      evidence_id: 'news-record:1', source_id: 'sample', source_name: 'Fixture source', title: '<img src=x>', publisher: 'Fixture publisher',
      original_url: 'https://example.test/original?edition=1', published_at: null, published_on: '2020-03-20',
      provider_observed_at: null, retrieved_at: '2026-11-09T14:20:00Z', relevance: 'direct',
    } as const;
    await start({ ...insufficient, coverage: 'partial', economic_interpretation: 'Reviewed evidence relates to GC=F.',
      economic_mechanisms: [{
        citation_id: citation.evidence_id,
        summary: 'CL=F, ^GSPC and VT had different exposure. In 2022, front-month WTI rose above $100. Another series reached $1,234.567, then fell.',
      }], citations: [citation,
      { ...citation, evidence_id: 'news-record:2', title: 'Invalid protocol', original_url: 'javascript:alert(1)', relevance: 'contextual' },
      { ...citation, evidence_id: 'news-record:3', title: 'URL with credentials', original_url: 'https://user:pass@example.test/private' },
      { ...citation, evidence_id: 'news-record:4', title: 'Original article covering GC=F and $100 oil' },
    ] });
    fireEvent.change(screen.getByLabelText(/Custom question/), { target: { value: 'Did demand change?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run analysis' }));
    const link = await screen.findByRole('link', { name: /<img src=x>/ });
    expect(link.getAttribute('href')).toBe(citation.original_url);
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.getByText(/Fixture publisher.*Contextual/)).toBeDefined();
    expect(screen.getAllByText('Mar 20, 2020')).toHaveLength(4);
    expect(screen.getByText('Reviewed evidence relates to Gold.')).toBeDefined();
    expect(screen.getByText('Crude Oil, S&P 500 and Global ETF had different exposure. In 2022, front-month WTI rose above $100.0. Another series reached $1,234.6, then fell.')).toBeDefined();
    expect(screen.getByRole('link', { name: 'Original article covering GC=F and $100 oil' })).toBeDefined();
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByRole('link', { name: /Invalid protocol|URL with credentials/ })).toBeNull();
    const analysis = http.mock.calls.find(([url]) => url === '/api/analysis');
    expect(JSON.parse(String(analysis?.[1].body)).question).toBe('Did demand change?');
  });

  it('rejects a successful analysis response with an incompatible nested payload', async () => {
    await start();
    http.mockImplementationOnce(() => json({ ...insufficient, citations: [null] }));
    fireEvent.click(screen.getByRole('button', { name: 'Run analysis' }));
    expect((await screen.findByRole('alert')).textContent).toContain('unexpected response');
    expect(screen.queryByRole('article', { name: 'Analysis result' })).toBeNull();
  });

  it('ignores a delayed response after changing assets', async () => {
    const select = await start();
    let resolve!: (response: Response) => void;
    http.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    fireEvent.click(screen.getByRole('button', { name: 'Run analysis' }));
    const attempt = http.mock.lastCall?.[1].signal;
    fireEvent.change(select, { target: { value: '234' } });
    await act(async () => resolve(Response.json(insufficient)));
    expect(attempt?.aborted).toBe(true);
    expect(screen.queryByRole('article', { name: 'Analysis result' })).toBeNull();
    expect(screen.getByPlaceholderText(/affect Crude Oil\?/)).toBeDefined();
  });
});
