# UI and LLM refinement — integration evidence

Validation date: 2026-11-10 (America/Montevideo). Baseline: `14d31b3e12a0401306eb6c5062e5fd2d65631779`, clean `main`, confirmed equal to `origin/main` after fetch. The user explicitly authorized an exception to the single-agent default, independent worktrees, phase commits, integration, a no-fast-forward merge into main and push after passing gates.

## Participants and worktrees

Three independent executor agents implemented A, B and C. A and B ran concurrently; C and the independent auditor subsequently used the two subordinate slots. The coordinator remained the primary participant. Five distinct participants over the task lifetime, never more than three active simultaneously. A and B were reactivated/reviewed only as needed; no worktree had concurrent writers. No model overrides were passed. Effective model/effort metadata: unverifiable; no separate model use claimed.

| Artifact | Worktree under `C:/Users/Usuario/.codex/worktrees/` | Branch | Final phase commit | Independent gate |
| --- | --- | --- | --- | --- |
| A | `refinement-phase-a/MarketPulse-AI` | `codex/refinement-phase-a` | `c4566ea1eccc3e5d8ce35521359e8cfb8666d824` | PASS 9.35, round 2/3 |
| B | `refinement-phase-b/MarketPulse-AI` | `codex/refinement-phase-b` | `6e6b8057926a14edc533d71f26630e7f454ce7a0` | PASS 9.35, round 1/3 |
| C | `refinement-phase-c/MarketPulse-AI` | `codex/refinement-phase-c` | `760f4dfe1b2215c6a4993d7cd32af9890c239871` | PASS 9.10, round 1/3 |

Integration uses the existing checkout at `C:/Users/Usuario/Documents/MarketPulse-AI` on `codex/refinement-integration`. Merge order: A (`db86e834b0030bc605b5f87bd621ca5458088a34`), B (`63164d9561483161c7660e4fb7e786342d3c4cd6`), C (`e96cbd61002e6f2acb77376308f863eed3ae4c9a`). Original worktrees `adf0`, `c940`, `d2fc` and `dfb0` were inspected and left untouched, including their unfinished changes.

A initially received REJECT 8.575 (display 8.58) for a P1 tooltip unit error: M3 ignored its millions multiplier. The same executor corrected visible/accessible units and added five regressions; the same independent auditor then passed round 2. B and C have no unresolved HIGH/CRITICAL/P0/P1 findings in their artifact verdicts.

## Integrated behavior

- Exact English title/subtitle, English controls and backend output; 7Y maximum preset; observation table/capture/verbose indicator metadata removed; source and reuse links retained.
- Exact raw-decimal chart tooltip uses readable scaled units and series color. Pointer, touch semantics and keyboard arrows/Home/End/Escape supported; original frequencies and verified effective-rate segments preserved.
- Reusable composer controls the same indicator as the chart, generates editable questions and sends explicit indicator/event plus the event window. Custom edits survive chart period/context changes with a review notice. Pending requests abort when chart indicator or enabled state changes; stale results cannot reappear when returning to an indicator.
- Event windows: COVID 2020-02-01..2020-12-31, Ukraine 2022-02-01..2024-02-29, Generative AI 2023-09-01..2026-09-30. Chart display period does not silently replace the event window.
- A single narrative is headed LLM Model, followed by RAW sources. Document order preserves descending actual retrieval relevance; only genuine cosine scores are shown, rounded to three decimals. Citation numbers/anchors remain aligned, and series provenance and reuse URLs remain available. Legacy response fields remain accepted without rigid Facts/Calculations headings.
- Backend calculation, extraction/grounding and API compatibility details are in `refinement-phase-c.md`.

## Final runtime checks

All commands used the original checkout; no local .env was read by the agents and no production account/database was used.

