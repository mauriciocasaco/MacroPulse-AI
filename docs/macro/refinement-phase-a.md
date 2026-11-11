# Refinement Phase A: UI and chart interaction

Baseline: `14d31b3e12a0401306eb6c5062e5fd2d65631779`, clean assigned worktree on `codex/refinement-phase-a`.

Implemented exact requested title and subtitle, removed MAX in favor of 7Y,
removed the observation count, capture timestamp, observations disclosure,
coverage prose and verbose indicator metadata beneath the chart. Official
source and reuse terms remain visible. Source observations, frequencies,
verified effective-rate segments and backend algorithms remain unchanged.
The chart tooltip preserves the raw decimal string and observation period;
its border and marker match the series color. Pointer hover and keyboard
arrows/Home/End select observations; Escape and blur dismiss the tooltip.
There is one keyboard entry point rather than one per data mark. Tooltip
positioning accounts for responsive SVG letterboxing and resizing.

Scope split: the coordinator owns MacroResearch heading cleanup, submit copy,
response rendering and composer wiring. Phase C owns English generated backend
summaries. Those files were intentionally not edited here.

## Verification

Working directory for npm commands:
`C:/Users/Usuario/.codex/worktrees/refinement-phase-a/MarketPulse-AI/frontend`.
Dependencies use an ignored junction to the existing checkout's node_modules.
No dependency versions changed, no .env files or live services accessed.

- `npm run test -- src/macro-chart.test.tsx src/MacroWorkspace.test.tsx`: exit 0;
  45 passed at initial focused run; additional regression added afterward.
- `npm run test`: exit 0; 100 passed, 0 failed, 0 skipped across 6 files.
- `npm run typecheck`: exit 0.
- `npm run lint`: exit 0, zero warnings. One documented lint exception allows
  SVG slider semantics because an HTML input cannot contain the SVG marks.
- `npm run build`: exit 0.
- `npm run build:public`: exit 0.
- `npm run test:worker`: exit 0; 6 passed, 0 skipped.
- `git diff --check`: exit 0.

All automated data are deterministic synthetic fixtures, including tests
through the actual Worker implementation with a mocked origin. These checks
are not live ECB, PostgreSQL or Gemini verification. The coordinator must
perform the required visual/real-public-data check and independent audit.
No audit score or verdict is asserted by this executor.

## Independent audit round 1 correction

The coordinator relayed the auditor's REJECT (8.575 before rounding) for a
blocking unit-magnitude defect: the tooltip displayed raw `EUR` although M3
observations use multiplier 6. The correction extracts the existing readable
unit mapping into `macro-chart.ts` and shares it between the chart overview,
tooltip, SVG title, point title and ARIA value text. The M3 decimal remains
`17612461.010`, paired with `Millions of EUR`; it is not rescaled or rounded.
The remaining supported units are USD per EUR, % per year, % quarter on
quarter and % year on year.

Five parameterized regressions verify exact decimal preservation and readable
units in tooltip and accessible descriptions for every supported indicator.
The workspace regression now asserts the M3 scaled unit explicitly.

Correction validation from the same frontend directory: `npm run test` exits
0 with 105 passed, 0 failed, 0 skipped across 6 files; `npm run typecheck`,
`npm run lint`, `npm run build`, `npm run build:public` and `git diff --check`
all exit 0. Builds are local and public-demo artifacts only, not deployments.
The changes are ready for independent round 2; no verdict is asserted here.
