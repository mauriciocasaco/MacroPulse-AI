"""Local, content-free Codex task telemetry and reports."""
import argparse
import csv
import html
import json
import os
import re
import statistics
import subprocess
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / ".local" / "codex-telemetry"
MODES = ("SIMPLE", "MEDIO", "ELABORADO", "UNKNOWN")
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
NA_REASONS = ("docs-only", "read-only", "no-executable-behavior", "covered-by-other-verification")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def instant(value):
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def identifier(value):
    return str(uuid.UUID(value))


def atomic(path, write):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            write(stream)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load(store):
    """Skip corrupt records visibly. Reports with ingestion gaps suppress the KPI."""
    records = []
    errors = 0
    for directory in ("runs", "evidence"):
        result = []
        for path in (store / directory).glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item["schema_version"] != 1:
                    raise ValueError("unsupported schema")
                identifier(item["task_id"])
                if directory == "runs":
                    identifier(item["run_id"])
                    if path.stem != item["run_id"] or item["selected_mode"] not in (*MODES[:-1], None):
                        raise ValueError("invalid run identity or mode")
                    instant(item["started_at"])
                    if item["ended_at"]:
                        instant(item["ended_at"])
                    if item["outcome"] not in ("completed", "failed", "interrupted", "unknown"):
                        raise ValueError("invalid outcome")
                    for field in ("execution_usage", "classifier_usage"):
                        usage = item[field]
                        if usage["status"] not in ("not_started", "observed", "incomplete"):
                            raise ValueError("invalid usage status")
                        if any(type(usage[key]) is not int or usage[key] < 0 for key in TOKEN_FIELDS):
                            raise ValueError("invalid usage counters")
                        if (usage["cached_input_tokens"] > usage["input_tokens"] or
                                usage["reasoning_output_tokens"] > usage["output_tokens"]):
                            raise ValueError("invalid token subsets")
                else:
                    instant(item["at"])
                    if item["kind"] not in EVIDENCE_KINDS:
                        raise ValueError("unknown evidence kind")
                result.append(item)
            except (KeyError, ValueError, AttributeError, TypeError, OSError):
                errors += 1
        records.append(result)
    return *records, errors


def append_evidence(store, task_id, kind, **fields):
    event = dict(schema_version=1, task_id=task_id, kind=kind,
                 at=datetime.now(timezone.utc).isoformat(), **fields)
    atomic(store / "evidence" / f"{uuid.uuid4()}.json", lambda stream: json.dump(event, stream))


def event_key(event):
    return instant(event["at"])


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def resolve_commit(repo, value):
    sha = git(repo, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}").strip()
    if not COMMIT.fullmatch(sha):
        raise ValueError("Git did not return a full commit SHA")
    return sha


def git_candidates(repo, task_id, events, ref="HEAD"):
    """Read messages and paths transiently. Return SHAs and fixed reason labels."""
    tip = resolve_commit(repo, ref)
    original = {e["commit"] for e in events if e["kind"] == "link"}
    dispositions = {e["commit"]: e["status"] for e in events if e["kind"] == "hotfix"}
    matches = {}
    for sha in sorted(original):
        origin_time = instant(git(repo, "show", "-s", "--format=%cI", sha).strip())
        paths = set(git(repo, "diff-tree", "--root", "-m", "--no-commit-id", "--name-only",
                        "-r", "-z", sha).split("\0")) - {""}
        for candidate in git(repo, "rev-list", "--ancestry-path", f"{sha}..{tip}").splitlines():
            if candidate in original:
                continue
            stamp, message = git(repo, "show", "-s", "--format=%cI%n%B", candidate).split("\n", 1)
            if candidate in dispositions:
                matches[candidate] = (dispositions[candidate], "manual-adjudication")
                continue
            reference = re.search(rf"(?<![a-z0-9-])(?:{re.escape(task_id)}|{sha}|{sha[:12]}|{sha[:7]})(?![a-z0-9-])", message, re.I)
            if reference:
                matches[candidate] = ("suspected", "explicit-reference")
                continue
            if candidate in matches or not timedelta(0) <= instant(stamp) - origin_time <= timedelta(days=90):
                continue
            if re.search(r"\b(?:fix(?:es|ed)?|hotfix|regression|bugfix)\b", message, re.I):
                changed = set(git(repo, "diff-tree", "-m", "--no-commit-id", "--name-only",
                                  "-r", "-z", candidate).split("\0")) - {""}
                if paths & changed:
                    matches[candidate] = ("suspected", "fix-message+overlap+90-days")
    return matches


