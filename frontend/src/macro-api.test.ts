import { afterEach, expect, it, vi } from 'vitest';
import { askMacroAnalysis } from './macro-api';

it.each([
  ['admin_auth_limit', 'Too many credential attempts.'],
  ['admin_research_limit', 'The administrator research limit has been reached.'],
  ['research_attempt_limit', 'Too many administrator requests.'],
  [undefined, 'Too many administrator requests.'],
])('distinguishes administrator 429 responses (%s)', async (code, message) => {
  vi.stubGlobal('fetch', vi.fn(async () => code
    ? Response.json({ code }, { status: 429, headers: { 'Retry-After': '60' } })
    : new Response(null, { status: 429 })));
  await expect(askMacroAnalysis(question, start, end, new AbortController().signal, context,
    undefined, '0'.repeat(64))).rejects.toThrow(message);
});

const question = 'How did the COVID-19 pandemic affect EUR/USD rate?';
const start = '2020-02-01';
const end = '2020-12-31';
const context = { indicator_id: 'eur-usd', event_id: 'covid' } as const;
const series = { id: 'series:eur-usd', kind: 'series', title: 'ECB reference rate',
  url: 'https://data.ecb.europa.eu/fixture', attribution: 'ECB statistics',
  period_start: start, period_end: end, published_on: null };
const document = { id: 'doc:fixture:1', kind: 'document', title: 'Synthetic institutional context',
  url: 'https://www.ecb.europa.eu/fixture', attribution: 'Synthetic test fixture',
  rights_url: 'https://www.ecb.europa.eu/rights', period_start: null, period_end: null, published_on: '2020-03-01',
  similarity_metric: 'cosine', similarity_score: 0.815123 };
function response() {
  return { status: 'answered', question, start, end, ...context, facts: [], calculations: [], interpretation: [],
    narrative: [{ text: 'Synthetic supported narrative.', citations: [series.id, document.id] }],
    model_id: null, evidence: [series, document], limitations: ['Synthetic fixture, not live Gemini.'], graph_steps: ['finish'] };
}
async function ask(payload: unknown, selected = true) {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(payload)));
  return askMacroAnalysis(question, start, end, new AbortController().signal, selected ? context : undefined);
}
afterEach(() => vi.unstubAllGlobals());

it('sends explicit selected context and preserves genuine cosine and narrative fields', async () => {
  const result = await ask(response());
  expect(result.narrative?.[0].text).toBe('Synthetic supported narrative.');
  expect(result.evidence[1].similarity_score).toBe(0.815123);
  expect(result.evidence[1].rights_url).toBe('https://www.ecb.europa.eu/rights');
  const fetch = vi.mocked(globalThis.fetch);
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ question, start, end, ...context });
});

it('accepts legacy optional fields while retaining legacy request shape without context', async () => {
  const payload = response();
  const { narrative: _n, model_id: _m, indicator_id: _i, event_id: _e, ...legacy } = payload;
  const result = await ask(legacy, false);
  expect(result.narrative).toBeUndefined();
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({ question, start, end });
  await expect(ask(legacy)).resolves.toBeTruthy();
});

it.each([
  { indicator_id: 'hicp-yoy' }, { event_id: 'ukraine' }, { model_id: 'invented-model' },
  { narrative: [{ text: 'Invented citation.', citations: ['doc:invented:1'] }] },
  { narrative: [{ text: 'Duplicate citation.', citations: [series.id, series.id] }] },
  { narrative: [] }, { evidence: [series, series] },
])('rejects mismatched or malformed narrative contracts: %j', async (change) => {
  await expect(ask({ ...response(), ...change })).rejects.toThrow();
});

it.each([
  { similarity_score: -0.1 }, { similarity_score: 1.1 }, { similarity_metric: 'distance' },
  { similarity_metric: null }, { similarity_score: null }, { rights_url: 'https://example.test/rights' },
])('rejects invalid similarity or rights metadata: %j', async (change) => {
  await expect(ask({ ...response(), evidence: [series, { ...document, ...change }] })).rejects.toThrow();
});

