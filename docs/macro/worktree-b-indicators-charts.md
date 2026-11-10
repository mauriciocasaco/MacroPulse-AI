# Worktree B — Indicators, periods and charts

Local implementation, 2026-11-10. Role B only. One primary participant;
no explicit execution mode was selected (MEDIO was a recommendation).
Modelo/esfuerzo efectivos: no verificables. No independent audit was performed.

## Baseline and destination

- Worktree: `C:/Users/Usuario/.codex/worktrees/c940/MarketPulse-AI`.
- Initial and final HEAD: `1829826fdfcbd06023840c50ccd6c0b6adc34d13`.
- Detached HEAD, existing checkout, initially clean. The main checkout's local
  `codex/macropulse-migration` ref points to the same baseline; no branch changes.
- Baseline contains the M6.1, M6.2A and M6.3 reports and accumulated code through
  `478cd85` (M6.3), followed by `1829826` (manual-mode instructions).
- No staging, commits, push, merge, deployment, remote migration, database
  access, RLS changes, Gemini invocation or independent audit.

## Files to integrate

Modified:

- `frontend/src/MacroWorkspace.tsx`: friendly selector/heading/table labels,
  selected period state, history loading, source metadata and research window wiring.
- `frontend/src/MacroWorkspace.test.tsx`: retained regressions, realistic bounded
  API fixtures, five names/colors, period persistence, retries and MAX interaction.
- `frontend/src/styles.css`: macro presets, selected state, colored data marks,
  neutral outlines and removal of obsolete macro date-form rules.

New (untracked, must be included when transporting this work):

- `frontend/src/macro-chart.ts`: reusable presentation mapping, calendar windows,
  bounded historical reads, observation selection and verified BCE segments.
- `frontend/src/MacroPlot.tsx`: time-proportional SVG lines, exact indicator
  colors, frequency-aware endpoint labels and actual-observation tooltips.
- `frontend/src/macro-chart.test.tsx`: synthetic chart and retrieval fixtures.
- `docs/macro/worktree-b-indicators-charts.md`: this handoff.

## Behavior

| Internal ID | UI label | Data color |
| --- | --- | --- |
| eur-usd | EUR/USD rate | #16A34A |
| m3-stock | Eurozone M3 | #2563EB |
| deposit-rate | ECB rate | #EA580C |
| real-gdp-qoq | Eurozone GDP | #C58B13 |
| hicp-yoy | Eurozone Inflation | #9333EA |

Original institutional titles remain in source metadata. IDs, units, scale,
frequencies, source/reuse URLs, attribution and API validation are preserved.
The friendly ECB label still denotes the deposit facility rate only.

The default is 1Y. Buttons offer 1M, 3M, 6M, 1Y, 3Y and MAX, expose their pressed
state and preserve selection when switching indicators. Finite presets use
calendar subtraction with month-end/leap-day clamping, anchored to the most
recent numeric observation. For effective rates, the latest verified applicability
boundary also counts as available information. Switching finite presets filters
already loaded data; no application reload or analysis request occurs.

History requests retain the existing 3,660-day cap. A recent chunk normally
covers all finite presets; stale series trigger additional older reads sufficient
for 3Y. MAX reads disjoint chunks back to 1900-01-01, a retrieval floor predating
all five pinned euro-area series, without changing the API or Worker. At the
current date MAX makes 13 sequential history requests. No observation is created,
forward-filled, sampled or rounded in the data table. The UI plots and tabulates
only the selected actual observations; a preceding BCE event may be shown as an
explicit prior event.

Daily/monthly/quarterly lines connect successive valid observations while keeping
true elapsed-time spacing. Monthly labels use month/year, quarterly labels use
quarter/year, and daily/effective-date labels include the day. Plot domains use
represented readings and verified segments, not an artificially wide query window.
A single observation stays a single centered point. Missing/non-numeric points
are never synthesized. BCE horizontal holds and vertical changes are bounded
by each response's verified coverage, and break at null events/unverified gaps.
Only real effective events receive point markers and tooltips.

Texts and titles remain neutral. The exact GDP gold has approximately 2.97:1
contrast against white, so a neutral outline provides a distinguishable edge
without changing the prescribed color. Contrast fixtures check light and dark
surfaces. Existing global theme behavior is preserved; human visual validation
on devices remains pending.

## Research boundary and integration conflicts

`MacroWorkspace.tsx` is shared with C and potentially A. Integrate the selector,
period state and source block carefully. C can reuse `indicatorPresentation` to
produce the exact friendly labels; this work does not change `MacroResearch.tsx`,
LangGraph, intent handling, evidence, citations or backend contracts.

MAX can span more than the existing research API allows. `researchWindow` keeps
that request within 3,660 days and the workspace explicitly discloses its narrower
range when enabled. C should preserve this bound or provide an explicit research
period control. MAX history is assembled from separate API reads; it does not
claim an atomic database snapshot. Capture timestamps remain visible.

The macro-only CSS additions/removals may overlap other styling work. Merge them
by selector. No legacy routing/navigation, App, backend, Worker, API types or
institutional metadata was changed. This work does not resolve or reassess the
previous HIGH institutional-attribution finding; that requires targeted evidence
from C/integration. No claim of independent security validation is made.

## Verification

All fixtures are synthetic and fetch is mocked; no application API or live
institutional/AI/database service was contacted during verification.

- `npm ci --ignore-scripts --no-audit --no-fund`: local dependencies installed;
  package manifests and lockfile unchanged.
- `npm test -- --reporter=dot`: **81 passed**, 6 files, including existing app,
  research and PWA route tests. Coverage includes all five names/colors, six
  presets, preserved selection, MAX historical retrieval, calendar boundaries,
  time-proportional weekends, monthly/quarterly sparsity, BCE steps/nulls/coverage,
  actual point counts, exact decimals, sources, bounded API requests, stale data,
  aborts and retries. No Gemini calls.
- `npm run typecheck`: PASS (also run by both builds).
- `npm run lint`: PASS with no warnings.
- `npm run build`: PASS, application/PWA assets generated locally.
- `npm run build:public`: PASS, public-demo/PWA assets generated locally.
- `git diff --check`: PASS.

Git delivery state: three modified tracked frontend files and four new untracked
files listed above; all remain uncommitted. Generated dependencies/build assets
are ignored. Integrator must include new files, reconcile shared components with
A/C, run integrated backend/frontend/Worker checks and perform human visual
validation before publication. No publication is authorized by this work.

WORKTREE B: PASS

FRIENDLY LABELS: 5/5

CHART PRESETS: WORKING

CHART COLORS: 5/5