def ratio(numerator, denominator):
    return round(numerator / denominator, 2) if numerator is not None and denominator else None


def count_tokens(usage):
    return usage["input_tokens"] + usage["output_tokens"]


def run_cost(run, coverage):
    execution, classifier = run["execution_usage"], run["classifier_usage"]
    complete = all(item["status"] in ("observed", "not_started") for item in (execution, classifier))
    if execution["status"] != "not_started" and run["selected_mode"] != "SIMPLE":
        complete &= run["run_id"] in coverage
    return count_tokens(execution) + count_tokens(classifier), complete


def sum_cost(runs, coverage):
    values = [run_cost(run, coverage) for run in runs]
    return sum(value for value, _ in values), sum(not complete for _, complete in values)


def complexity(run, events):
    labels = [e["level"] for e in events if e["kind"] == "complexity"]
    if labels:
        return labels[-1]
    ratings = run.get("ratings")
    if ratings:
        return {"SIMPLE": "low", "MEDIO": "moderate", "ELABORADO": "high"}.get(run["selected_mode"], "UNKNOWN")
    return "UNKNOWN"


def task_view(runs, events, now, window, coverage):
    runs = sorted(runs, key=lambda run: (instant(run["started_at"]), run["run_id"]))
    initial = next((run for run in runs if run["selected_mode"]), runs[0])
    started = instant(runs[0]["started_at"])
    last_activity = max(instant(run["ended_at"] or run["started_at"]) for run in runs)
    events = sorted((event for event in events if event_key(event) <= now), key=event_key)
    decisions = [e for e in events if e["kind"] == "acceptance"]
    audits = [e for e in events if e["kind"] == "audit"]
    tests = [e for e in events if e["kind"] == "tests"]
    by_run = {r["run_id"]: r for r in runs}

    def quality_events(run_id, at):
        test = next((e for e in reversed(tests) if e["run_id"] == run_id and event_key(e) <= at), {})
        audit = max((e for e in audits if e["run_id"] == run_id and event_key(e) <= at),
                    key=lambda e: (e["round"], event_key(e)), default={})
        return test, audit

    def quality(decision, at):
        run_id = decision.get("run_id")
        run = by_run.get(run_id)
        if (not decision.get("criteria_met") or not run or run["outcome"] != "completed" or
                not run["ended_at"] or instant(run["ended_at"]) > event_key(decision)):
            return False
        # An ELABORADO implementation in the acceptance lineage requires formal review.
        audit_required = any(r["selected_mode"] == "ELABORADO" and
                             instant(r["started_at"]) <= event_key(decision) for r in runs)
        test, audit = quality_events(run_id, at)
        tests_ok = test.get("status") == "PASS" or (
            test.get("status") == "not-applicable" and test.get("na_reason") in NA_REASONS)
        audit_pass = (audit.get("status") == "PASS" and isinstance(audit.get("score"), (int, float)) and
                      8.5 <= audit["score"] <= 10 and
                      audit.get("no_blockers") is True)
        return (tests_ok and
                audit.get("status") != "REJECT" and
                (not audit_required or audit_pass))

    accepted = [e for e in decisions if e["status"] == "accepted" and quality(e, event_key(e))]
    first = accepted[0] if accepted else None
    latest = decisions[-1] if decisions else {}
    reviewed_run = latest.get("run_id") or runs[-1]["run_id"]
    test, audit = quality_events(reviewed_run, now)
    acceptance = latest.get("status", "unknown")
    if acceptance == "accepted" and (latest not in accepted or not quality(latest, now) or any(
            r["selected_mode"] and instant(r["started_at"]) > event_key(latest) for r in runs)):
        acceptance = "unknown"
    adjudications = {e["commit"]: e for e in events if e["kind"] == "hotfix"}
    followups = [e for e in events if e["kind"] == "defect"]
    confirmed = [e for e in adjudications.values() if e["status"] == "confirmed"]
    confirmed.extend(e for e in followups if e["status"] == "confirmed")
    accepted_revision = accepted[-1] if accepted else None
    none_observed = any(e["status"] == "none-observed" and accepted_revision and
                        event_key(e) >= event_key(accepted_revision) + window for e in followups)
    links = [e for e in events if e["kind"] == "link"]
    post_commit_end = max((instant(e["commit_at"]) + window for e in links if e.get("commit_at")),
                          default=started)
    post_commit_followup = none_observed and any(e["status"] == "none-observed" and
                                                  event_key(e) >= post_commit_end for e in followups)
    suspected = (any(e["status"] == "suspected" for e in adjudications.values()) or
                 bool(followups and followups[-1]["status"] == "suspected"))
    anchor = first if confirmed else accepted_revision
    observation_anchor = event_key(anchor) if anchor else max(last_activity, event_key(latest) if latest else started)
    mature = now >= observation_anchor + window and acceptance != "pending"
    if not mature:
        effective = "PENDING"
    elif confirmed:
        effective = "FAIL"
    elif acceptance == "accepted" and none_observed:
        effective = "PASS"
    elif acceptance in ("rejected", "abandoned"):
        effective = "FAIL"
    else:
        effective = "UNKNOWN"

    initial_attempts = [r for r in runs if r["selected_mode"] and
                        (not first or instant(r["started_at"]) <= event_key(first))]
    # A classifier failure spends resources but does not start implementation.
    first_pass = bool(first and len(initial_attempts) == 1 and first["run_id"] == initial_attempts[0]["run_id"]
                      and not any(e["status"] == "REJECT" and event_key(e) <= event_key(first) for e in audits))
    before_acceptance = [r for r in runs if first and instant(r["started_at"]) <= event_key(first)]
    first_implementation = next((index for index, run in enumerate(runs) if run["selected_mode"]), len(runs))
    corrections = runs[first_implementation + 1:]
    rejected_rounds = {(e["run_id"], e["round"]) for e in audits if e["status"] == "REJECT" and
                       first and event_key(e) <= event_key(first)}
    distinct_modes = {r["selected_mode"] for r in runs if r["selected_mode"]}
    repairs = []
    for defect in confirmed:
        detected = instant(defect.get("detected_at", defect["at"]))
        resolved = [event_key(e) for e in accepted if event_key(e) >= detected]
        if defect.get("repair_at") and instant(defect["repair_at"]) >= detected:
            resolved.append(instant(defect["repair_at"]))
        if resolved:
            repairs.append((min(resolved) - detected).total_seconds() / 86400)
    return {
        "task_id": runs[0]["task_id"], "mode": initial["selected_mode"] or "UNKNOWN",
        "complexity": complexity(initial, events), "mixed": len(distinct_modes) > 1,
        "started": started, "mature": mature, "effective": effective, "acceptance": acceptance,
        "tests": test.get("status", "unknown"), "audit": audit.get("status", "unknown"),
        "audit_score": audit.get("score"), "audit_round": audit.get("round"),
        "first": bool(first), "first_pass": first_pass, "spent": sum_cost(runs, coverage),
        "initial_spent": sum_cost(before_acceptance, coverage),
        "correction_spent": sum_cost(corrections, coverage),
        "corrections": max(0, len(initial_attempts) - 1), "rejections": len(rejected_rounds),
        "confirmed": bool(confirmed), "confirmed_hotfix": any(e["kind"] == "hotfix" for e in confirmed),
        "suspected": suspected, "none_observed": none_observed,
        "linked": bool(links), "post_commit_followup": post_commit_followup, "repairs": repairs,
        "audits": list({(e["run_id"], e["round"]): e for e in audits}.values()),
        "runs": runs,
    }

