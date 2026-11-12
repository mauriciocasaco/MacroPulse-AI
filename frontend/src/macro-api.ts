import { launcherReadHeader } from '../pwa/local-read';

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

export interface ResearchClaim {
  text: string;
  citations: string[];
  category?: ResearchCategory;
}

const researchCategories = ['official observation', 'calculated from official observations',
  'documentary context', 'institutional interpretation', 'proxy context'] as const;
export type ResearchCategory = typeof researchCategories[number];

export interface ResearchEvidence {
  id: string;
  kind: 'series' | 'document';
  title: string;
  url: string;
  attribution: string;
  period_start: string | null;
  period_end: string | null;
  published_on: string | null;
  updated_at?: string | null;
  rights_url?: string | null;
  similarity_score?: number | null;
  similarity_metric?: 'cosine' | null;
  claim_id?: string;
  document_id?: string;
  event?: string;
  indicator?: string;
  geography?: string;
  relation?: 'direct' | 'context' | 'proxy';
  locator?: string;
  qualifiers?: string;
  claim_type?: 'observed' | 'institutional_interpretation';
  series_key?: string;
  unit?: string;
  multiplier?: number;
  adjustment?: string;
}

export interface ObservedChange {
  indicator_id: string;
  value: string;
  unit: 'percent' | 'percentage_points';
  direction: 'up' | 'down' | 'flat';
  first_observed_on: string;
  last_observed_on: string;
  citation: string;
}

export interface MacroAnalysis {
  status: 'answered' | 'insufficient_evidence';
  question: string;
  start: string;
  end: string;
  facts: ResearchClaim[];
  calculations: ResearchClaim[];
  interpretation: ResearchClaim[];
  limitations: string[];
  evidence: ResearchEvidence[];
  graph_steps: string[];
  narrative?: ResearchClaim[];
  narrative_kind?: 'gemini' | 'deterministic' | null;
  observed_changes?: ObservedChange[];
  model_id?: string | null;
  indicator_id?: string | null;
  event_id?: string | null;
  research_v2?: true;
  support?: 'supported' | 'partial' | 'insufficient';
  publication_cutoff?: string;
}

const ids = new Set(['eur-usd', 'm3-stock', 'deposit-rate', 'real-gdp-qoq', 'hicp-yoy']);
const frequencies = new Set(['daily', 'monthly', 'quarterly', 'effective_date']);
const sourceHosts = new Set(['data.ecb.europa.eu', 'www.ecb.europa.eu', 'ec.europa.eu', 'eur-lex.europa.eu', 'doi.org']);
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

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function claims(value: unknown): value is ResearchClaim[] {
  return Array.isArray(value) && value.every((item) => object(item)
    && typeof item.text === 'string' && strings(item.citations));
}

function researchEvidence(value: unknown): value is ResearchEvidence {
  return object(value) && ['series', 'document'].includes(String(value.kind))
    && ['id', 'title', 'attribution'].every((key) => typeof value[key] === 'string')
    && officialLink(value.url)
    && ['period_start', 'period_end', 'published_on'].every((key) => value[key] === null || date(value[key]))
    && (value.updated_at == null || (value.kind === 'series' && typeof value.updated_at === 'string'
      && Number.isFinite(Date.parse(value.updated_at))))
    && (value.rights_url == null || officialLink(value.rights_url))
    && (value.similarity_metric == null || value.similarity_metric === 'cosine')
    && (value.similarity_score == null || (value.kind === 'document' && value.similarity_metric === 'cosine'
      && typeof value.similarity_score === 'number' && Number.isFinite(value.similarity_score)
      && value.similarity_score >= 0 && value.similarity_score <= 1))
    && (value.similarity_metric == null || value.similarity_score != null);
}

