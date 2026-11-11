"""Independent M7.C commands. Inference uses the pinned local cache."""
from argparse import ArgumentParser
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3

from pydantic import BaseModel

from .corpus import Corpus, GEO, RELATIONS, Scope
from .index import LocalE5, build_index
from .retrieval import HybridRetriever

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "docs/m7b"
EVALUATION = ROOT / "docs/m7c/evaluation.jsonl"
LEGACY = ROOT / "backend/macro_ai/corpus.json"
INDEX = ROOT / "data/m7c/index.sqlite3"


def emit(value):
    def serialize(value):
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        raise TypeError(f"Unsupported JSON type: {type(value).__name__}")

    print(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False, default=serialize))


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--model-cache", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build")
    query = commands.add_parser("query")
    query.add_argument("question")
    query.add_argument("--event", choices=("covid", "ukraine", "genai"), required=True)
    query.add_argument("--indicator", choices=("eur-usd", "m3-stock", "hicp-yoy", "real-gdp-qoq", "deposit-rate"), required=True)
    query.add_argument("--cutoff", required=True)
    query.add_argument("--geography", choices=sorted(set(GEO.values())), required=True)
    query.add_argument("--relations", nargs="+", choices=RELATIONS, default=RELATIONS)
    query.add_argument("--limit", type=int, default=5)
    benchmark = commands.add_parser("evaluate")
    benchmark.add_argument("--dataset", type=Path, default=EVALUATION)
    benchmark.add_argument("--out", type=Path, required=True)
    benchmark.add_argument("--repeats", type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["LANGSMITH_TRACING"] = "false"

    try:
        corpus = Corpus.load(args.corpus)
        model = LocalE5(args.model_cache)
        import torch

        torch.set_num_threads(2)
        if args.command == "build":
            emit(asdict(build_index(args.index, corpus, model)))
            return
        retriever = HybridRetriever(corpus, args.index, model)
        if args.command == "query":
            scope = Scope(event=args.event, indicator=args.indicator, cutoff=args.cutoff,
                          geography=args.geography, relations=tuple(args.relations))
            emit([asdict(hit) for hit in retriever.search(args.question, scope, limit=args.limit)])
            return
        from .evaluate import evaluate

        result = evaluate(retriever, args.dataset, LEGACY, repeats=args.repeats)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
                            encoding="utf-8", newline="\n")
        emit(result["metrics"])
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"M7.C: {exc}\n")


if __name__ == "__main__":
    main()