def cohort(tasks, incomplete_ingestion=False):
    eligible = [task for task in tasks if task["mature"]]
    accepted = [task for task in tasks if task["first"]]
    first_pass = [task for task in accepted if task["first_pass"]]
    effective = sum(task["effective"] == "PASS" for task in eligible)
    spent = sum(task["spent"][0] for task in eligible)
    missing = sum(task["spent"][1] for task in eligible)
    post_commit = [task for task in eligible if task["linked"] and task["first"]]
    followed = [task for task in post_commit if task["confirmed"] or task["post_commit_followup"]]
    failed = [task for task in eligible if task["effective"] == "FAIL"]
    initial_missing = sum(task["initial_spent"][1] for task in accepted)
    first_missing = sum(task["initial_spent"][1] for task in first_pass)
    audits = [audit for task in tasks for audit in task["audits"]]
    scores = [audit["score"] for audit in audits if audit.get("score") is not None]
    repairs = [repair for task in tasks for repair in task["repairs"]]
    return {
        "tasks": len(tasks),
        "eligible_tasks": len(eligible),
        "effective_pass": effective,
        "failed_or_unknown": len(eligible) - effective,
        "failed": len(failed),
        "unknown": sum(task["effective"] == "UNKNOWN" for task in eligible),
        "pending": len(tasks) - len(eligible),
        "observed_spent_tokens": spent,
        "total_spent_tokens": spent if not (missing or incomplete_ingestion) else None,
        "incomplete_cost_runs": missing,
        "tokens_per_effective_pass": ratio(spent, effective) if not (missing or incomplete_ingestion) else None,
        "effective_pass_pct": ratio(100 * effective, len(eligible)),
        "initially_accepted": len(accepted),
        "tokens_per_initial_acceptance": (ratio(sum(t["initial_spent"][0] for t in accepted), len(accepted))
                                          if not (initial_missing or incomplete_ingestion) else None),
        "first_pass_accepted": len(first_pass),
        "first_pass_success_pct": ratio(100 * sum(t["first_pass"] for t in eligible), len(eligible)),
        "tokens_per_first_pass_acceptance": (ratio(sum(t["initial_spent"][0] for t in first_pass), len(first_pass))
                                              if not (first_missing or incomplete_ingestion) else None),
        "observed_failed_task_tokens": sum(task["spent"][0] for task in failed),
        "observed_correction_tokens": sum(task["correction_spent"][0] for task in tasks),
        "incomplete_correction_runs": sum(task["correction_spent"][1] for task in tasks),
        "corrections_before_acceptance": sum(task["corrections"] for task in accepted),
        "audit_rejections_before_acceptance": sum(task["rejections"] for task in accepted),
        "post_commit_tasks": len(post_commit),
        "post_commit_known_followup": len(followed),
        "confirmed_hotfix_pct": ratio(100 * sum(t["confirmed_hotfix"] for t in followed), len(followed)),
        "post_commit_confirmed_defect_pct": ratio(100 * sum(t["confirmed"] for t in followed), len(followed)),
        "confirmed_defect_tasks": sum(task["confirmed"] for task in tasks),
        "suspected_defect_tasks": sum(task["suspected"] for task in tasks),
        "mixed_mode_tasks": sum(task["mixed"] for task in tasks),
        "mean_time_to_repair_days": ratio(sum(repairs), len(repairs)),
        "timed_repairs": len(repairs),
        "audit_pass": sum(a["status"] == "PASS" for a in audits),
        "audit_reject": sum(a["status"] == "REJECT" for a in audits),
        "audit_pass_pct": ratio(100 * sum(a["status"] == "PASS" for a in audits),
                                sum(a["status"] in ("PASS", "REJECT") for a in audits)),
        "audit_average_score": ratio(sum(scores), len(scores)),
    }