function observedChange(value: unknown, start: string, end: string): value is ObservedChange {
  if (!object(value) || typeof value.indicator_id !== 'string' || !ids.has(value.indicator_id)
    || typeof value.value !== 'string' || !/^-?\d+\.\d{2}$/.test(value.value)
    || !Number.isFinite(Number(value.value)) || value.value === '-0.00'
    || value.unit !== (['eur-usd', 'm3-stock'].includes(value.indicator_id) ? 'percent' : 'percentage_points')
    || value.citation !== `series:${value.indicator_id}`
    || !date(value.first_observed_on) || !date(value.last_observed_on)
    || value.first_observed_on < start || value.last_observed_on > end
    || value.first_observed_on >= value.last_observed_on) return false;
  const number = Number(value.value);
  return number > 0 ? value.direction === 'up' : number < 0 ? value.direction === 'down'
    : value.direction === 'flat' || value.direction === 'up' || value.direction === 'down';
}

function hasV2Provenance(item: ResearchEvidence, cutoff: string): boolean {
  if (item.kind === 'series') {
    return ['series_key', 'unit', 'geography', 'adjustment'].every((key) =>
      typeof item[key as keyof ResearchEvidence] === 'string') && Number.isInteger(item.multiplier);
  }
  const sha256 = /^[a-f0-9]{64}$/;
  return typeof item.claim_id === 'string' && sha256.test(item.claim_id) && item.id === `claim:${item.claim_id}`
    && typeof item.document_id === 'string' && sha256.test(item.document_id)
    && item.published_on !== null && item.published_on <= cutoff
    && typeof item.indicator === 'string' && ids.has(item.indicator)
    && typeof item.geography === 'string' && typeof item.locator === 'string' && typeof item.qualifiers === 'string'
    && ['covid', 'ukraine', 'genai'].includes(item.event ?? '')
    && ['direct', 'context', 'proxy'].includes(item.relation ?? '')
    && ['observed', 'institutional_interpretation'].includes(item.claim_type ?? '');
}

async function get(path: string, signal: AbortSignal, onResponse?: (response: Response) => void): Promise<unknown> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (import.meta.env.MODE === 'windows-launcher') headers[launcherReadHeader] = '1';
  const response = await fetch(`/api/macro/indicators${path}`, {
    signal, cache: 'no-store', headers,
  });
  if (!response.ok) throw new Error('Official data is currently unavailable.');
  onResponse?.(response);
  return response.json();
}

