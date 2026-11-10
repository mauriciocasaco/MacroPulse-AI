export interface Asset {
  asset_source_id: number;
  asset_id: string;
  name: string;
  source_id: string;
  provider_name: string;
  symbol: string;
  economic_unit: string;
  calendar_code: string;
  first_date: string | null;
  last_date: string | null;
  bar_count: number;
}

// FastAPI serializes Decimal as a string. A numeric value can still be plotted.
export type Decimal = string | number;

export interface DailyBar {
  bar_date: string;
  close: Decimal | null;
  adjusted_close: Decimal | null;
  retrieved_at: string;
  availability_time: string | null;
  session_status: 'closed' | 'provisional' | 'unknown';
  quality_status: 'complete' | 'partial';
  source_convention: string;
  rolling_method: string | null;
}

export interface HistoryResponse {
  asset: Asset;
  bars: DailyBar[];
}

export interface DateWindow {
  start: string;
  end_exclusive: string;
}

export type Event = 'covid' | 'ukraine' | 'genai';

export interface AnalysisRequest extends DateWindow {
  asset: string;
  event: Event;
  question: string;
}

interface ObservedClose {
  date: string;
  close: Decimal;
  retrieved_at: string;
}

interface PriceSummary {
  source_id: string;
  asset_source_id: number;
  economic_unit: string;
  price_basis: 'provider_unadjusted';
  source_convention: string;
  rolling_method: string | null;
  observations: number;
  first: ObservedClose | null;
  last: ObservedClose | null;
  minimum_close: ObservedClose | null;
  maximum_close: ObservedClose | null;
  close_change: Decimal | null;
  close_change_pct: Decimal | null;
}

export interface Citation {
  evidence_id: string;
  source_id: string;
  source_name: string;
  publisher?: string | null;
  title?: string | null;
  original_url: string;
  published_at: string | null;
  published_on: string | null;
  provider_observed_at: string | null;
  retrieved_at: string;
  relevance: 'direct' | 'contextual';
}

export interface AnalysisResponse extends DateWindow {
  asset: string;
  asset_id: string;
  event: Event;
  as_of_exclusive: string | null;
  prices: PriceSummary;
  coverage: 'sufficient' | 'partial' | 'insufficient';
  citations: Citation[];
  economic_mechanisms: { citation_id: string; summary: string }[];
  economic_interpretation: string;
  limitations: string[];
  provider: 'simulated';
}

const errors: Record<number, string> = {
  404: 'The requested resource is unavailable.',
  413: 'The request is too large.',
  422: 'Check the request parameters and try again.',
  502: 'The service could not complete the request.',
  503: 'The service or its data source is unavailable.',
};

type JsonObject = Record<string, unknown>;
const object = (value: unknown): value is JsonObject => typeof value === 'object' && value !== null && !Array.isArray(value);
const textFields = (value: JsonObject, fields: string[]) => fields.every((field) => typeof value[field] === 'string');
const optionalText = (value: unknown) => value === null || typeof value === 'string';
const date = (value: unknown) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(`${value}T00:00:00Z`));
const decimal = (value: unknown): value is Decimal => (typeof value === 'number'
  || (typeof value === 'string' && /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())))
  && Number.isFinite(Number(value));

// Check the shape the UI consumes. FastAPI owns validation of the financial data.
function isAsset(value: unknown): value is Asset {
  return object(value) && typeof value.asset_source_id === 'number'
    && Number.isSafeInteger(value.asset_source_id) && value.asset_source_id > 0
    && textFields(value, ['asset_id', 'name', 'source_id', 'provider_name', 'symbol', 'economic_unit', 'calendar_code'])
    && (value.first_date === null || date(value.first_date)) && (value.last_date === null || date(value.last_date));
}

type RawBar = Omit<DailyBar, 'close' | 'adjusted_close'> & { close?: unknown; adjusted_close?: unknown };

function isDatedBar(value: unknown): value is RawBar {
  return object(value) && date(value.bar_date)
    && textFields(value, ['retrieved_at', 'source_convention']) && optionalText(value.rolling_method);
}

function isAnalysis(value: unknown, query: AnalysisRequest): value is AnalysisResponse {
  if (!object(value) || !object(value.prices)) return false;
  const { prices, citations, economic_mechanisms, limitations } = value;
  return value.asset_id === query.asset && value.event === query.event
    && value.start === query.start && value.end_exclusive === query.end_exclusive
    && (value.as_of_exclusive === null || date(value.as_of_exclusive))
    && textFields(value, ['asset', 'economic_interpretation']) && value.provider === 'simulated'
    && (value.coverage === 'sufficient' || value.coverage === 'partial' || value.coverage === 'insufficient')
    && typeof prices.observations === 'number' && textFields(prices, ['source_id', 'economic_unit', 'source_convention'])
    && optionalText(prices.rolling_method)
    && ['first', 'last', 'minimum_close', 'maximum_close'].every((key) => {
      const item = prices[key];
      return item === null || (object(item) && date(item.date) && decimal(item.close) && typeof item.retrieved_at === 'string');
    })
    && [prices.close_change, prices.close_change_pct].every((value) => value === null || decimal(value))
    && Array.isArray(citations) && citations.every((item) => object(item)
      && textFields(item, ['evidence_id', 'source_id', 'source_name', 'original_url', 'retrieved_at'])
      && (item.relevance === 'direct' || item.relevance === 'contextual')
      && (item.publisher === undefined || optionalText(item.publisher))
      && (item.title === undefined || optionalText(item.title))
      && (item.published_on === null || date(item.published_on))
      && [item.published_at, item.published_on, item.provider_observed_at].every(optionalText))
    && Array.isArray(economic_mechanisms) && economic_mechanisms.every((item) => object(item) && textFields(item, ['citation_id', 'summary']))
    && Array.isArray(limitations) && limitations.every((item) => typeof item === 'string');
}

async function request<T>(path: string, signal: AbortSignal, matches: (value: unknown) => value is T, body?: AnalysisRequest): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      signal,
      headers: { Accept: 'application/json', ...(body && { 'Content-Type': 'application/json' }) },
      ...(body && { method: 'POST', body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error('Could not connect. Check that the local service is running.');
  }

  if (!response.ok) {
    throw new Error(`${errors[response.status] ?? 'The service could not respond.'} (HTTP ${response.status})`);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error('The service returned invalid data.');
  }
  if (!matches(payload)) throw new Error('The service returned an unexpected response.');
  return payload;
}

export function getAssets(signal: AbortSignal): Promise<Asset[]> {
  return request('/assets', signal, (value): value is Asset[] => Array.isArray(value) && value.every(isAsset));
}

export async function getHistory(id: number, window: DateWindow, signal: AbortSignal): Promise<HistoryResponse> {
  const params = new URLSearchParams({ start: window.start, end_exclusive: window.end_exclusive });
  const { asset, bars } = await request(`/assets/${id}/history?${params}`, signal,
    (value): value is { asset: Asset; bars: RawBar[] } =>
    object(value) && isAsset(value.asset) && value.asset.asset_source_id === id
    && Array.isArray(value.bars) && value.bars.every(isDatedBar));
  // A dated bar can be missing one price field. Keep its date and other fields intact.
  return { asset, bars: bars.map((bar) => ({
    ...bar,
    close: decimal(bar.close) ? bar.close : null,
    adjusted_close: decimal(bar.adjusted_close) ? bar.adjusted_close : null,
  })) };
}

export function getAnalysis(query: AnalysisRequest, signal: AbortSignal): Promise<AnalysisResponse> {
  return request('/analysis', signal, (value): value is AnalysisResponse => isAnalysis(value, query), query);
}
