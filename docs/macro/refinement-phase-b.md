# Phase B: interactive question composer

## Scope and baseline

- Worktree: `C:/Users/Usuario/.codex/worktrees/refinement-phase-b/MarketPulse-AI`.
- Branch: `codex/refinement-phase-b`.
- Baseline: `14d31b3e12a0401306eb6c5062e5fd2d65631779`; clean before edits.
- Added only `QuestionComposer.tsx`, its isolated stylesheet and tests, `question-events.ts`, and this evidence document.
- Existing worktrees, backend, chart, shared styles and research renderer were untouched.
- User authorized the custom multi-agent workflow and phase branch commits. No merge or push performed by the executor.

## Integration contract

`QuestionComposer` accepts `indicators: MacroIndicator[]`, controlled `indicatorId: string`,
`onIndicatorChange(id: string)`, `pending: boolean`, `enabled: boolean`, and
`onSubmit(submission: QuestionSubmission)`. It exports the submission and event-ID types.
The parent must update the chart indicator using the same controlled selection.

Submissions contain `{ question, indicatorId, eventId, start, end }`. Event IDs and complete inclusive windows:

| ID | Label | Start | End |
| --- | --- | --- | --- |
| `covid` | COVID-19 | 2020-02-01 | 2020-12-31 |
| `ukraine` | Ukraine War | 2022-02-01 | 2024-02-29 |
| `generative-ai` | Generative AI Boom | 2023-09-01 | 2026-09-30 |

The initial event is COVID-19. Generated questions use the existing supported historical
question grammar. Selection never submits a request. A single submit button reads
“Ask LLM Model”. Unknown controlled indicators disable submission rather than selecting
a fallback. Pending/disabled forms are inert, including programmatic form submission.

Custom text survives indicator/event changes; a context-review message makes that
preservation explicit. “Use generated question” restores the current template.
The parent/backend must enforce the explicit selected-series contract when custom text
mentions a conflicting series; the component always submits the controlled chart ID.
The parent owns request cancellation and result reset on context changes.

Native buttons, fieldset legends, pressed-state semantics, unique textarea labels,
context descriptions and visible focus styles support keyboard/screen-reader use.
Choices wrap and the layout adjusts below 480px. Browser visual and end-to-end checks
of the integrated component remain the coordinator's responsibility.

## Verification evidence

Commands ran in `C:/Users/Usuario/.codex/worktrees/refinement-phase-b/MarketPulse-AI/frontend`:

| Command | Exit | Result |
| --- | --- | --- |
| `npm run test -- src/QuestionComposer.test.tsx` | 0 | 29 passed; 0 skipped |
| `npm run typecheck` | 0 | TypeScript checks passed |
| `npm run lint` | 0 | No warnings/errors |
| `npm run build` | 0 | Production build completed |
| `npm run test` | 0 | 127 passed across 7 files; 0 skipped |

The first verification run identified two mock-typing errors and two lint warnings;
these were corrected before the passing commands above. Tests use synthetic catalog
fixtures and no network calls, economic data services, database, or LLM. They cover
all 15 indicator/event templates, exact event windows (including leap-day February
2024), custom edits, external indicator updates, no silent fallback, disabled/pending
submission guards, invalid input and native focus/label semantics.

The build here is a baseline compatibility check; the parent integrates the component
into the application and validates its final bundled/rendered behavior. No live Gemini,
PostgreSQL, real-data tooltip or integrated browser validation is claimed by Phase B.
Dependencies use an ignored junction to the existing frontend `node_modules`; no
lockfile or package changes. Model/effort effective metadata is not verifiable here.
Independent audit and scoring remain pending; this executor does not self-audit.
