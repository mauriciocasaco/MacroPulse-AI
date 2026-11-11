"""Run the experimental gate or its offline benchmark."""
from argparse import ArgumentParser
import json
import os
from pathlib import Path

from backend.m7c.corpus import GEO, RELATIONS, Scope
from backend.m7c.index import LocalE5

from .evaluate import evaluate
from .gate import EvidenceGate
from .ranking import SemanticRanker

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "docs/m7b")
    parser.add_argument("--model-cache", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    query = commands.add_parser("query")
    query.add_argument("question")
    query.add_argument("--event", required=True)
    query.add_argument("--indicator", required=True)
    query.add_argument("--geography", choices=sorted(set(GEO.values())), required=True)
    query.add_argument("--cutoff", required=True)
    query.add_argument("--relations", nargs="+", choices=RELATIONS, default=RELATIONS)
    benchmark = commands.add_parser("evaluate")
    benchmark.add_argument("--dataset", type=Path, required=True)
    benchmark.add_argument("--kind", choices=("regression", "selection", "holdout"), required=True)
    benchmark.add_argument("--out", type=Path, required=True)
    benchmark.add_argument("--repeats", type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        os.environ[key] = "1"
    os.environ["LANGSMITH_TRACING"] = "false"
    try:
        model = LocalE5(args.model_cache) if args.model_cache else None
        if model:
            import torch

            torch.set_num_threads(2)
        if args.command == "query":
            scope = Scope(event=args.event, indicator=args.indicator, cutoff=args.cutoff,
                          geography=args.geography, relations=tuple(args.relations))
            gate = (EvidenceGate(args.corpus, ranker=lambda texts: SemanticRanker(texts, model))
                    if model else EvidenceGate(args.corpus))
            print(gate.assess(args.question, scope).model_dump_json(indent=2))
        else:
            output = evaluate(args.corpus, args.dataset, args.kind, model=model, repeats=args.repeats)
            args.out.write_text(json.dumps(output, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
                                encoding="utf-8", newline="\n")
            print(f"{len(output['cases'])} cases written to {args.out}")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"M7.D.1: {exc}\n")


if __name__ == "__main__":
    main()
