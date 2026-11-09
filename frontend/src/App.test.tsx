// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AnalysisRequest, AnalysisResponse, Asset, DailyBar } from './api';
import { App } from './App';

const plot = vi.hoisted(() => ({ create: vi.fn(), setSize: vi.fn(), destroy: vi.fn() }));
vi.mock('uplot', () => ({
  default: class {
    setSize = plot.setSize;
    destroy = plot.destroy;
    setScale() {}
    constructor(...args: unknown[]) { plot.create(...args); }
  },
}));

const gold: Asset = {
  asset_source_id: 781, asset_id: 'gold_future', name: 'Oro futuro', symbol: 'GC=F', source_id: 'sample',
  provider_name: 'Fuente sintética', economic_unit: 'USD/oz', calendar_code: 'CMES',
  first_date: '2020-01-02', last_date: '2026-10-31', bar_count: 2,
};
const wti: Asset = { ...gold, asset_source_id: 234, asset_id: 'wti_future', name: 'Petróleo WTI futuro', symbol: 'CL=F', economic_unit: 'USD/bbl' };
const bar: DailyBar = {
  bar_date: '2020-04-20', close: '-37.630000', adjusted_close: null,
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
  plot.create.mockClear();
  plot.setSize.mockClear();
  plot.destroy.mockClear();
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
  return screen.findByRole('combobox', { name: 'Activo financiero' });
}

