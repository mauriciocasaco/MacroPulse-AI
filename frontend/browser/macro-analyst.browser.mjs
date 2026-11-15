import { strict as assert } from 'node:assert';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { after, before, test } from 'node:test';
import { chromium, devices, webkit } from 'playwright-core';
import { createServer } from 'vite';

const output = resolve('../.local/codex-telemetry/macro-analyst');
// Keep the sticky header at its document position in full-page captures.
const workspaceCapture = { fullPage: true, style: '.topbar { position: relative; } .ripple-ink { visibility: hidden; }' };
const series = [
  ['eur-usd', 'daily', 'USD/EUR', 0, 'ecb'],
  ['m3-stock', 'monthly', 'EUR', 6, 'ecb'],
  ['deposit-rate', 'effective_date', 'percent_per_annum', 0, 'ecb'],
  ['real-gdp-qoq', 'quarterly', 'percent_qoq', 0, 'eurostat'],
  ['hicp-yoy', 'monthly', 'percent_yoy', 0, 'eurostat'],
];
const catalog = series.map(([id, frequency, unit, multiplier, provider]) => {
  const source = provider === 'ecb' ? 'https://data.ecb.europa.eu/' : 'https://ec.europa.eu/eurostat/';
  return { id, title: id, frequency, unit, multiplier, provider,
    geography: 'euro_area', adjustment: 'none', series_key: 'browser-fixture',
    source_url: source, reuse_url: source, attribution: provider === 'ecb' ? 'ECB statistics' : 'Eurostat' };
});
const dates = [
  '2020-06-05', '2025-12-31', '2026-01-30', '2026-02-27', '2026-03-31',
  '2026-04-30', '2026-05-29', '2026-06-05', '2026-06-08', '2026-06-09',
  '2026-06-30', '2026-07-31', '2026-08-31', '2026-09-30', '2026-10-30',
];
const values = [1.24, 1.17, 1.12, 1.14, 1.06, 1.09, 1.12, 1.16, 1.19, 1.17, 1.15, 1.14, 1.18, 1.22, 1.19];

function observations(indicator) {
  const eligible = indicator.frequency === 'daily' ? dates
    : dates.filter((date) => !['2026-06-05', '2026-06-08', '2026-06-09'].includes(date)
      && (indicator.frequency !== 'quarterly' || ['03', '06', '09', '12'].includes(date.slice(5, 7))));
  return eligible.map((date) => {
    const period_raw = indicator.frequency === 'monthly' ? date.slice(0, 7)
      : indicator.frequency === 'quarterly' ? date.slice(0, 4) + '-Q' + Math.ceil(Number(date.slice(5, 7)) / 3)
        : date;
    return { period_raw, period_start: date, period_end: date,
      effective_date: indicator.frequency === 'effective_date' ? date : null, status_kind: 'normal',
      status_raw: null, value: indicator.id === 'm3-stock' ? (17612461 - (dates.length - 1 - dates.indexOf(date)) * 30845) + '.010' : values[dates.indexOf(date)].toFixed(4) };
  });
}

let server;
let browser;
let base;
before(async () => {
  process.env.MARKETPULSE_LAN_DEV = 'false';
  process.env.MARKETPULSE_MACRO_AI_ENABLED = 'true';
  server = await createServer({ server: { port: 0, host: '127.0.0.1' } });
  await server.listen();
  base = 'http://127.0.0.1:' + server.httpServer.address().port;
  await mkdir(output, { recursive: true });
  browser = process.env.MACRO_BROWSER === 'webkit' ? await webkit.launch()
    : await chromium.launch({ channel: 'msedge', args: ['--enable-unsafe-swiftshader'] });
});
after(async () => {
  try { await browser?.close(); } finally { await server?.close(); }
});