| Check | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q -rs` | 517 passed, 81 skipped, 0 failed |
| `npm test -- --reporter=dot` in frontend | 138 passed across 8 files; 0 skipped |
| `npm run test:worker` | 6 passed; 0 skipped |
| `npm run typecheck` | passed |
| `npm run lint` | passed, zero warnings |
| `npm run build` | passed |
| `npm run build:public` | passed |
| `node scripts/refinement-ui-check.mjs ...` | 16 browser checks passed in headless Microsoft Edge |
| `git diff --check` | passed |

The three C-worktree TLS fixture failures do not reproduce in the integration checkout: the complete backend suite passes here. The 81 skips require unset disposable PostgreSQL DSNs and remain unvalidated integrations. Mocked Gemini calls are not live Gemini verification.

The browser service initially timed out; verification used a temporary Playwright 1.56.1 installation and the existing Edge executable instead. No application dependency or global Codex configuration was changed. The harness is committed at `scripts/refinement-ui-check.mjs`. With a loopback Vite server started using `MARKETPULSE_MACRO_AI_ENABLED=true`, reproduce deterministic checks with:

```text
node scripts/refinement-ui-check.mjs --module <temporary-playwright-install>/node_modules/playwright/index.mjs --url http://127.0.0.1:5194
```

The optional `--history <public-snapshot.json>` replays captured public observations without changing their values. Default fixtures, all analysis responses and the LLM in browser checks are synthetic. Browser checks cover title, 7Y/no MAX, metadata removal, pointer/keyboard exact values, attribution, bidirectional indicator sync, editable questions/all three event windows, single submit, narrative presentation, empty-evidence abstention/no invented scores, sanitized errors, mobile width/scaled M3 units, and no runtime exceptions.

## Official-data and visual evidence

A separate public HTTPS GET through the existing bounded ECB transport/adapter returned 235 EUR/USD observations, February through December 2020, HTTP 200. Source:
`https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?startPeriod=2020-02-01&endPeriod=2020-12-31&format=csvdata`.
Capture time: 2026-11-11T00:21:10Z (2026-11-10 local); CSV SHA-256: `0af1f7b1d87dc525c116f64a45565f6327d97781a221ae60d8ce101d40ca90d4`.

The browser replay verified the real observed value `1.2271 USD per EUR` at `2020-12-31` with pointer and keyboard. ECB source/reuse links remained visible. This verifies the chart with official observations, not a live database-to-LLM integration. The snapshot retains original source decimals and metadata; the raw data are ignored local validation artifacts, not committed datasets.

Coordinator visually inspected desktop 1440px and mobile 390px full-page screenshots. The exact official-data tooltip, mobile M3 scale, controls, narrative, RAW citations and score display fit without horizontal overflow. Screenshots and browser JSON report are retained locally under `.local/codex-telemetry/refinement-validation/`; official chart, synthetic response-flow, and synthetic mobile fixtures are named separately.

## Audit freeze and limits

For every phase review, the coordinator captured root/branch/HEAD, exact `git status --porcelain=v1 -uall`, complete tracked path inventory, changed/addition inventories and SHA-256 of all changed phase files in memory before and after. All snapshots were identical. Secret/private/ignored files were excluded from content hashing and diffs; untracked names were inventoried. The auditor performed read-only inspection and no tests/services/writes. Host-enforced read-only isolation is not certified; the host exposes unrestricted filesystem access with approval policy never. The checks establish local review behavior, not sandbox isolation.

Final integration independent audit: pending at candidate freeze. No main merge/push occurs until that verdict passes. Its report and final Git identities will be recorded afterward.

Residual limits: no live Gemini/PostgreSQL validation; current captured statistical vintages are explicitly distinguished from historical publication vintages; reviewed corpus has no Generative AI documents and some event/indicator combinations must abstain. Model prose is constrained extractive generation with citation-local statement validation, not unrestricted paraphrase or a general semantic truth detector. The configured Gemini identifier does not prove the provider's resolved revision. No deployment or production migration was performed.