it('rejects a claimed cosine order that increases while preserving valid descending order', async () => {
  const second = { ...document, id: 'doc:second:1', similarity_score: 0.9 };
  await expect(ask({ ...response(), evidence: [series, document, second] })).rejects.toThrow('retrieval order');
  await expect(ask({ ...response(), evidence: [series, second, document] })).resolves.toBeTruthy();
});

it('accepts safe empty-evidence abstention without a fabricated model or score', async () => {
  const result = await ask({ ...response(), status: 'insufficient_evidence', narrative: [], evidence: [] });
  expect(result.evidence).toEqual([]);
  expect(result.model_id).toBeNull();
});

it('validates the fallback as isolated unverified text with the same RAG diagnosis', async () => {
  const fallback = {
    status: 'answered', triggered: true, verified: false, reason: 'ambiguous_interpretation',
    model_id: 'gemini-3.6-flash',
    answer: 'M3 includes liquid deposits. During COVID-19, ECB policy could affect saving. Eurostat can provide economic context.',
  };
  const payload = {
    ...response(), status: 'insufficient_evidence', research_v2: true, support: 'insufficient',
    publication_cutoff: end, narrative: [], evidence: [],
    insufficiency_reason: fallback.reason, general_fallback: fallback,
  };
  const invoke = (body: unknown) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(body)));
    return askMacroAnalysis(question, start, end, new AbortController().signal,
      { ...context, research_version: 'v2' });
  };
  await expect(invoke(payload)).resolves.toMatchObject({ general_fallback: fallback });
  for (const change of [
    { verified: true }, { triggered: false }, { model_id: 'gemini-other-model' }, { citations: [] },
    { reason: 'missing_official_observations' }, { answer: 'An invented result of 25 percent.' },
    { answer: 'COVID-19 reduced the money stock by five percent.' },
    { answer: 'M3 fell to €５ billion while households shifted their savings.' },
    { answer: 'The ECB reports that AI boosted broad money growth.' },
    { answer: 'Eurostat’s latest figures show that broad money rose during the boom.' },
    { answer: 'For more information see invented-source.test/macro.' },
    { answer: 'A plausible reason. Reference: https://example.test.' },
    { status: 'failed', error: 'timeout' }, // an error cannot also include an answer
  ]) await expect(invoke({ ...payload, general_fallback: { ...fallback, ...change } })).rejects.toThrow('fallback');
  await expect(invoke({ ...payload, status: 'answered', support: 'supported',
    narrative: [{ text: 'Valid cited claim.', category: 'official observation', citations: [series.id] }],
  })).rejects.toThrow('fallback');
});

it('accepts a reviewed claim available after the economic window and checks its version and cutoff', async () => {
  const claimVersion = 'a'.repeat(64);
  const source = { ...document, id: 'claim:fixture-review:' + claimVersion,
    claim_id: 'fixture-review', claim_version: claimVersion, document_id: 'b'.repeat(64),
    available_at: '2026-11-14T15:28:23Z', source_id: 'ecb', claim_type: 'observed',
    event: 'covid', indicator: 'eur-usd', geography: 'bilateral', measure: 'spot_reference_rate',
    unit: 'USD per EUR', event_linkage: 'contemporaneous_non_causal', relation: 'direct',
    locator: 'Figure 1', qualifiers: 'Contemporaneous observation.',
    similarity_metric: null, similarity_score: null };
  const payload = { ...response(), research_v2: true, support: 'partial',
    publication_cutoff: '2026-11-14', evidence: [source],
    narrative: [{ text: 'Approved documentary statement.', citations: [source.id], category: 'documentary context' }] };
  const invoke = (body: unknown) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(body)));
    return askMacroAnalysis(question, start, end, new AbortController().signal,
      { ...context, research_version: 'v2' });
  };
  await expect(invoke(payload)).resolves.toMatchObject({ evidence: [source] });
  await expect(invoke({ ...payload, publication_cutoff: end })).rejects.toThrow('provenance');
  await expect(invoke({ ...payload, evidence: [{ ...source, claim_version: 'c'.repeat(64) }] }))
    .rejects.toThrow('provenance');
});
