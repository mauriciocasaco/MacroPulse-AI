# Fase 12.0 — Continuous Promotion

## Branches and human approval

- Feature and fix PRs target `dev`. The owner reviews and merges them.
- On a protected `dev` push, `.github/workflows/auto-promotion.yml` opens an
  idempotent `dev -> staging` PR with the repository-installed GitHub App.
- The trusted `.github/workflows/release-quality-gates.yml` runs independently
  of the candidate code's workflow configuration. Its **Release Quality Gate**
  checks the candidate merge without production credentials.
- Only after this gate passes does a separate, non-checkout job request GitHub
  native `--auto --merge`. GitHub enforces all required branch checks.
- Once `staging` merges, its protected-branch push opens an idempotent
  `staging -> prod` PR. A stronger `prod` gate repeats the process.
- Failing checks, version drift, merge conflicts or revoked credentials stop
  the chain. There is **no bypass** configured and no direct branch push.

## Test levels

| Target | Additional release checks (beyond required CI, CodeQL, Gitleaks, Promotion Policy) |
|---|---|
| dev | Offline 15-scenario Research matrix, deterministic security fallback |
| staging | dev + approved M7.C/M7.D, public API, fallback/security tests, frozen 28-case RAG gate benchmark with metrics |
| prod | staging + 32-case held-out evaluation, 17-case gate regression, explicit manual deployment configuration |

No external Gemini requests, live PostgreSQL connection, E5 model download, load
test or actual staging deployment is claimed by these gates. Those require a
separate environment/protected integration workflow before relying on them.

## GitHub settings

- Default branch: `dev`.
- Active ruleset: `dev`, `staging`, `prod`; require PR, 0 approvals,
  conversation resolution, no force pushes/deletes; allow merge commits; do
  not require linear history.
- Existing required checks: Backend tests, Frontend and Worker tests,
  Python dependency audit, npm dependency audit, CodeQL (python),
  CodeQL (javascript-typescript), Gitleaks, Promotion Policy.
- **Add `Release Quality Gate` to required checks when a successful
  check first appears in GitHub Actions.** This prevents an ordinary manual
  PR merge from bypassing the release gate.
- Repository: Allow auto-merge enabled.
- App: `macropulse-promotion-bot`, installed only on MacroPulse-AI,
  repository permissions Contents R/W and Pull requests R/W.
- Actions variable: `MACROPULSE_PROMOTION_CLIENT_ID`.
- Actions secret: `MACROPULSE_PROMOTION_APP_PRIVATE_KEY`.

GitHub requires signed commits only if explicitly configured; this project
currently relies instead on protected PRs, required CI and exact-pinned
actions. Review workflow changes to `.github/workflows/**` especially
carefully: after a human merges them into default `dev`, those workflows
become the trusted control plane. Do not grant any bypass to the bot.

## Security considerations

- `pull_request_target` uses the trusted default branch workflow. The
  untrusted candidate merge is checked out and executed **only** in a
  read-only testing job without application secrets. Its permissions are
  explicitly restricted to read access.
- The App private key appears **only** in a separate privileged job that
  never checks out the PR or executes scripts supplied by the PR.
- The privileged job rechecks source/destination, same repository, PR state,
  and original head **and base** SHAs immediately before requesting auto-merge.
- Use the GitHub App token for mutations, not the workflow's `GITHUB_TOKEN`,
  to ensure that subsequent merge/push events can trigger other workflows.
- Token grants are restricted to a single repository and to Contents/PRs.
- No UI users, accounts, login or profiles are introduced.
- A valid CI check is a regression guard, not evidence that all production
  security problems (including existing P1s) have been resolved.

## Deployment boundary

The `prod` merge means **release branch prepared**, not a public deployment.
Render `autoDeployTrigger: 'off'` remains configured, and Cloudflare
deployment is not invoked by these workflows. Inspect and authorize each
deployment separately. Verify the Render service dashboard is still linked to
the intended production branch before deploying.

## Operational checks

1. Review the first integration PR into `dev` and merge it manually.
2. The already-open `dev -> staging` PR must receive Release Quality Gate,
   Promotion Policy and all seven existing required checks.
3. On success the bot enables auto-merge; the PR merges to `staging`.
4. A `staging -> prod` PR appears without human interaction; tests run
   again with production holdout and config guards. On success it merges.
5. If a PR remains open, inspect its **Release Quality Gate** and standard
   required check runs before changing any protection.
6. If behavior needs to stop immediately, disable these Actions workflows or
   revoke the private key/repository App installation; do not disable all
   branch rulesets to get around a failure.

## Limits and future hardening

No queue guarantees release batching: a second PR merged into dev while
staging is being tested updates its promotion PR and requires a new
successful test run. Version synchronization is rechecked before auto-merge.

If absolute per-feature approval isolation is required, move from live branch
heads to immutable release SHA branches and a managed release queue. Configure
environment-specific strict status checks and protect default-branch workflow
modifications with independent CODEOWNERS review before expanding production
automation or enabling public auto-deploy.
