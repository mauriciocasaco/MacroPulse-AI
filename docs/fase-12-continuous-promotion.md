# Fase 12.0 — Automatic PR Creation, Manual Promotions

## Contract

**Only PR creation is automatic. Every merge requires a deliberate human action.**

1. A feature/fix PR targets `dev`. A human reviews and merges it after the
   required checks pass.
2. A push to protected `dev` triggers `.github/workflows/auto-promotion.yml`,
   which uses `macropulse-promotion-bot` to open an idempotent `dev -> staging`
   PR (or leaves an already-open PR alone).
3. CI, CodeQL, secret scanning, Promotion Policy and Release Quality Gate run.
   **Passing checks does not merge the PR.** A human reviews and merges it.
4. That push to protected `staging` triggers the same workflow, opening
   an idempotent `staging -> prod` PR (or leaving an existing one alone).
5. The stricter production quality gates execute. **A human must explicitly
   merge `staging -> prod`** after reviewing results.
6. `prod` branch promotion does not deploy Render or Cloudflare.

There is no `gh pr merge`, GitHub API merge, auto-merge enablement, bypass
or source-branch direct push in these promotion workflows. The bot only
creates the next PR; it never approves or merges one.

## Quality levels

| PR target | Additional checks beyond CI, CodeQL, Gitleaks and Promotion Policy |
|---|---|
| dev | Offline Research matrix, defensive fallback checks |
| staging | dev + curated RAG gate, public API/security tests, frozen 28-case RAG benchmark |
| prod | staging + 32-case holdout, 17-case evidence holdout, manual deployment config guard |

The `Release Quality Gate` workflow runs on the standard `pull_request`
event and tests the proposed PR merge with a read-only token and no application
secrets. This avoids the default `pull_request_target` restriction for public
repositories. It is not evidence of live Gemini requests, PostgreSQL integration,
model download, load testing, or an actual staging deployment.

Add `Release Quality Gate` to required branch checks once a successful run
is available. Without that ruleset requirement, a person may merge while the
additional release tests are pending.

## Settings and secrets

- Default branch: `dev`.
- Active ruleset protects `dev`, `staging` and `prod`.
- Require Pull Requests, conversation resolution, required checks;
  block force pushes and deletions; allow merge commits.
- Existing checks: Backend tests, Frontend and Worker tests,
  Python dependency audit, npm dependency audit, CodeQL (python),
  CodeQL (javascript-typescript), Gitleaks, Promotion Policy.
- The repository's **Allow auto-merge** setting should be switched **OFF**
  if the owner wants to prevent anyone from enabling it manually on a PR.
  The promotion workflows themselves never request auto-merge.
- The installed app `macropulse-promotion-bot` operates only in this repo.
  It uses Actions variable `MACROPULSE_PROMOTION_CLIENT_ID` and Actions
  secret `MACROPULSE_PROMOTION_APP_PRIVATE_KEY`. Its workflow asks for
  a short-lived token with `contents: read`, `pull-requests: write`.
- Do not grant the bot bypass privileges on the branch rulesets.

## Security and review

- `pull_request` has read-only permissions. It executes candidate merge tests
  only in a job without production secrets. No privileged event is needed.
- Unlike the prior prototype, it contains **no privileged App-token merge
  job**. The only use of the private key is creating a follow-up PR from
  an already-protected branch push.
- Every promotion PR remains open after all checks pass until manually
  merged. Conflicts, test failures or branch drift require human action.
- PRs updating `.github/workflows/**` deserve particular review; they
  alter the trusted CI control plane once merged into `dev`.
- No user login, registration, accounts or profiles are introduced.

## Deployment boundary

`prod` means a release candidate was manually approved through all
three branches. It is **not** a public deployment. Render currently has
`autoDeployTrigger: 'off'` in `render.yaml`; Cloudflare deployment is
not invoked in these workflows. Verify actual Render dashboard branch
binding and deployment settings independently before future publication.

## Operations

1. Manually merge a reviewed feature PR into `dev`.
2. Wait for the bot PR `dev -> staging`. Check all required statuses
   and **manually merge** when acceptable.
3. Wait for the bot PR `staging -> prod`. Review stronger statuses and
   **manually merge** when acceptable.
4. To pause PR creation, disable `Continuous Promotion PR` in Actions.
   Do not disable the branch rulesets merely to force through a failing PR.
5. New changes on `dev` while a `dev -> staging` PR is open will update
   its head; review the full updated diff and rerun required checks.

For immutable per-release snapshots and stronger isolation, a future version
can replace live branch heads with release SHA branches and a promotion queue.

## Historical branch reconciliation (Fase 12)

The first `staging` promotion (#21) was merged while later release-control
changes (#22 and #24) were being integrated independently into `dev`.
This caused four add/add conflicts on the next `dev -> staging` PR (#25).
We reconciled the histories in a dedicated reviewed feature PR into `dev`,
with a merge commit that preserves `staging` ancestry but **selects the
current `dev` versions** of all conflicting release workflows and docs.
In particular, `Release Quality Gates` uses the unprivileged `pull_request`
event, not `pull_request_target`, and no promotion job auto-merges.
All subsequent promotions still require PRs, required checks and a deliberate
human merge; do not directly push either protected branch.

## Historical production reconciliation (Fase 12)

PR #27 revealed another add/add conflict: the first production promotion
(#23) had merged the older release workflows independently of the later
`dev` and `staging` corrections. The history-reconciliation commit for
this issue records the existing `prod` tip as a second Git parent while
keeping the newer `dev` tree. The correction must travel through the
normal, reviewed `feature -> dev -> staging -> prod` pipeline. In particular,
keep the standard `pull_request` release checks and manual PR merges.

The final history alignment additionally records the latest `staging` merge
(#25) as a parent of `dev` (after recording the earlier `prod` merge).
This avoids a second divergent-merge conflict in intermediate promotion
PR #29 without changing any release workflow or RAG implementation.
