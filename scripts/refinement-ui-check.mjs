// Isolated browser smoke checks. API/LLM replies are synthetic except an optional
// public observation snapshot supplied with --history. No real accounts are used.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const options = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, i, all) => {
  if (value.startsWith('--')) pairs.push([value.slice(2), all[i + 1]]);
  return pairs;
}, []));
const { chromium } = await import(options.module ? pathToFileURL(resolve(options.module)).href : 'playwright');
const base = options.url ?? 'http://127.0.0.1:5194';
assert.equal(new URL(base).hostname, '127.0.0.1', 'Only loopback test servers are allowed');
const output = resolve(options.output ?? '.local/codex-telemetry/refinement-validation');
await mkdir(output, { recursive: true });
const snapshot = options.history ? JSON.parse(await readFile(resolve(options.history), 'utf8')) : null;
const entries = [
  ['eur-usd', 'EUR/USD rate', 'daily', 'USD/EUR', 0],
  ['m3-stock', 'Eurozone M3', 'monthly', 'EUR', 6],
  ['deposit-rate', 'ECB rate', 'effective_date', 'percent_per_annum', 0],
  ['real-gdp-qoq', 'Eurozone GDP', 'quarterly', 'percent_qoq', 0],
  ['hicp-yoy', 'Eurozone Inflation', 'monthly', 'percent_yoy', 0],
];
const catalog = entries.map(([id, title, frequency, unit, multiplier]) => ({
  id, title, frequency, unit, multiplier, provider: 'ecb', series_key: 'synthetic-browser-fixture',
  geography: 'fixture', adjustment: 'fixture', source_url: 'https://data.ecb.europa.eu/',
  reuse_url: 'https://www.ecb.europa.eu/', attribution: 'ECB statistics',
}));
if (snapshot) catalog[0] = snapshot.indicator;
function points(indicator) {
  if (snapshot && indicator.id === 'eur-usd') return snapshot.observations;
  return ['2020-02-03', '2020-12-31', '2023-09-29', '2026-09-30'].map((day, i) => ({
    period_raw: indicator.frequency === 'monthly' ? day.slice(0, 7)
      : indicator.frequency === 'quarterly' ? `${day.slice(0, 4)}-Q${Math.floor((+day.slice(5, 7) - 1) / 3) + 1}` : day,
    period_start: day, period_end: day, effective_date: indicator.frequency === 'effective_date' ? day : null,
    value: indicator.id === 'm3-stock' ? `${17612461 + i}.010` : `${1 + i}.2300`,
    status_kind: 'normal', status_raw: null,
  }));
}
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, serviceWorkers: 'block' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
const requests = [];
let responseMode = 'answer';
await page.route('**/api/**', async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname === '/api/macro/indicators') return route.fulfill({ json: catalog });
  if (url.pathname.endsWith('/history')) {
    const indicator = catalog.find((item) => url.pathname.includes(`/${item.id}/`));
    assert.ok(indicator);
    const start = url.searchParams.get('start');
    const end = url.searchParams.get('end');
    assert.ok((Date.parse(end) - Date.parse(start)) / 86400000 < 2600);
    return route.fulfill({ json: { indicator, start, end, updated_at: snapshot?.as_of ?? '2026-11-10T12:00:00Z',
      observations: points(indicator).filter((row) => row.period_end >= start && row.period_start <= end),
      anchor: null, coverage_start: null, coverage_end: null } });
  }
  assert.equal(url.pathname, '/api/macro/analysis');
  const request = route.request().postDataJSON();
  requests.push(request);
  if (responseMode === 'error') return route.fulfill({ status: 503, json: { detail: 'Synthetic failure' } });
  const evidence = responseMode === 'empty' ? [] : [
    { id: 'series:fixture', kind: 'series', title: 'Synthetic calculation fixture',
      url: 'https://data.ecb.europa.eu/', attribution: 'Synthetic fixture',
      period_start: request.start, period_end: request.end, published_on: null },
    { id: 'doc:fixture', kind: 'document', title: 'Synthetic institutional document fixture',
      url: 'https://www.ecb.europa.eu/', attribution: 'Synthetic fixture',
      period_start: null, period_end: null, published_on: request.start,
      similarity_score: 0.81256, similarity_metric: 'cosine' },
  ];
  return route.fulfill({ json: { ...request, status: responseMode === 'empty' ? 'insufficient_evidence' : 'answered',
    facts: [], calculations: [], interpretation: [],
    narrative: responseMode === 'empty' ? [] : [{ text: 'This synthetic browser reply does not verify live Gemini or establish causality.', citations: ['series:fixture', 'doc:fixture'] }],
    model_id: null, evidence, limitations: ['Synthetic browser fixture; no live LLM call.'], graph_steps: ['finish'] } });
});
const checks = [];
function checked(name) { checks.push(name); }
try {
  await page.goto(base);
  await page.getByRole('heading', { name: 'AI-Powered Macroeconomic Intelligence', exact: true }).waitFor();
  assert.ok(await page.getByText('Powered by LLMs, RAG, LangChain and LangGraph.', { exact: true }).isVisible());
  checked('exact English title and subtitle');
  assert.equal(await page.getByRole('button', { name: 'MAX', exact: true }).count(), 0);
  await page.getByRole('button', { name: '7Y', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '7Y', exact: true }).getAttribute('aria-pressed'), 'true');
  checked('7Y available and MAX absent');
  assert.equal(await page.getByText('View observations', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('table').count(), 0);
  checked('redundant chart metadata removed');
  const chart = page.locator('svg.macro-plot');
  await chart.waitFor();
  await chart.focus();
  await page.keyboard.press('End');
  const tooltip = page.getByRole('tooltip');
  await tooltip.waitFor();
  const expected = points(catalog[0]).at(-1);
  assert.ok((await tooltip.innerText()).includes(expected.value));
  assert.ok((await tooltip.innerText()).includes(expected.period_raw));
  assert.ok((await tooltip.innerText()).includes('USD per EUR'));
  assert.equal(await tooltip.evaluate((el) => getComputedStyle(el).getPropertyValue('--indicator-color').trim()), '#16A34A');
  checked(snapshot ? `exact official ECB tooltip: ${expected.period_raw}=${expected.value}` : 'exact synthetic decimal tooltip');
  await page.keyboard.press('Home');
  assert.ok((await tooltip.innerText()).includes(points(catalog[0])[0].value));
  await page.keyboard.press('Escape');
  assert.equal(await tooltip.count(), 0);
  checked('keyboard Home End Escape');
  const dot = chart.locator('circle').last();
  await dot.hover();
  await tooltip.waitFor();
  assert.ok((await tooltip.innerText()).includes(expected.value));
  checked('pointer hover exact observation');
  await page.screenshot({ path: resolve(output, snapshot ? 'chart-official-data.png' : 'chart-synthetic.png'), fullPage: true });
  await page.mouse.move(5, 5);
  assert.ok(await page.getByRole('link', { name: 'Reuse terms', exact: true }).isVisible());
  checked('source and reuse attribution preserved');
  const composer = page.getByRole('form', { name: 'Macro research question composer' });
  await composer.getByRole('button', { name: 'Eurozone M3', exact: true }).click();
  assert.equal(await page.getByLabel('Indicator', { exact: true }).inputValue(), 'm3-stock');
  await page.getByLabel('Indicator', { exact: true }).selectOption('hicp-yoy');
  assert.equal(await composer.getByRole('button', { name: 'Eurozone Inflation', exact: true }).getAttribute('aria-pressed'), 'true');
  checked('bidirectional indicator synchronization');
  const events = [
    ['COVID-19', 'covid', '2020-02-01', '2020-12-31'],
    ['Ukraine War', 'ukraine', '2022-02-01', '2024-02-29'],
    ['Generative AI Boom', 'generative-ai', '2023-09-01', '2026-09-30'],
  ];
  for (const [label, id, start, end] of events) {
    await composer.getByRole('button', { name: label, exact: true }).click();
    assert.ok((await page.getByLabel('Research question', { exact: true }).inputValue()).includes('Eurozone Inflation'));
    await page.getByLabel('Research question', { exact: true }).fill('How did Eurozone Inflation change?');
    await composer.getByRole('button', { name: 'Ask LLM Model', exact: true }).click();
    await page.getByRole('heading', { name: 'LLM Model', exact: true }).waitFor();
    const body = requests.at(-1);
    assert.deepEqual(body, { question: 'How did Eurozone Inflation change?', start, end, indicator_id: 'hicp-yoy', event_id: id });
    await page.getByText('Cosine similarity 0.813', { exact: false }).waitFor();
    await composer.getByRole('button', { name: 'Use generated question', exact: true }).click();
    checked(`editable ${label} question and exact window`);
  }
  assert.equal(await page.getByRole('button', { name: 'Ask LLM Model', exact: true }).count(), 1);
  for (const label of ['Facts', 'Calculations', 'Interpretation']) assert.equal(await page.getByRole('heading', { name: label, exact: true }).count(), 0);
  checked('single submit and coherent narrative presentation');
  await page.screenshot({ path: resolve(output, 'desktop-synthetic-flow.png'), fullPage: true });
  responseMode = 'empty';
  await composer.getByRole('button', { name: 'Ask LLM Model', exact: true }).click();
  await page.getByText('There is insufficient evidence for an answer.', { exact: true }).waitFor();
  assert.equal(await page.getByText('No institutional documents were retrieved for this answer.', { exact: true }).count(), 1);
  assert.equal(await page.getByText(/Cosine similarity/).count(), 0);
  checked('empty evidence abstains without invented scores or documents');
  responseMode = 'error';
  await composer.getByRole('button', { name: 'Ask LLM Model', exact: true }).click();
  await page.getByRole('alert').waitFor();
  checked('safe error state');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel('Indicator', { exact: true }).selectOption('m3-stock');
  await chart.focus();
  await page.keyboard.press('End');
  assert.ok((await tooltip.innerText()).includes('Millions of EUR'));
  const box = await tooltip.boundingBox();
  assert.ok(box && box.x >= -1 && box.x + box.width <= 391, 'mobile tooltip stays within viewport');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  checked('mobile layout and correctly scaled M3 tooltip');
  await page.screenshot({ path: resolve(output, 'mobile-synthetic-m3.png'), fullPage: true });
  assert.deepEqual(errors, []);
  checked('no browser runtime exceptions');
  const report = { checks: checks.length, passed: checks, officialObservationSnapshot: snapshot ? { source: snapshot.source, as_of: snapshot.as_of, sha256: snapshot.sha256, observations: snapshot.observations.length } : null,
    limitations: ['API and LLM response flow mocked; no live Gemini or PostgreSQL validation.', 'Official observations, if supplied, replayed locally without changing values.'] };
  await writeFile(resolve(output, snapshot ? 'browser-official-report.json' : 'browser-synthetic-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
