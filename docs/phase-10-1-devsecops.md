# Fase 10.1 — DevSecOps

This phase adds repository-level checks for MacroPulse AI without adding application
accounts, authorization roles, infrastructure changes, or automatic deployments.

## Checks on pull requests and `main`

| Workflow | Check | Scope |
| --- | --- | --- |
| [CI](../.github/workflows/ci.yml) | Backend tests | Python 3.12, pinned local AI + developer requirements, `compileall`, `pytest -ra` |
| CI | Frontend and Worker tests | Node 24, `npm ci`, Vitest, Worker tests, lint, local and public PWA builds |
| CI | Python dependency audit | `pip-audit` on public Render and local/developer requirements, including resolved dependencies |
| CI | npm dependency audit | `npm audit` for production and full lockfile; high/critical findings fail |
| [CodeQL](../.github/workflows/codeql.yml) | CodeQL (python) | Python security-extended queries |
| CodeQL | CodeQL (javascript-typescript) | JS/TS security-extended queries |
| [Secret scan](../.github/workflows/secrets.yml) | Gitleaks | Detect secrets in version-control changes; do not upload potential credentials as artifacts |

All workflows use read-only checkout. CodeQL has the narrowly scoped
`security-events: write` permission for findings. Actions are pinned to full
commit SHAs, and [Dependabot](../.github/dependabot.yml) checks GitHub Actions,
npm, and Python dependencies weekly.

**Expected behavior:** dependency audits are intentionally *blocking* when
high-risk findings are present (Python vulnerability findings at any severity,
and npm high/critical). Do not mark these checks as passing if they have not
run. Fix or explicitly triage a finding before making its check mandatory.
A scanner passing does not prove the absence of vulnerabilities.

## Important scope boundaries

- CI runs unit and mocked tests without external credentials, PostgreSQL,
  Supabase, Gemini, Cloudflare, or Render. Tests requiring disposable PostgreSQL
  are skipped when `MARKET_DATA_TEST_DSN` / `MACRO_DATA_TEST_DSN` are absent.
  This does **not** replace the separate PostgreSQL integration test suite.
- The files in this phase do not deploy or migrate anything. Render remains
  manually deployed and `autoDeployTrigger: 'off'`; Cloudflare is not published
  by these workflows.
- GitHub Actions and Gitleaks receive no application secrets. Never put the
  production Gemini key, Supabase credentials, or origin secret in CI.
- Gitleaks detects recognizable secrets, not all sensitive content. Any
  confirmed leaked credential must be **rotated**, even if its commit is
  subsequently removed. A clean PR scan is not a full historical attestation.
- CodeQL's results appear in Security > Code scanning after a successful run.
  Native Secret Scanning and Push Protection are GitHub account/repository
  settings, not enabled merely by committing Gitleaks.
- Dependabot proposes PRs; it does not auto-merge them or publish the site.

## One-time GitHub configuration (repository administrator)

After the first PR checks have completed and failures have been investigated:

1. In **Settings > Rules > Rulesets**, create an **active branch ruleset**
   targeting `main`. Require a pull request before merging (zero required
   approvals is sensible for a solo-maintained portfolio). Block force pushes
   and deletions; require successful status checks, and prefer up-to-date PR
   branches. Do not accidentally demand an independent reviewer who does
   not exist.
2. Add the passing check names from the PR: `Backend tests`,
   `Frontend and Worker tests`, `Python dependency audit`,
   `npm dependency audit`, `CodeQL (python)`,
   `CodeQL (javascript-typescript)`, and `Gitleaks`. GitHub may
   include workflow prefixes in the displayed check names; select the
   exact names shown under **Checks**. If a dependency audit fails due
   to an existing advisory, fix/triage it before enforcing it.
3. In **Settings > Advanced Security** (or **Security > Code security and
   analysis**, depending on the UI), enable native **Secret Scanning**,
   **Push Protection**, and **Dependabot security updates** if those controls
   are available to this repository. Avoid enabling a duplicate CodeQL
   default setup when using the committed advanced CodeQL workflow.
4. Confirm **Actions** can use the approved first-party actions and
   `gitleaks/gitleaks-action`. Keep workflow token permissions read-only by
   default; CodeQL alone needs `security-events: write`.
5. Check that `main` is shown as **protected** and that a test PR cannot
   merge while a required check is failing.

The GitHub connector does not expose repository ruleset writes or native
secret-scanning toggles. Those changes are manual and must not be described
as enabled until GitHub confirms them.

## Ongoing workflow

Open an ordinary feature branch and PR. Review test failures and scan results,
then merge after checks pass. Keep Render and Cloudflare releases manual;
record the deployed Git SHA and verify health and Research separately after
deployment. Dependabot PRs undergo the same CI. Rotate any leaked key rather
than suppressing an alert solely to make CI green.

## Local verification

```powershell
# Backend
.\.venv\Scripts\python.exe -m pytest -ra

# Frontend
cd frontend
npm ci
npm test
npm run test:worker
npm run lint
npm run build
npm run build:public
npm audit --audit-level=high
```

No passwords, API keys, test database DSNs, or cloud deployments are required
for the default CI checks.
