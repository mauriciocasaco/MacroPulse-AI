# MarketPulse Codex telemetry

The launcher writes local records under `.local/codex-telemetry/`. Git ignores
the entire directory. The collector does not persist prompts, replies, reasoning,
commands, file paths from Git diffs, transcripts or environment data. JSONL on
stdout is still the original CLI output and should be treated as private.
Telemetry adds no inference calls. It probes the CLI with `--version` and
reads baseline HEAD using local Git.

## Dashboard

From the repository root with Python 3.11 or later:

```powershell
python scripts/codex_telemetry.py runs
python scripts/codex_telemetry.py report
Invoke-Item .local/codex-telemetry/reports/dashboard.html
```

CSV files beside the dashboard cover cohorts, contributions, complexity, monthly
trends and tasks. For a narrower report:

```powershell
python scripts/codex_telemetry.py report --mode MEDIO --complexity moderate --start 2026-11-01 --end 2027-01-01 --maturity mature --window-days 14
python scripts/codex_telemetry.py report --scan-git
```

Dates filter initial task start. The end is exclusive. Date-only filters use UTC.
Use `--as-of` for historical adjudications. Live Git candidate scanning uses the
current local HEAD and is unavailable with `--as-of`.

## Group work and record evidence

The launcher prints the task and run UUIDs to stderr. Reuse the task UUID for
retries, corrections and hotfixes, even across modes. Use a new UUID for a
different objective. A commit link can identify a run or the task as a whole.
Multiple runs may link to the same commit.

```powershell
$taskId = '<task UUID from launcher stderr>'
$runId = '<run UUID that produced the reviewed deliverable>'
$commit = '<implementation commit SHA>'
$fix = '<possible corrective commit SHA>'

# Example for a future correction run
./scripts/Invoke-MarketPulseCodex.ps1 -Mode MEDIO -TaskId $taskId -Task 'Correct the documented regression using synthetic data.'

python scripts/codex_telemetry.py link --task-id $taskId --run-id $runId --commit $commit
python scripts/codex_telemetry.py tests --task-id $taskId --run-id $runId --status PASS --evidence-source command-exit --exit-code 0
# For a task with no executable behavior, use this instead of PASS.
python scripts/codex_telemetry.py tests --task-id $taskId --run-id $runId --status not-applicable --na-reason docs-only
# Record independently checked formal audit evidence where required.
python scripts/codex_telemetry.py audit --task-id $taskId --run-id $runId --status PASS --round 1 --score 8.50 --no-blockers
python scripts/codex_telemetry.py acceptance --task-id $taskId --run-id $runId --status accepted --criteria-met
# After the observation window, record the result of follow-up.
python scripts/codex_telemetry.py defect --task-id $taskId --status none-observed

python scripts/codex_telemetry.py candidates --task-id $taskId --ref HEAD
python scripts/codex_telemetry.py hotfix --task-id $taskId --commit $fix --status confirmed --detected-at 2026-11-06T12:00:00Z
# Use this instead if review shows that the candidate is unrelated.
python scripts/codex_telemetry.py hotfix --task-id $taskId --commit $fix --status unrelated
```

Use `--evidence-source machine-result` when you inspected a trusted test result
file. Use `command-exit` after confirming the required commands and exit code.
The command stores an attestation and fixed metadata only. It does not run tests
or independently check the artifacts. Use `acceptance --status rejected` or
`--status abandoned` to close unsuccessful work. Use `defect --status confirmed`
with a detection timestamp for confirmed defects without an associated commit.
The repair clock runs from detection to a confirmed repair commit or a subsequent
documented acceptance, whichever comes first after detection.

Git detection scans descendants of explicitly linked commits under the selected
local ref. A task or commit reference is a suspicion. Without such a reference,
a fix-like message, file overlap and a commit timestamp within 90 days are all
required. Only manual confirmation attributes a defect. An unrelated follow-up
can touch the same files. Suspicions do not revoke PASS. A confirmed late defect
revises the current historical outcome to FAIL once the cohort is mature.

## Accounting