describe('market workspace', () => {
  it('loads the real mappings and updates the window, coverage, and preset with the selected asset', async () => {
    const select = await start();
    expect(screen.getByRole('heading', { name: 'Oro futuro' })).toBeDefined();
    expect(await screen.findByText('No hay observaciones para este período.')).toBeDefined();
    fireEvent.change(select, { target: { value: '234' } });
    fireEvent.click(screen.getByRole('button', { name: '1 mes' }));
    await waitFor(() => expect(http.mock.calls.map(([url]) => url)).toContain('/api/assets/234/history?start=2026-09-30&end_exclusive=2026-11-01'));
    expect(screen.getByRole('heading', { name: 'Petróleo WTI futuro' })).toBeDefined();
    expect(screen.getByText(/USD\/bbl · CMES/)).toBeDefined();
    expect(screen.getByPlaceholderText(/a CL=F\?/)).toBeDefined();
  });

  it('handles catalog HTTP failures and retries into the empty catalog state', async () => {
    http.mockResolvedValueOnce(new Response('', { status: 503 })).mockImplementationOnce(() => json([]));
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('HTTP 503');
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('FastAPI no devolvió activos disponibles.')).toBeDefined();
  });

  it('rejects a successful catalog response with an incompatible shape', async () => {
    http.mockImplementationOnce(() => json([null]));
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('no coincide con el contrato');
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeDefined();
  });

  it('rejects a successful history response with malformed bars', async () => {
    http.mockImplementation((url) => url === '/api/assets' ? json([gold]) : json({ asset: gold, bars: [{ ...bar, bar_date: 'yesterday' }] }));
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('no coincide con el contrato');
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('displays exact negative Close and resizes the chart for a narrow container', async () => {
    http.mockImplementation((url) => url === '/api/assets' ? json([wti]) : json({ asset: wti, bars: [bar] }));
    render(<App />);
    expect(await screen.findByText('-37.630000')).toBeDefined();
    expect(screen.getByRole('img', { name: 'Serie de Close diario de CL=F' })).toBeDefined();
    act(() => observeWidth(945));
    expect(plot.setSize).not.toHaveBeenCalled();
    flushResize();
    expect(plot.setSize).toHaveBeenLastCalledWith({ width: 945, height: 342 });
    act(() => observeWidth(945));
    expect(resizeFrame).toBeUndefined();
    act(() => observeWidth(332));
    flushResize();
    expect(plot.setSize).toHaveBeenLastCalledWith({ width: 332, height: 264 });
    act(() => observeWidth(990));
    fireEvent.change(screen.getByRole('combobox', { name: 'Serie' }), { target: { value: 'adjusted_close' } });
    expect(screen.getByText('No hay valores de Adj Close en este período.')).toBeDefined();
    expect(screen.queryByText('-37.630000')).toBeNull();
    expect(plot.destroy).toHaveBeenCalledTimes(1);
    expect(resizeFrame).toBeUndefined();
  });
});

describe('financial analysis', () => {
  it.each([
    ['Pandemia de COVID-19', 'covid', '2020-02-01', '2020-07-01', 'la pandemia del COVID-19'],
    ['Invasión rusa de Ucrania', 'ukraine', '2022-02-01', '2022-07-01', 'la invasión rusa de Ucrania'],
    ['Auge de la IA generativa', 'genai', '2022-11-01', '2025-01-01', 'el auge de la IA generativa'],
  ])('submits the preset and independent analysis window for %s', async (title, event, startDate, endDate, subject) => {
    await start();
    fireEvent.click(screen.getByRole('button', { name: '3 años' }));
    fireEvent.click(screen.getByRole('radio', { name: new RegExp(title) }));
    fireEvent.click(screen.getByRole('button', { name: 'Analizar acontecimiento' }));
    const analysis = http.mock.calls.find(([url]) => url === '/api/analysis');
    expect(JSON.parse(String(analysis?.[1].body))).toEqual({
      asset: gold.asset_id, event, start: startDate, end_exclusive: endDate, question: `¿Cómo afectó ${subject} a GC=F?`,
    });
    const result = await screen.findByRole('article', { name: 'Resultado de la consulta' });
    expect(within(result).getByText('Evidencia insuficiente')).toBeDefined();
    expect(within(result).getByText('Proveedor simulado')).toBeDefined();
    expect(within(result).getByText(insufficient.economic_interpretation)).toBeDefined();
    expect(within(result).getByText('La respuesta no incluye citas documentales.')).toBeDefined();
  });

  it('sends a custom question, opens safe original links, and displays untrusted text inertly', async () => {
    const citation = {
      evidence_id: 'news-record:1', source_id: 'sample', source_name: '<img src=x>',
      original_url: 'https://example.test/original?edition=1', published_at: null, published_on: '2020-03-20',
      provider_observed_at: null, retrieved_at: '2026-11-09T14:20:00Z', relevance: 'direct',
    } as const;
    await start({ ...insufficient, coverage: 'partial', citations: [citation,
      { ...citation, evidence_id: 'news-record:2', source_name: 'Esquema inválido', original_url: 'javascript:alert(1)' },
      { ...citation, evidence_id: 'news-record:3', source_name: 'URL con credenciales', original_url: 'https://user:pass@example.test/private' },
    ] });
    fireEvent.change(screen.getByLabelText(/Pregunta personalizada/), { target: { value: '¿Hubo cambios en la demanda?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Analizar acontecimiento' }));
    const link = await screen.findByRole('link', { name: /<img src=x>/ });
    expect(link.getAttribute('href')).toBe(citation.original_url);
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByRole('link', { name: /Esquema inválido|URL con credenciales/ })).toBeNull();
    const analysis = http.mock.calls.find(([url]) => url === '/api/analysis');
    expect(JSON.parse(String(analysis?.[1].body)).question).toBe('¿Hubo cambios en la demanda?');
  });

  it('rejects a successful analysis response with an incompatible nested payload', async () => {
    await start();
    http.mockImplementationOnce(() => json({ ...insufficient, citations: [null] }));
    fireEvent.click(screen.getByRole('button', { name: 'Analizar acontecimiento' }));
    expect((await screen.findByRole('alert')).textContent).toContain('no coincide con el contrato');
    expect(screen.queryByRole('article', { name: 'Resultado de la consulta' })).toBeNull();
  });

  it('ignores a delayed response after changing assets', async () => {
    const select = await start();
    let resolve!: (response: Response) => void;
    http.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    fireEvent.click(screen.getByRole('button', { name: 'Analizar acontecimiento' }));
    const attempt = http.mock.lastCall?.[1].signal;
    fireEvent.change(select, { target: { value: '234' } });
    await act(async () => resolve(Response.json(insufficient)));
    expect(attempt?.aborted).toBe(true);
    expect(screen.queryByRole('article', { name: 'Resultado de la consulta' })).toBeNull();
    expect(screen.getByPlaceholderText(/a CL=F\?/)).toBeDefined();
  });
});