async function withPage(options, run) {
  const context = await browser.newContext({ ...options, serviceWorkers: 'block' });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const errors = [];
  const research = [];
  let historyRequests = 0;
  page.on('pageerror', (error) => errors.push(error.message));
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== base) return route.abort();
    if (url.pathname === '/api/macro/indicators') {
      return route.fulfill({ json: catalog, headers: { 'X-MarketPulse-Research-V2': 'true' } });
    }
    const indicator = catalog.find(({ id }) => url.pathname === '/api/macro/indicators/' + id + '/history');
    if (indicator) {
      historyRequests++;
      const start = url.searchParams.get('start');
      const end = url.searchParams.get('end');
      return route.fulfill({ json: { indicator, start, end, updated_at: '2026-10-31T12:00:00Z',
        observations: observations(indicator).filter((row) => row.period_end >= start && row.period_end <= end),
        anchor: null, coverage_start: null, coverage_end: null } });
    }
    if (url.pathname === '/api/macro/analysis') {
      const request = route.request().postDataJSON();
      research.push(request);
      const v2 = request.research_version === 'v2';
      const evidence = { id: 'series:eur-usd', kind: 'series', title: 'ECB reference rate',
        url: 'https://data.ecb.europa.eu/', attribution: 'ECB statistics',
        period_start: request.start, period_end: request.end, published_on: null };
      return route.fulfill({ json: { ...request, status: v2 ? 'insufficient_evidence' : 'answered',
        facts: [], calculations: [], interpretation: [], graph_steps: ['fixture'],
        limitations: ['Synthetic browser test response.'],
        narrative: v2 ? [] : [{ text: 'The reference rate changed over the selected period.', citations: [evidence.id] }],
        evidence: v2 ? [] : [evidence],
        ...(v2 && { research_v2: true, publication_cutoff: request.end, support: 'insufficient',
          insufficiency_reason: 'unsupported_operation_or_series',
          general_fallback: { status: 'answered', triggered: true, verified: false,
            reason: 'unsupported_operation_or_series', model_id: 'gemini-3.6-flash',
            answer: 'A supply shock can raise the price level.' } }),
      } });
    }
    return url.pathname.startsWith('/api/') ? route.abort() : route.continue();
  });
  try {
    await page.clock.setSystemTime(new Date('2026-11-15T12:00:00Z'));
    await page.goto(base);
    await page.getByRole('slider').waitFor();
    await run(page, research, () => historyRequests);
    assert.deepEqual(errors, []);
  } catch (error) {
    error.message += '\nBrowser state: ' + JSON.stringify({
      url: page.url(), visibleNotices: await page.locator('.notice').allTextContents(), errors,
      oceanMode: await page.locator('.ocean').getAttribute('data-mode', { timeout: 1000 }).catch(() => null),
    });
    throw error;
  } finally {
    await context.close();
  }
}

async function follow(page, name, id, tap = false) {
  const link = page.getByRole('link', { name, exact: true });
  if (tap) await link.tap();
  else await link.press('Enter');
  await page.waitForFunction((section) => {
    const target = document.getElementById(section);
    return location.hash === '#' + section && document.activeElement === target
      && target.getBoundingClientRect().top >= 55 && target.getBoundingClientRect().top < innerHeight / 2;
  }, id);
  assert.equal(new URL(page.url()).pathname, '/');
}

