"""Retrieval metrics for labeled, reproducible corpora."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Judgment:
    expected: frozenset[str]
    ranked: tuple[str, ...]


def evaluate(judgments: list[Judgment], *, k: int) -> dict:
    if k < 1 or not judgments:
        raise ValueError("evaluation requires cases and a positive cutoff")
    positives = negatives = hits = false_positives = 0
    recall = precision = reciprocal_rank = 0.0
    for judgment in judgments:
        if len(set(judgment.ranked)) != len(judgment.ranked):
            raise ValueError("evaluation results must be deduplicated")
        top = judgment.ranked[:k]
        if not judgment.expected:
            negatives += 1
            false_positives += bool(top)
            continue
        positives += 1
        relevant = set(top) & judgment.expected
        hits += bool(relevant)
        recall += len(relevant) / len(judgment.expected)
        precision += len(relevant) / k
        reciprocal_rank += next((1 / i for i, result in enumerate(top, 1) if result in relevant), 0.0)
    return {
        "k": k,
        "positive_cases": positives,
        "no_evidence_cases": negatives,
        "false_positive_cases": false_positives,
        "hit_at_k": hits / positives if positives else None,
        "recall_at_k": recall / positives if positives else None,
        "precision_at_k": precision / positives if positives else None,
        "mrr_at_k": reciprocal_rank / positives if positives else None,
    }
