"""Reproducible offline evaluation of claim ranking and the evidence gate."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import platform
from statistics import median, quantiles

from pydantic import Field

from backend.m7c.corpus import Scope, digest, read_jsonl
from backend.m7c.evaluate import Case, hit_rate, measure
from backend.m7c.index import LocalE5
from scripts.m7b_schema import ID

from .contracts import Sufficiency
from .gate import EvidenceGate
from .ranking import SemanticRanker


class Judgment(Case):
    expected_sufficiency: Sufficiency
    required_claim_groups: list[list[ID]] = Field(default_factory=list)


def _case_result(case, gate, *, gated: bool, repeats: int) -> dict:
    scope = Scope.model_validate(case.model_dump(include=set(Scope.model_fields)))
    if gated:
        result, latencies = measure(lambda: gate.assess(case.question, scope), repeats)
        selected = result.selected_claims
    else:
        selected, latencies = measure(lambda: gate.rank_candidates(case.question, scope), repeats)
        result = None
    return {
        "claim_ids": [claim.claim_id for claim in selected],
        "document_ids": list(dict.fromkeys(claim.document_id for claim in selected)),
        "grounding_claim_ids": [claim.claim_id for claim in result.grounding_claims] if result else None,
        "status": result.status if result else None,
        "limitations": result.limitations if result else None,
        "latency_ms": latencies,
    }


def _summary(rows: list[dict], method: str) -> dict:
    items = [(row, row["runs"][method]) for row in rows]
    claim_rankings = [(run["claim_ids"], row["relevant_claims"]) for row, run in items if row["relevant_claims"]]
    doc_rankings = [(run["document_ids"], row["relevant_documents"]) for row, run in items if row["relevant_documents"]]
    claims = sum(len(run["claim_ids"]) for _, run in items)
    relevant = sum(len(set(run["claim_ids"]) & set(row["relevant_claims"])) for row, run in items)
    grounding_claims = sum(len(run["grounding_claim_ids"] or ()) for _, run in items)
    relevant_grounding = sum(len(set(run["grounding_claim_ids"] or ()) & set(row["relevant_claims"])) for row, run in items)
    no_answer = [(row, run) for row, run in items if row["expected_sufficiency"] != "sufficient"]
    no_claims = [run for row, run in items if not row["relevant_claims"]]
    complete = [(row, run) for row, run in items if row["expected_sufficiency"] == "sufficient"]
    multiple = [(row, run) for row, run in items if row["required_claim_groups"]]
    gated = method.startswith("gate_")
    latencies = [sample for _, run in items for sample in run["latency_ms"]]
    return {
        "cases": len(rows), "positive_cases": len(complete), "negative_or_partial_cases": len(no_answer),
        "claim_hit_at_1": hit_rate(claim_rankings, 1), "claim_hit_at_3": hit_rate(claim_rankings, 3),
        "document_hit_at_1": hit_rate(doc_rankings, 1), "document_hit_at_3": hit_rate(doc_rankings, 3),
        "claims_returned": claims, "claims_relevant": relevant, "claim_precision": relevant / claims if claims else None,
        "grounding_claim_precision": relevant_grounding / grounding_claims if grounding_claims else None,
        "cases_without_relevant_claims": len(no_claims),
        "cases_without_relevant_claims_with_hits": sum(bool(run["claim_ids"]) for run in no_claims),
        "false_answers": sum(run["status"] == "sufficient" for _, run in no_answer) if gated else None,
        "false_abstentions": sum(run["status"] != "sufficient" for _, run in complete) if gated else None,
        "status_matches": sum(row["expected_sufficiency"] == run["status"] for row, run in items) if gated else None,
        "status_confusion": dict(Counter(f'{row["expected_sufficiency"]} -> {run["status"]}' for row, run in items))
        if gated else None,
        "multi_claim_cases": len(multiple),
        "multi_claim_cases_with_all_groups": sum(
            all(set(group) & set(run["claim_ids"]) for group in row["required_claim_groups"]) for row, run in multiple
        ),
        "latency_ms": {
            "median": median(latencies),
            "p95": quantiles(latencies, n=20, method="inclusive")[-1] if len(latencies) > 1 else latencies[0],
        },
    }


def evaluate(corpus: Path, dataset: Path, kind: str, *, model: LocalE5 | None, repeats: int) -> dict:
    if kind not in {"regression", "selection", "holdout"} or not 1 <= repeats <= 10:
        raise ValueError("Invalid benchmark kind or repeat count")
    cases = read_jsonl(dataset, Case if kind == "regression" else Judgment)
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Evaluation cases must have unique IDs")
    lexical = EvidenceGate(corpus)
    gates = {"lexical": lexical}
    if model is not None:
        gates["e5"] = EvidenceGate(corpus, ranker=lambda texts: SemanticRanker(texts, model))
        model.encode("annual inflation rate in the euro area", query=True)
    rows = []
    for case in cases:
        scope = Scope.model_validate(case.model_dump(include=set(Scope.model_fields)))
        eligible = set(lexical._eligible(scope))
        if not set(case.relevant_claims) <= eligible or {
            lexical._corpus.claims[key].document_id for key in case.relevant_claims
        } != set(case.relevant_documents):
            raise ValueError(f"Evaluation judgments do not match approved eligibility: {case.id}")
        status = case.expected_sufficiency if isinstance(case, Judgment) else (
            "sufficient" if case.relevant_claims else "insufficient"
        )
        groups = case.required_claim_groups if isinstance(case, Judgment) else (
            [[key] for key in case.relevant_claims] if len(case.relevant_claims) > 1 else []
        )
        if any(not group or not set(group) <= set(case.relevant_claims) for group in groups):
            raise ValueError(f"Invalid required claim groups: {case.id}")
        runs = {
            f"{'gate' if gated else 'rank'}_{name}": _case_result(case, gate, gated=gated, repeats=repeats)
            for name, gate in gates.items() for gated in (False, True)
        }
        rows.append({
            "id": case.id, "indicator": case.indicator, "event": case.event, "expected_sufficiency": status,
            "relevant_claims": case.relevant_claims, "relevant_documents": case.relevant_documents,
            "required_claim_groups": groups, "runs": runs,
        })

    metrics = {}
    for name in rows[0]["runs"]:
        metrics[name] = _summary(rows, name)
        for dimension in ("event", "indicator"):
            metrics[name][f"by_{dimension}"] = {
                value: _summary([row for row in rows if row[dimension] == value], name)
                for value in sorted({row[dimension] for row in rows})
            }
    return {
        "schema_version": "m7d-eval-v1", "dataset_kind": kind, "dataset_sha256": digest(dataset.read_bytes()),
        "corpus_sha256": lexical.corpus_sha256, "cases": rows, "metrics": metrics, "repeats": repeats,
        "environment": {"python": platform.python_version(), "system": platform.system(), "machine": platform.machine()},
        "e5_model": model.provenance if model else None, "e5_sha256": model.fingerprint if model else None,
    }
