export type MacroFrequency = 'daily' | 'monthly' | 'quarterly' | 'effective_date';

export interface MacroIndicator {
  id: string;
  title: string;
  frequency: MacroFrequency;
  unit: string;
  multiplier: number;
  provider: string;
  series_key: string;
  geography: string;
  adjustment: string;
  source_url: string;
  reuse_url: string;
  attribution: string;
}

export interface MacroObservation {
  period_raw: string;
  period_start: string;
  period_end: string;
  effective_date: string | null;
  value: string | null;
  status_kind: string;
  status_raw: string | null;
}

export interface MacroHistory {
  indicator: MacroIndicator;
  start: string;
  end: string;
  updated_at: string | null;
  observations: MacroObservation[];
  anchor: MacroObservation | null;
  coverage_start: string | null;
  coverage_end: string | null;
}

const ids = new Set(['eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy']);
const frequencies = new Set(['daily', 'monthly', 'quarterly', 'effective_date']);
const sourceHosts = new Set(['data.ecb.europa.eu', 'www.ecb.europa.eu', 'ec.europa.eu', 'doi.org']);
const isoDate = /^\d{4}-\d{2}-\d{2}$/;
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function officialLink(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && sourceHosts.has(url.host) && !url.username && !url.password;
  } catch {
    return false;
  }
}

function indicator(value: unknown): value is MacroIndicator {
  return object(value) && typeof value.id === 'string' && ids.has(value.id)
    && typeof value.frequency === 'string' && frequencies.has(value.frequency)
    && Number.isInteger(value.multiplier)
    && ['title', 'unit', 'provider', 'series_key', 'geography', 'adjustment', 'attribution']
      .every((key) => typeof value[key] === 'string')
    && officialLink(value.source_url) && officialLink(value.reuse_url);
}

function date(value: unknown): value is string {
  return typeof value === 'string' && isoDate.test(value)
    && Number.isFinite(Date.parse(value));
}

function observation(value: unknown): value is MacroObservation {
  return object(value) && typeof value.period_raw === 'string'
    && date(value.period_start) && date(value.period_end) && value.period_start <= value.period_end
    && (value.effective_date === null || date(value.effective_date))
    && typeof value.status_kind === 'string'
    && (value.status_raw === null || typeof value.status_raw === 'string')
    && (value.value === null || (typeof value.value === 'string'
      && decimal.test(value.value) && Number.isFinite(Number(value.value))));
}

async function get(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(`/api/macro/indicators${path}`, {
    signal, cache: 'no-store', headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error('Official data is currently unavailable.');
  return response.json();
}

export async function getMacroIndicators(signal: AbortSignal): Promise<MacroIndicator[]> {
  const result = await get('', signal);
  if (!Array.isArray(result) || result.length !== ids.size || !result.every(indicator)
    || new Set(result.map(({ id }) => id)).size !== ids.size) {
    throw new Error('Unexpected macro catalog.');
  }
  return result;
}

export async function getMacroHistory(
  id: string, start: string, end: string, signal: AbortSignal,
): Promise<MacroHistory> {
  const query = new URLSearchParams({ start, end });
  const result = await get(`/${encodeURIComponent(id)}/history?${query}`, signal);
  if (!object(result) || !indicator(result.indicator) || result.indicator.id !== id
    || result.start !== start || result.end !== end
    || !(result.updated_at === null || (typeof result.updated_at === 'string'
      && Number.isFinite(Date.parse(result.updated_at))))
    || !Array.isArray(result.observations) || !result.observations.every(observation)
    || !(result.anchor === null || observation(result.anchor))
    || !(result.coverage_start === null || date(result.coverage_start))
    || !(result.coverage_end === null || date(result.coverage_end))) {
    throw new Error('Unexpected macro observations.');
  }
  return {
    indicator: result.indicator, start, end, updated_at: result.updated_at,
    observations: result.observations, anchor: result.anchor,
    coverage_start: result.coverage_start, coverage_end: result.coverage_end,
  };
}
