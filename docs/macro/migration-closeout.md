# MacroPulse migration closeout

As of 2026-11-10. Authorized scope: commit on `codex/macropulse-migration`,
merge with `--no-ff` into `main`, and push `main` to `origin` after validation.
Production deployment and production database migrations are excluded.
One participant; effective model/effort unavailable; no independent audit in
this closeout. Host permissions and approval policy were unchanged.

## Git baseline and preservation

- Migration checkout was clean at `5232eab4ba43c005c213f0d59d7a868db0f455da`
  (`feat: unify MacroPulse UX and integrate event-based RAG`). A/B/C were already
  committed and published on the migration branch; there were no pending
  implementation files to recommit.
- After fetching origin, local and remote `main` both pointed to
  `06fcad69370fe60de9121e9e19554819b9282a00`. Main had no exclusive changes.
- Original A (`d2fc`), B (`c940`), C (`adf0`) worktrees retain their uncommitted
  deliveries at detached baseline `1829826`. They are not merged, switched,
  cleaned, staged or deleted. The additional `dfb0` worktree was clean and left
  untouched. Status and SHA-256 delivery-file hashes were captured for A/B/C
  to compare before pushing. This covers listed changed/untracked delivery
  files, not ignored files or private contents.
- This closeout commit adds this report and replaces eight trailing-space
  Markdown hard breaks with equivalent backslash hard breaks in historical
  audit reports. Historical findings and verdicts are unchanged.
- A pattern scan of 66 added/modified migration files found no private keys,
  Google/GitHub/AWS token patterns, JWTs, credential URLs or real `.env` files.
  This is a bounded heuristic scan, not a guarantee against every secret form.
  No local `.env` was read and no credentials were added.

## Repeated validation on the migration branch

| Check | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q -rs --tb=short` | 492 passed, 81 skipped |
| `npm test -- --reporter=dot` | 98 passed, 6 files |
| `npm run test:worker` | 6 passed |
| `npm run lint` | passed, warnings denied |
| `npm run typecheck` | passed |
| `npm run build` | passed, frontend/PWA |
| `npm run build:public` | passed, public-demo frontend/PWA |

Commands use the existing dependencies (Python 3.12.0, Node 24.20.0). Backend
tests run with `MARKETPULSE_DISABLE_LOCAL_DOTENV=1`, `MARKETPULSE_MODE=local` and
connection/provider credential variables removed from the test subprocess.
The 81 skipped database tests require `MARKET_API_TEST_DSN`,
`MACRO_DATA_TEST_DSN`, `MARKET_DATA_TEST_DSN` or `MARKETPULSE_NEWS_LOCAL_DSN` for
disposable local PostgreSQL databases. They are not passing integration tests.
No separate Python lint/build command is defined by the repository; backend
verification uses pytest. No live providers or production services were tested.

## A/B/C and retired routes

The regression suite verifies MacroPulse-only entry points, including old
`?workspace=market` links; five indicator names/colors and six chart presets;
original units, decimals, gaps and bounded ECB rate steps; all fifteen
indicator/event prompt combinations; editing a question and sending only with
Ask; and institutional evidence/abstention behavior. Chart tests traverse the
actual Worker with a synthetic upstream for all five indicators and six
presets. Research responses and provider data in these tests are simulated.

Local-read allowlist and Worker tests reject retired `/api/assets`, asset
history and `/api/analysis` routes without upstream forwarding. Public macro
reads remain gated and public AI remains disabled. Internal legacy backend
endpoints remain in the repository; the retired browser/proxy routes do not
expose them. The migration does not claim deletion of all legacy backend code.

## Main gate and public-release limits

The complete checks above must be repeated on merged `main`, along with
`git diff --check` against the original main baseline, clean status, ancestry,
remote synchronization and A/B/C preservation checks before push. Actual merge
hash, post-merge check results and final remote hash are reported in the
closeout chat; this pre-merge report does not assert a future push succeeded.

Before public deployment, validate disposable PostgreSQL integration, the
complete migrated flow with real Gemini/Supabase, human visual/responsive/PWA
behavior, and target-environment configuration. Earlier live M6.3 evidence is
historical and does not validate this complete A/B/C revision. Corpus coverage
and abstentions, revised statistical vintages, non-atomic MAX captures, request
quotas and the shorter RAG window remain as documented in
`final-integration.md`. No public deployment was performed or authorized.
