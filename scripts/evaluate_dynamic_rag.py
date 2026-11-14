"""Evaluate the opt-in Research graph against DEV using the online reader.

Run from the checkout root with python -m scripts.evaluate_dynamic_rag.
Uses the configured read-only online DSN and simulated Gemini by default.
Set --gemini to use the existing public provider configuration.
"""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from os import environ
from time import monotonic, perf_counter

from backend.macro_ai.models import EVENT_WINDOWS, ResearchRequest
from backend.macro_ai.v2_models import Draft
from backend.macro_ai.v2_service import (
    ResearchRuntime, _EVENT_QUESTIONS, _INDICATOR_LABELS, run,
)
from backend.macro_data.contracts import CATALOG

# The DEV inventory established at the beginning of this phase.
EXPECTED_EDITORIAL = {
    "covid": {"eur-usd", "m3-stock", "deposit-rate", "real-gdp-qoq"},
    "ukraine": set(CATALOG),
    "generative-ai": {"real-gdp-qoq"},
}


def scenarios():
    for event, (start, end) in EVENT_WINDOWS.items():
        for indicator, label in _INDICATOR_LABELS.items():
            yield ResearchRequest(
                question=f"{_EVENT_QUESTIONS[event]} {label}?",
                start=start, end=end, event_id=event, indicator_id=indicator,
            )


def simulated_compose(statements):
    return Draft.model_validate({"paragraphs": [
        {"statement_id": item["statement_id"], "style": "direct"} for item in statements
    ]})


def report(request, result, elapsed):
    series = [item for item in result.evidence if item.kind == "series"]
    documents = [item for item in result.evidence if item.kind == "document"]
    editorial = [item for item in documents if item.claim_version]
    fallback = [item for item in documents if item.claim_version is None and item.source_id is None]
    legacy = [item for item in documents if item.source_id == "eia"]
    indicator = CATALOG[request.indicator_id]
    all_citations = {citation for row in result.narrative for citation in row.citations}
    expected = request.indicator_id in EXPECTED_EDITORIAL[request.event_id]
    causal_limit = any("causal effect" in line for line in result.limitations)
    valid = (
        all_citations == {item.id for item in result.evidence}
        and all(item.series_key == indicator.series_key and item.unit == indicator.unit for item in series)
        and all(item.attribution and item.locator and item.qualifiers for item in documents)
        and all(item.relation == "proxy" for item in editorial if request.event_id == "generative-ai")
        and causal_limit
    )
    outcome = ("FAIL" if not valid else
               "PARTIAL" if not series or (expected and not editorial) else "PASS")
    return {
        "outcome": outcome, "question": request.question, "event": request.event_id,
        "indicator": indicator.id, "kpi": indicator.title, "unit": indicator.unit,
        "official_source": indicator.source_url, "citation_count": len(all_citations),
        "editorial": [item.id for item in editorial], "fallback": [item.id for item in fallback],
        "legacy": [item.id for item in legacy],
        "latency_ms": round(elapsed * 1000, 2), "response": result.model_dump(mode="json"),
    }


def evaluate(execute):
    results = []
    for request in scenarios():
        started = perf_counter()
        response = execute(request)
        results.append(report(request, response, perf_counter() - started))
    return results


def main():
    from backend.macro_ai.online_evidence import retrieve_online
    from backend.macro_ai.public_gemini import PublicGemini
    from backend.macro_data.api import load_history
    from backend.public_demo import _research_config
    from backend.public_online import SCHEMA, connect_online, resolve_online_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gemini", action="store_true")
    options = parser.parse_args()
    settings = resolve_online_settings(environ)
    credentials = _research_config(environ) if options.gemini else None
    if options.gemini and not credentials:
        parser.error("Existing public Gemini configuration is unavailable")

    def execute(request):
        with ExitStack() as stack:
            reader = stack.enter_context(connect_online(settings))
            provider = PublicGemini(api_key=credentials[0], model=credentials[1],
                                    deadline=monotonic() + 30) if credentials else None

            def compose(rows):
                stack.close()
                return provider.compose(rows) if provider else simulated_compose(rows)

            runtime = ResearchRuntime(
                read=lambda key, window: load_history(reader, CATALOG[key], window, schema=SCHEMA),
                interpret=lambda _: None, compose=compose,
                model_id=credentials[1] if credentials else "simulated-plan",
                supplemental_retriever=lambda *args: retrieve_online(reader, *args, dynamic=True),
                capture_vintage="latest available Supabase DEV", dynamic_editorial=True,
            )
            return run(request, runtime=runtime)

    results = evaluate(execute)
    print(json.dumps({"evaluated_at": datetime.now(timezone.utc).isoformat(),
                      "gemini": "real" if credentials else "simulated", "results": results},
                     ensure_ascii=True, indent=2))
    raise SystemExit(int(any(item["outcome"] != "PASS" for item in results)))


if __name__ == "__main__":
    main()