const alternateBrowser = process.env.MACRO_BROWSER === 'webkit';
test('desktop anchors, original plot geometry and both research views', { skip: alternateBrowser }, async () => {
  await withPage({ viewport: { width: 1440, height: 960 } }, async (page, research, historyCount) => {
    assert.equal(await page.getByRole('heading', { level: 1 }).textContent(), 'Macro Analyst');
    await page.locator('.ocean[data-mode=webgl]').waitFor();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: resolve(output, 'desktop-hero.png') });
    await page.getByRole('button', { name: 'Pause motion' }).click();
    await page.locator('.ocean[data-mode=paused] .ocean-live canvas').waitFor();
    assert.equal(await page.locator('[data-motion=off]').count(), 1);
    await page.getByRole('button', { name: 'Resume motion' }).click();
    await page.locator('.ocean[data-mode=webgl]').waitFor();

    await follow(page, 'Explore Macro Data', 'data');
    const plot = page.getByRole('slider');
    const count = historyCount();
    await page.getByRole('button', { name: '7Y', exact: true }).click();
    assert.equal(historyCount(), count);
    const dots = plot.locator('circle');
    const readGeometry = () => dots.evaluateAll((marks) => marks.map((mark) =>
      [Number(mark.getAttribute('cx')), Number(mark.getAttribute('cy'))]));
    assert.equal(await page.locator('.macro-reveal').evaluate((node) => getComputedStyle(node).animationName), 'chart-reveal');
    const beforeReveal = await readGeometry();
    assert.equal(beforeReveal.length, dates.length);
    const friday = dates.indexOf('2026-06-05');
    const [x1, x2, x3] = beforeReveal.slice(friday, friday + 3).map(([x]) => x);
    assert.ok(Math.abs((x2 - x1) / (x3 - x2) - 3) < .000001);
    assert.ok(beforeReveal[values.indexOf(Math.min(...values))][1] > beforeReveal[values.indexOf(Math.max(...values))][1]);
    await page.waitForFunction(() => document.querySelector('.macro-reveal').getAnimations().every((a) => a.playState === 'finished'));
    assert.deepEqual(await readGeometry(), beforeReveal);

    await plot.focus();
    await plot.press('End');
    const tooltip = page.getByRole('tooltip');
    assert.match(await tooltip.innerText(), /2026-10-30.*1\.1900 USD per EUR/);
    await plot.press('Escape');
    assert.equal(await tooltip.count(), 0);
    await page.getByRole('button', { name: '1M', exact: true }).click();
    assert.equal(await dots.count(), 2);
    await page.getByRole('button', { name: '1Y', exact: true }).click();
    assert.equal(await page.getByLabel('Indicator', { exact: true }).locator('option').count(), 5);
    assert.equal(await page.locator('.chart-footer time').getAttribute('datetime'), '2026-10-31T12:00:00Z');

    await page.getByRole('link', { name: 'MacroPulse AI home' }).click();
    await follow(page, 'Ask LLM Model', 'ask');
    const form = page.getByRole('form', { name: 'Macro research question composer' });
    await form.getByRole('button', { name: 'Ukraine War' }).click();
    const question = 'How did the EUR/USD rate change?';
    await form.getByLabel('Research question').fill(question);
    const submit = form.getByRole('button', { name: 'Ask LLM Model' });
    await submit.click();
    await page.getByRole('link', { name: 'Source 1, ECB reference rate' }).waitFor();
    assert.deepEqual(research, [{ question, indicator_id: 'eur-usd', event_id: 'ukraine', start: '2022-02-01', end: '2024-02-29' }]);

    await page.getByRole('checkbox', { name: 'Use experimental Research v2' }).check();
    await submit.click();
    await page.getByText('A supply shock can raise the price level.').waitFor();
    assert.deepEqual(research[1], { ...research[0], research_version: 'v2' });
    await page.getByRole('button', { name: 'RAG-Based LLM Answer' }).click();
    await page.getByRole('heading', { name: 'Evidence-based analysis' }).waitFor();
    await page.getByRole('button', { name: 'Pure LLM Answer' }).click();
    await page.getByText('A supply shock can raise the price level.').waitFor();
    assert.equal(research.length, 2);
    for (const selector of ['main', '#data', '#ask', '.question-composer']) assert.equal(await page.locator(selector).count(), 1);
    assert.equal(await page.locator('iframe').count(), 0);
    await page.screenshot({ ...workspaceCapture, path: resolve(output, 'desktop-workspace.png') });
  });
});

test('iPhone viewport, touch and reduced motion', async () => {
  const device = devices['iPhone 13'];
  await withPage({ ...device, reducedMotion: 'reduce' }, async (page) => {
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.locator('.ocean[data-mode=static] .ocean-still canvas').count(), 1);
    assert.equal(await page.locator('.ocean-live canvas').count(), 0);
    assert.equal(await page.locator('.macro-reveal').evaluate((node) => getComputedStyle(node).animationName), 'none');
    assert.equal(await page.evaluate(() => performance.getEntriesByType('resource').some(({ name }) => /\/src\/ocean\.ts/.test(name))), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), device.viewport.width);
    const prefix = alternateBrowser ? 'webkit' : 'mobile';
    await page.screenshot({ path: resolve(output, prefix + '-hero.png') });
    await follow(page, 'Explore Macro Data', 'data', true);
    await page.getByLabel('Indicator', { exact: true }).selectOption('m3-stock');
    const plot = page.getByRole('slider', { name: /Eurozone M3/ });
    await plot.waitFor();
    const bounds = await plot.boundingBox();
    await plot.tap({ position: { x: bounds.width - 36, y: bounds.height / 2 } });
    assert.match(await page.getByRole('tooltip').innerText(), /17612461\.010 Millions of EUR/);
    await follow(page, 'Ask LLM Model', 'ask', true);
    assert.equal(await page.getByRole('form').count(), 1);
    await page.screenshot({ ...workspaceCapture, path: resolve(output, prefix + '-workspace.png') });
  });
});

test('WebGL loss switches to static ocean and preserves navigation', { skip: alternateBrowser }, async () => {
  await withPage({ viewport: { width: 1200, height: 900 } }, async (page) => {
    const canvas = page.locator('.ocean[data-mode=webgl] .ocean-live canvas');
    await canvas.waitFor();
    await canvas.evaluate((node) => node.getContext('webgl2').getExtension('WEBGL_lose_context').loseContext());
    await page.locator('.ocean[data-mode=static] .ocean-still canvas').waitFor();
    assert.equal(await page.locator('.ocean-live canvas').count(), 0);
    await follow(page, 'Explore Macro Data', 'data');
  });
});