def contribution(runs, coverage):
    observed, incomplete = sum_cost(runs, coverage)
    completed = [run_cost(run, coverage) for run in runs if run["outcome"] == "completed"]
    complete_samples = [tokens for tokens, complete in completed if complete]
    durations = [r["duration_seconds"] for r in runs if r["duration_seconds"] is not None]
    usages = [run[field] for run in runs for field in ("execution_usage", "classifier_usage")]
    token_totals = {key: sum(usage[key] for usage in usages) for key in TOKEN_FIELDS}
    counts = Counter(run["outcome"] for run in runs)
    return {
        "runs": len(runs), **{key: counts[key] for key in ("completed", "failed", "interrupted", "unknown")},
        "complete_usage_completed_runs": len(complete_samples),
        "observed_tokens": observed, "incomplete_cost_runs": incomplete,
        "avg_tokens_per_completed_run": ratio(sum(complete_samples), len(complete_samples)),
        "median_tokens_per_completed_run": statistics.median(complete_samples) if complete_samples else None,
        "mean_duration_seconds": ratio(sum(durations), len(durations)),
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "observed_duration_seconds": round(sum(durations), 3),
        "timed_runs": len(durations),
        "observed_classifier_tokens": sum(count_tokens(r["classifier_usage"]) for r in runs),
        "incomplete_classifier_runs": sum(r["classifier_usage"]["status"] == "incomplete" for r in runs),
        **token_totals,
        "uncached_input_tokens": token_totals["input_tokens"] - token_totals["cached_input_tokens"],
    }


