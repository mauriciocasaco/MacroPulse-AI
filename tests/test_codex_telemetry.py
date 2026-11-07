import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_telemetry as telemetry

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
WINDOW = timedelta(days=14)


def usage(total=0, status="observed"):
    return dict(status=status, input_tokens=total * 3 // 4,
                cached_input_tokens=total // 4, output_tokens=total - total * 3 // 4,
                reasoning_output_tokens=total // 8)


def run(mode, total, task=None, age=40, outcome="completed", classification=0, status="observed"):
    return dict(schema_version=1, run_id=str(uuid.uuid4()), task_id=task or str(uuid.uuid4()),
                selected_mode=mode, requested_mode="AUTO" if classification else mode,
                started_at=(NOW - timedelta(days=age)).isoformat(),
                ended_at=(NOW - timedelta(days=age) + timedelta(minutes=1)).isoformat(),
                duration_seconds=60, outcome=outcome,
                execution_usage=usage(total, status),
                classifier_usage=usage(classification, "observed" if classification else "not_started"),
                ratings={"scope": "Bounded"} if classification else None)


def evidence(r, kind, status, age=30, **fields):
    return dict(schema_version=1, at=(NOW - timedelta(days=age)).isoformat(), task_id=r["task_id"],
                run_id=r["run_id"], kind=kind, status=status, **fields)


def accepted(r, audit=False):
    events = [evidence(r, "tests", "PASS", 31)]
    if audit:
        events.append(evidence(r, "audit", "PASS", 31, score=8.5, round=2, no_blockers=True))
    return events + [evidence(r, "acceptance", "accepted", criteria_met=True),
                     evidence(r, "defect", "none-observed", 12)]


class AccountingTests(unittest.TestCase):
    def view(self, runs, events, coverage=()):
        return telemetry.task_view(runs, events, NOW, WINDOW, set(coverage))

    def test_exact_kpi_all_modes_mixed_and_abandoned(self):
        simple = run("SIMPLE", 100)
        retry = run("MEDIO", 50, simple["task_id"], age=37)
        abandoned = run("SIMPLE", 50, outcome="failed")
        medio = run("MEDIO", 270, classification=30)
        elabor = run("ELABORADO", 350)
        reject = run("ELABORADO", 50, outcome="failed")
        coverage = {r["run_id"] for r in (retry, medio, elabor, reject)}
        simple_tasks = [
            self.view([simple, retry], accepted(retry), coverage),
            self.view([abandoned], [evidence(abandoned, "acceptance", "abandoned")]),
        ]
        medio_tasks = [self.view([medio], accepted(medio), coverage)]
        elabor_tasks = [
            self.view([elabor], accepted(elabor, audit=True) +
                      [evidence(elabor, "audit", "REJECT", 32, score=8.49, round=1)], coverage),
            self.view([reject], [evidence(reject, "acceptance", "rejected")], coverage),
        ]
        results = [telemetry.cohort(tasks) for tasks in (simple_tasks, medio_tasks, elabor_tasks)]
        self.assertEqual([r["tokens_per_effective_pass"] for r in results], [200, 300, 400])
        self.assertEqual([r["eligible_tasks"] for r in results], [2, 1, 2])
        self.assertEqual(results[0]["tokens_per_initial_acceptance"], 150)
        self.assertEqual(results[0]["observed_correction_tokens"], 50)
        self.assertEqual(results[0]["first_pass_success_pct"], 0)
        self.assertEqual(results[2]["audit_rejections_before_acceptance"], 1)
        self.assertTrue(simple_tasks[0]["mixed"])
        contribution = telemetry.contribution([retry, medio], coverage)
        self.assertEqual(contribution["observed_tokens"], 350)
        self.assertEqual(contribution["observed_classifier_tokens"], 30)
        self.assertEqual(contribution["avg_tokens_per_completed_run"], 175)
        self.assertEqual(contribution["input_tokens"] + contribution["output_tokens"], 350)
        self.assertEqual(contribution["cached_input_tokens"] + contribution["uncached_input_tokens"],
                         contribution["input_tokens"])

    def test_unknown_boundaries_and_incomplete_attempts_withhold_kpi(self):
        good = run("SIMPLE", 100)
        incomplete = run("SIMPLE", 0, status="incomplete", outcome="interrupted")
        multi = run("ELABORADO", 200)
        multi["execution_usage"]["child_tokens"] = 500
        self.assertEqual(telemetry.run_cost(multi, set()), (200, False))
        self.assertEqual(telemetry.run_cost(multi, {multi["run_id"]}), (200, True))
        group = [self.view([good], accepted(good)), self.view([incomplete], [])]
        row = telemetry.cohort(group)
        self.assertEqual(row["effective_pass"], 1)
        self.assertEqual(row["unknown"], 1)
        self.assertEqual(row["observed_spent_tokens"], 100)
        self.assertIsNone(row["tokens_per_effective_pass"])

    def test_quality_requires_evidence_and_followup(self):
        simple = run("SIMPLE", 100)
        elaborate = run("ELABORADO", 100)
        self.assertEqual(self.view([simple], [])["effective"], "UNKNOWN")
        for events in (
            [e for e in accepted(simple) if e["kind"] != "tests"],
            accepted(simple) + [evidence(simple, "tests", "FAIL", 29)],
        ):
            self.assertEqual(self.view([simple], events)["effective"], "UNKNOWN")
        self.assertEqual(self.view([elaborate], accepted(elaborate))["effective"], "UNKNOWN")
        self.assertEqual(self.view([elaborate], accepted(elaborate, audit=True) +
                         [evidence(elaborate, "audit", "REJECT", 29, score=8.5, round=3)])["effective"],
                         "UNKNOWN")
        self.assertEqual(self.view([simple], accepted(simple)[:-1])["effective"], "UNKNOWN")
        pending = run("SIMPLE", 50, age=2)
        self.assertEqual(self.view([pending], [])["effective"], "PENDING")
        na = accepted(simple)
        na[0].update(status="not-applicable", na_reason="docs-only")
        self.assertEqual(self.view([simple], na)["effective"], "PASS")

    def test_late_hotfix_revises_pass_and_suspicion_does_not(self):
        original = run("SIMPLE", 100)
        events = accepted(original) + [
            evidence(original, "link", None, commit="a" * 40),
            evidence(original, "hotfix", "suspected", 3, commit="b" * 40),
        ]
        self.assertEqual(self.view([original], events)["effective"], "PASS")
        events += [evidence(original, "hotfix", "confirmed", 2, commit="b" * 40,
                            detected_at=(NOW - timedelta(days=5)).isoformat(),
                            repair_at=(NOW - timedelta(days=3)).isoformat())]
        view = self.view([original], events)
        self.assertEqual(view["effective"], "FAIL")
        self.assertEqual(view["repairs"], [2])
        self.assertEqual(telemetry.cohort([view])["confirmed_hotfix_pct"], 100)

    def test_report_outputs_and_corrupt_input(self):
        original = run("SIMPLE", 100)
        with tempfile.TemporaryDirectory(dir=telemetry.ROOT) as temporary:
            store = Path(temporary)
            path = store / "runs" / f"{original['run_id']}.json"
            telemetry.atomic(path, lambda stream: json.dump(original, stream))
            (path.parent / "bad.json").write_text("{bad", encoding="utf-8")
            runs, _, errors = telemetry.load(store)
            self.assertEqual((len(runs), errors), (1, 1))
            args = telemetry.parser().parse_args(["report", "--as-of", NOW.isoformat()])
            with contextlib.redirect_stdout(io.StringIO()):
                rows = telemetry.report(store, runs, accepted(original), errors, args)
            self.assertIsNone(rows[0]["tokens_per_effective_pass"])
            self.assertIn("Tokens per effective PASS", (store / "reports/dashboard.html").read_text(encoding="utf-8"))
            self.assertTrue((store / "reports/cohorts.csv").is_file())


class LocalGitTests(unittest.TestCase):
    def test_candidates_and_many_runs_for_one_commit(self):
        with tempfile.TemporaryDirectory(dir=telemetry.ROOT) as temporary:
            repo = Path(temporary)

            def git(*args):
                return telemetry.git(repo, *args).strip()

            git("init", "-q")

            def snapshot(message, name, contents, parent=None):
                (repo / name).write_text(contents, encoding="utf-8")
                git("add", "--", name)
                tree = git("write-tree")
                parents = ["-p", parent] if parent else []
                return git("-c", "user.name=Offline Fixture", "-c", "user.email=fixture@example.invalid",
                           "commit-tree", tree, *parents, "-m", message)

            first = run("SIMPLE", 100)
            second = run("MEDIO", 50, first["task_id"])
            original = snapshot("Implement example", "example.txt", "original")
            unrelated = snapshot("Refresh example", "example.txt", "refresh", original)
            different = snapshot("fix another subsystem", "other.txt", "other", unrelated)
            possible = snapshot("fix regression", "example.txt", "fix", different)
            explicit = snapshot(f"Follow up {first['task_id']}", "other.txt", "follow up", possible)
            events = [evidence(item, "link", None, commit=original) for item in (first, second)]
            matches = telemetry.git_candidates(repo, first["task_id"], events, explicit)
            self.assertEqual(set(matches), {possible, explicit})
            self.assertEqual(matches[possible][0], "suspected")
            events.append(evidence(first, "hotfix", "unrelated", commit=possible))
            events.append(evidence(first, "hotfix", "confirmed", commit=explicit))
            matches = telemetry.git_candidates(repo, first["task_id"], events, explicit)
            self.assertEqual(matches[possible][0], "unrelated")
            self.assertEqual(matches[explicit][0], "confirmed")


if __name__ == "__main__":
    unittest.main()