- **Run** is one launcher invocation. Its cost is execution plus the separate
  AUTO preflight. Input includes cached input. Output includes reasoning.
  Uncached input is input minus cached input. The collector uses the first
  top-level completed-turn summary and never adds child usage. Missing,
  malformed, interrupted or ambiguous usage stays incomplete. Failed runs
  can expose partial usage, which is displayed separately from exact totals.
- **Usage coverage.** A completed SIMPLE turn with valid counters provides
  single-agent usage. MEDIO and ELABORADO remain incomplete until independent
  host evidence confirms that the reported execution counters cover the whole
  workflow. Only then record
  `python scripts/codex_telemetry.py coverage --task-id $taskId --run-id $runId --host-verified-aggregate`.
  This confirms coverage without adding counters. It cannot recover missing
  usage. When the host supplies no boundary evidence, leave the exact KPI
  UNKNOWN for affected cohorts.
- **Initially accepted** requires a completed run, criteria attestation,
  passing test evidence or justified non-applicability, and formal audit PASS
  when the implementation lineage used ELABORADO. Initial acceptance cost
  includes every run started through that acceptance. First-pass acceptance
  also requires one implementation attempt and zero prior audit rejections.
  The first-pass success percentage uses the same mature task denominator as
  the effective PASS rate. The average first-pass cost uses first-pass accepts.
- **Effective PASS** requires the latest accepted revision to have a mature
  observation window and an explicit no-defect follow-up at or after its end.
  A linked correction run calls for fresh acceptance evidence. Unaccepted
  tasks age from their latest run or decision. Explicitly pending tasks remain
  provisional. Mature rejected, abandoned and unknown tasks still contribute
  all attempt costs to the numerator. The denominator is verified effective
  passes only. If any eligible attempt lacks complete usage or records are
  unreadable, report observed spend and leave the exact KPI UNKNOWN.
- **Comparisons.** The first selected mode owns the task cohort, including
  preflight failures before selection. Contribution groups individual runs by
  selected mode and includes an UNKNOWN bucket for unselected runs. A mixed
  task belongs to one cohort. Complexity is derived from the initial AUTO
  ratings or set explicitly with `complexity --task-id $taskId --level moderate`.
  Forced runs otherwise have UNKNOWN complexity. Post-commit defect and hotfix
  rates use mature linked tasks with observed follow-up or confirmed defects.
  Treat observational averages as comparisons within the selected cohort.
- **Audit analytics** use manually corroborated verdicts per run and round.
  The collector does not parse agent text for tests, verdicts, model or effort.
  Spawn event counts and allowlisted role names are available when emitted by
  the host. They do not establish exact participation. A hard kill may leave
  a checkpoint with unknown completion and incomplete usage. Attempts that
  never wrote a checkpoint are absent.

## Storage schema v1

All times are ISO 8601 UTC unless a Git commit timestamp carries an offset.
Files are written as UTF-8 JSON and published by a same-directory atomic rename.

| Record | Location | Fields |
| --- | --- | --- |
| Run | `runs/<run UUID>.json` | `schema_version`, `run_id`, `task_id`, requested/selected modes, routing ratings and source, requested model/effort, nullable effective model/effort, expected agents, nullable CLI version and baseline HEAD, start/end timestamps, elapsed seconds, exit code, outcome, execution/classifier usage |
| Usage | Two objects within a run | `status` (not_started, observed, incomplete), turn count, invalid/failed flags, input/cached/output/reasoning integer counters, spawn event count and allowlisted role names |
| Evidence | `evidence/<event UUID>.json` | `schema_version`, `task_id`, `kind`, `at`, provenance, optional run UUID or commit SHA, and structured fields for that evidence kind |
| Reports | `reports/` | Regenerable static HTML and CSV |

Evidence kinds are `link`, `tests`, `audit`, `acceptance`, `defect`, `hotfix`,
`coverage` and `complexity`. Evidence files append independently, so concurrent
writers do not replace one another's decisions. The latest relevant adjudication
is used for a run, round or candidate. No historical session importer or
currency estimate is included.

## Offline verification

```powershell
python -m unittest discover -s tests -p test_codex_telemetry.py -v
pwsh -NoProfile -File tests/Test-CodexTelemetry.ps1
```

The PowerShell test requires Node.js and runs a temporary launcher copy wired
only to a local mock. The Python tests use synthetic records and a temporary
Git repository. Both remove their fixtures.