def table(headers, rows):
    def cell(value):
        return html.escape(str(value)) if value is not None else "UNKNOWN"

    header = "".join(f"<th>{cell(key.replace('_', ' '))}</th>" for key in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell(row.get(key))}</td>" for key in headers) + "</tr>"
                   for row in rows)
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def report(store, runs, evidence, errors, args):
    now = instant(args.as_of) if args.as_of else datetime.now(timezone.utc)
    window = timedelta(days=args.window_days)
    by_task, events = defaultdict(list), defaultdict(list)
    coverage = set()
    for event in evidence:
        if event_key(event) <= now:
            events[event["task_id"]].append(event)
            if event["kind"] == "coverage":
                coverage.add(event["run_id"])
    for run in runs:
        # A snapshot overwritten after the requested as-of cannot reconstruct partial past usage.
        if instant(run["ended_at"] or run["started_at"]) <= now:
            by_task[run["task_id"]].append(run)
        elif instant(run["started_at"]) <= now:
            errors += 1
    tasks = [task_view(group, events[key], now, window, coverage) for key, group in by_task.items()]
    tasks = [t for t in tasks if
             (not args.mode or t["mode"] == args.mode) and
             (not args.complexity or t["complexity"] == args.complexity) and
             (not args.start or t["started"] >= instant(args.start)) and
             (not args.end or t["started"] < instant(args.end)) and
             (args.maturity == "all" or t["mature"] == (args.maturity == "mature"))]
    cohorts = [dict(mode=mode, **cohort([t for t in tasks if t["mode"] == mode], errors)) for mode in MODES]
    contributions = [dict(mode=mode, **contribution(
        [r for t in tasks for r in t["runs"] if (r["selected_mode"] or "UNKNOWN") == mode], coverage))
        for mode in MODES]
    trends = []
    for month in sorted({t["started"].strftime("%Y-%m") for t in tasks}):
        for mode in MODES:
            group = [t for t in tasks if t["mode"] == mode and t["started"].strftime("%Y-%m") == month]
            if group:
                trends.append(dict(month=month, mode=mode, **cohort(group, errors)))
    task_rows = [{key: t[key] for key in ("task_id", "mode", "complexity", "mixed", "mature", "effective", "acceptance",
                                         "tests", "audit", "audit_score", "audit_round",
                                         "first_pass", "confirmed", "suspected")} |
                 {"attempts": len(t["runs"]), "observed_tokens": t["spent"][0],
                  "incomplete_cost_runs": t["spent"][1]} for t in tasks]
    segments = []
    for mode in MODES:
        for level in ("low", "moderate", "high", "UNKNOWN"):
            group = [t for t in tasks if t["mode"] == mode and t["complexity"] == level]
            if group:
                segments.append(dict(mode=mode, complexity=level, **cohort(group, errors)))
    headline = ("mode", "eligible_tasks", "effective_pass", "failed_or_unknown", "total_spent_tokens",
                "tokens_per_effective_pass", "first_pass_success_pct", "confirmed_hotfix_pct")
    repair_counts = Counter()
    for t in tasks:
        latest = {e["commit"]: e["status"] for e in sorted(events[t["task_id"]], key=event_key)
                  if e["kind"] == "hotfix"}
        if args.scan_git and t["linked"]:
            for sha, (status, _) in git_candidates(ROOT, t["task_id"], events[t["task_id"]]).items():
                latest.setdefault(sha, status)
        repair_counts.update(latest.values())
    output = store / "reports"
    sections = [("Primary cohort", cohorts), ("Mode resource contribution", contributions),
                ("Complexity", segments), ("Monthly cohorts", trends), ("Tasks", task_rows)]
    for filename, (_, rows) in zip(("cohorts", "contributions", "complexity", "trends", "tasks"), sections):
        def write_csv(stream):
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        atomic(output / f"{filename}.csv", write_csv)
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>MarketPulse Codex telemetry</title>
<style>body{{font:15px system-ui;margin:2rem;color:#192630}}table{{border-collapse:collapse;margin-bottom:1rem}}
td,th{{padding:.45rem .65rem;border:1px solid #c5d2dd;text-align:right}}th{{background:#eaf1f6}}
.scroll{{overflow:auto}}p{{max-width:80ch}}</style>
<h1>Tokens per effective PASS by initial mode</h1>
<p>As of {html.escape(now.isoformat())} · Observation window {args.window_days} days · Ingestion gaps {errors}.
UNKNOWN totals indicate incomplete coverage or a zero denominator. Observed tokens may be partial.
These are observational cohorts. Check sample sizes, maturity and complexity before comparing modes.</p>
{table(headline, cohorts)}
<p>Hotfix candidates in this selection: {repair_counts['confirmed']} confirmed,
{repair_counts['suspected']} suspected, {repair_counts['unrelated']} unrelated, {repair_counts['unknown']} unknown.
Run the candidates command for current Git inferences.</p>"""
    for title, rows in sections:
        document += f"<h2>{title}</h2>"
        if rows:
            document += f'<div class="scroll">{table(list(rows[0]), rows)}</div>'
        else:
            document += "<p>No records.</p>"
    document += "</html>"
    atomic(output / "dashboard.html", lambda stream: stream.write(document))
    for row in cohorts:
        print(" | ".join(str(row[key]) if row[key] is not None else "UNKNOWN" for key in headline))
    print(output / "dashboard.html")
    return cohorts


EVIDENCE_KINDS = ("link", "tests", "audit", "acceptance", "defect", "hotfix", "coverage", "complexity")

def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    actions = cli.add_subparsers(dest="action", required=True)
    dashboard = actions.add_parser("report")
    dashboard.add_argument("--mode", choices=MODES)
    dashboard.add_argument("--complexity", choices=("low", "moderate", "high", "UNKNOWN"))
    dashboard.add_argument("--start", type=instant_argument, help="Inclusive task start date")
    dashboard.add_argument("--end", type=instant_argument, help="Exclusive task start date")
    dashboard.add_argument("--as-of", type=instant_argument)
    dashboard.add_argument("--window-days", type=int, default=14)
    dashboard.add_argument("--maturity", choices=("all", "mature", "pending"), default="all")
    dashboard.add_argument("--scan-git", action="store_true", help="Include live local Git suspicions")

    listing = actions.add_parser("runs")
    listing.add_argument("--task-id", type=identifier)
    for action in (*EVIDENCE_KINDS, "candidates"):
        cmd = actions.add_parser(action)
        cmd.add_argument("--task-id", type=identifier, required=True)
        if action in ("tests", "audit", "coverage", "acceptance", "link"):
            cmd.add_argument("--run-id", type=identifier, required=action in ("tests", "audit", "coverage"))
        if action in ("link", "hotfix"):
            cmd.add_argument("--commit", required=True)
        if action == "candidates":
            cmd.add_argument("--ref", default="HEAD")
        elif action == "tests":
            cmd.add_argument("--status", choices=("PASS", "FAIL", "not-applicable", "not-run", "unknown"),
                             required=True)
            cmd.add_argument("--na-reason", choices=NA_REASONS)
            cmd.add_argument("--evidence-source", choices=("machine-result", "command-exit"))
            cmd.add_argument("--exit-code", type=int)
        elif action == "audit":
            cmd.add_argument("--status", choices=("PASS", "REJECT", "not-applicable", "unknown"), required=True)
            cmd.add_argument("--round", type=int, choices=(1, 2, 3), required=True)
            cmd.add_argument("--score", type=float)
            cmd.add_argument("--no-blockers", action="store_true")
        elif action == "coverage":
            cmd.add_argument("--host-verified-aggregate", action="store_true", required=True)
        elif action == "acceptance":
            cmd.add_argument("--status", choices=("accepted", "rejected", "abandoned", "pending", "unknown"),
                             required=True)
            cmd.add_argument("--criteria-met", action="store_true")
        elif action == "defect":
            cmd.add_argument("--status", choices=("confirmed", "suspected", "none-observed", "unknown"), required=True)
        elif action == "hotfix":
            cmd.add_argument("--status", choices=("confirmed", "suspected", "unrelated", "unknown"), required=True)
        elif action == "complexity":
            cmd.add_argument("--level", choices=("low", "moderate", "high", "UNKNOWN"), required=True)
        if action in ("defect", "hotfix"):
            cmd.add_argument("--detected-at", type=instant_argument)
    return cli


def instant_argument(value):
    try:
        instant(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO 8601 date or timestamp") from exc
    return value


def adjudicate(args, repo, store, runs, evidence):
    task_runs = [r for r in runs if r["task_id"] == args.task_id]
    task_events = [e for e in evidence if e["task_id"] == args.task_id]
    if not task_runs:
        raise ValueError("Unknown task ID. Use runs to find recorded IDs.")
    if args.action == "candidates":
        for sha, (status, reason) in git_candidates(repo, args.task_id, sorted(task_events, key=event_key), args.ref).items():
            print(f"{sha} | {status} | {reason}")
        return
    fields = vars(args).copy()
    action = fields.pop("action")
    task_id = fields.pop("task_id")
    run_id = fields.get("run_id")
    run = next((r for r in task_runs if r["run_id"] == run_id), None)
    if run_id and not run:
        raise ValueError("Run ID does not belong to this task.")
    if action in ("link", "hotfix"):
        fields["commit"] = resolve_commit(repo, args.commit)
    if action == "link":
        fields["commit_at"] = git(repo, "show", "-s", "--format=%cI", fields["commit"]).strip()
    if action == "hotfix" and args.status == "confirmed":
        fields["repair_at"] = git(repo, "show", "-s", "--format=%cI", fields["commit"]).strip()
    if fields.get("detected_at"):
        if args.status != "confirmed" or instant(args.detected_at) > datetime.now(timezone.utc):
            raise ValueError("Detection date must be in the past and used with a confirmed defect.")
    else:
        fields.pop("detected_at", None)
    if action == "tests" and (args.status == "not-applicable") != bool(args.na_reason):
        raise ValueError("A not-applicable test decision requires a fixed reason code.")
    if action == "tests":
        if (args.status in ("PASS", "FAIL")) != bool(args.evidence_source):
            raise ValueError("PASS and FAIL require an evidence source inspected by the operator.")
        if (args.evidence_source == "command-exit") != (args.exit_code is not None):
            raise ValueError("Supply an exit code only for command-exit evidence.")
        if args.exit_code is not None and (args.exit_code == 0) != (args.status == "PASS"):
            raise ValueError("Test status disagrees with the exit code.")
    if action == "audit":
        if args.score is not None and not 0 <= args.score <= 10:
            raise ValueError("Audit score must be between 0 and 10.")
        if args.status == "PASS" and (args.score is None or args.score < 8.5 or not args.no_blockers):
            raise ValueError("Formal PASS requires an unrounded score of at least 8.50 and no open P0/P1.")
    if action == "coverage" and run["execution_usage"]["status"] != "observed":
        raise ValueError("Coverage cannot fill in missing execution usage.")
    if action == "acceptance" and args.status == "accepted":
        if not args.criteria_met or not run or run["outcome"] != "completed":
            raise ValueError("Acceptance requires a completed run and --criteria-met.")
        now = datetime.now(timezone.utc)
        trial = {"kind": "acceptance", "status": "accepted", "run_id": run_id,
                 "criteria_met": True, "at": now.isoformat()}
        view = task_view(task_runs, [*task_events, trial], now, timedelta(days=14), set())
        if not view["first"] or view["acceptance"] != "accepted":
            raise ValueError("This run is missing passing test or required audit evidence.")
    append_evidence(store, task_id, action, provenance="operator-attested", **fields)
    print(f"Recorded {action} for {task_id}")


def main():
    cli = parser()
    args = cli.parse_args()
    runs, evidence, errors = load(STORE)
    try:
        if args.action == "report":
            if args.window_days < 0:
                raise ValueError("Observation window must be nonnegative.")
            if args.as_of and args.scan_git:
                raise ValueError("Live Git candidates cannot be combined with a historical as-of report.")
            report(STORE, runs, evidence, errors, args)
        elif args.action == "runs":
            print("Task ID | Run ID | Started (UTC) | Mode | Outcome")
            for run in sorted(runs, key=lambda item: instant(item["started_at"])):
                if not args.task_id or args.task_id == run["task_id"]:
                    print(f"{run['task_id']} | {run['run_id']} | {run['started_at']} | "
                          f"{run['selected_mode'] or 'UNKNOWN'} | {run['outcome']}")
            if errors:
                print(f"Skipped invalid records: {errors}")
        else:
            adjudicate(args, ROOT, STORE, runs, evidence)
    except subprocess.CalledProcessError:
        cli.error("Local Git lookup failed. Check the repository, commit and ref.")
    except (ValueError, OSError) as exc:
        cli.error(str(exc))


if __name__ == "__main__":
    main()