export async function getMacroIndicators(
  signal: AbortSignal, onResearchV2?: (enabled: boolean) => void, onPublicSiteKey?: (sitekey: string) => void,
): Promise<MacroIndicator[]> {
  let researchV2 = false;
  let sitekey = '';
  const result = await get('', signal, (response) => {
    researchV2 = response.headers?.get('X-MarketPulse-Research-V2') === 'true';
    const advertised = response.headers?.get('X-MacroPulse-Turnstile-Sitekey') ?? '';
    if (/^[A-Za-z0-9_-]{12,80}$/.test(advertised)) sitekey = advertised;
  });
  if (!Array.isArray(result) || result.length !== ids.size || !result.every(indicator)
    || new Set(result.map(({ id }) => id)).size !== ids.size) {
    throw new Error('Unexpected macro catalog.');
  }
  if (!signal.aborted) {
    onResearchV2?.(researchV2);
    onPublicSiteKey?.(sitekey);
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

export async function askMacroAnalysis(
  question: string, start: string, end: string, signal: AbortSignal,
  context?: { indicator_id: string; event_id: 'covid' | 'ukraine' | 'generative-ai'; research_version?: 'v2' },
  turnstileToken?: string,
): Promise<MacroAnalysis> {
  const response = await fetch('/api/macro/analysis', {
    method: 'POST', signal, cache: 'no-store',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json',
      ...(turnstileToken ? { 'X-MacroPulse-Turnstile': turnstileToken } : {}) },
    body: JSON.stringify({ question, start, end, ...context }),
  });
  if (!response.ok) {
    if (!turnstileToken) throw new Error('Local macro research is unavailable.');
    if (response.status === 403) throw new Error('Complete a new Turnstile check.');
    if (response.status === 429) throw new Error('The public research quota has been reached. Please try again later.');
    if (response.status === 408 || response.status === 504) throw new Error('Research timed out. Please try again.');
    if (response.status === 503 && response.headers.get('Retry-After')) {
      throw new Error('Public research is busy. Please try again shortly.');
    }
    if (response.status === 422) throw new Error('Review the selected event and question before trying again.');
    throw new Error('Public research is currently unavailable.');
  }
  const result: unknown = await response.json();
  if (!object(result) || (result.status !== 'answered' && result.status !== 'insufficient_evidence')
    || result.question !== question || result.start !== start || result.end !== end
    || (context !== undefined && ((result.indicator_id != null && result.indicator_id !== context.indicator_id)
      || (result.event_id != null && result.event_id !== context.event_id)))
    || (result.narrative !== undefined && (!claims(result.narrative)
      || (result.status === 'answered' && result.narrative.length === 0)))
    || !(result.narrative_kind == null || (result.status === 'answered' && claims(result.narrative)
      && result.narrative.length > 0 && (result.narrative_kind === 'gemini' || result.narrative_kind === 'deterministic')))
    || !(result.model_id == null || (typeof result.model_id === 'string' && /^gemini-[a-z0-9]+(?:[.-][a-z0-9]+){1,6}$/.test(result.model_id)))
    || !claims(result.facts) || !claims(result.calculations) || !claims(result.interpretation)
    || !strings(result.limitations) || !strings(result.graph_steps)
    || !Array.isArray(result.evidence) || !result.evidence.every(researchEvidence)) {
    throw new Error('Unexpected research response.');
  }
  const researchV2 = context?.research_version === 'v2';
  if (researchV2 && (result.research_v2 !== true || !date(result.publication_cutoff)
    || result.publication_cutoff > end || !['supported', 'partial', 'insufficient'].includes(String(result.support))
    || (result.support === 'insufficient') !== (result.status === 'insufficient_evidence')
    || !claims(result.narrative) || !result.narrative.every(({ category }) =>
      category !== undefined && researchCategories.includes(category)))) {
    throw new Error('Unexpected Research v2 response.');
  }
  const evidence = result.evidence as ResearchEvidence[];
  if (researchV2 && !evidence.every((item) => hasV2Provenance(item, result.publication_cutoff as string))) {
    throw new Error('Unexpected Research v2 provenance.');
  }
  const changes = result.observed_changes ?? [];
  if (!Array.isArray(changes) || !changes.every((item) => observedChange(item, start, end)
    && (!context || item.indicator_id === context.indicator_id)
    && evidence.some((source) => source.kind === 'series' && source.id === item.citation))
    || new Set(changes.map(({ indicator_id }) => indicator_id)).size !== changes.length) {
    throw new Error('Unexpected observed change.');
  }
  const citations = new Set(evidence.map(({ id }) => id));
  const allClaims = [...result.facts, ...result.calculations, ...result.interpretation,
    ...(result.narrative as ResearchClaim[] | undefined ?? [])];
  if (citations.size !== evidence.length || allClaims.some((claim) => claim.citations.length === 0
    || new Set(claim.citations).size !== claim.citations.length
    || claim.citations.some((id) => !citations.has(id)))) throw new Error('Unexpected research citations.');
  const documents = evidence.filter(({ kind }) => kind === 'document');
  if (documents.some((entry, index) => index > 0 && entry.similarity_score != null
    && documents[index - 1].similarity_score != null
    && entry.similarity_score > documents[index - 1].similarity_score!)) throw new Error('Unexpected retrieval order.');
  return {
    ...(researchV2 ? {
      research_v2: true as const,
      support: result.support as MacroAnalysis['support'],
      publication_cutoff: result.publication_cutoff as string,
    } : {}),
    narrative: result.narrative as ResearchClaim[] | undefined,
    narrative_kind: result.narrative_kind as MacroAnalysis['narrative_kind'],
    observed_changes: changes,
    model_id: result.model_id as string | null | undefined,
    indicator_id: result.indicator_id as string | null | undefined,
    event_id: result.event_id as string | null | undefined,
    status: result.status, question, start, end, facts: result.facts, calculations: result.calculations,
    interpretation: result.interpretation, limitations: result.limitations,
    evidence: result.evidence, graph_steps: result.graph_steps,
  };
}
