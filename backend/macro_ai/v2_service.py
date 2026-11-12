"""Bounded Research v2 graph. Only the server authorizes answer statements."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache, wraps
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from ..macro_data.api import MacroWindow
from ..macro_data.contracts import CATALOG
from . import v2_gemini, v2_retrieval
from .gemini import configured_model
from .models import EVENT_WINDOWS, Claim, EventId, IndicatorId, ObservedChange, ResearchRequest
from .statistics import read_statistics
from .v2_models import Category, CuratedEvidence, Draft, Intent, NarrativeClaim, Requirement, ResearchV2Response, SeriesEvidence
from .v2_statistics import SeriesResult, exact_period, matches_definition, summarize_requirement


@dataclass(frozen=True)
class Plan:
    indicator: IndicatorId
    window: MacroWindow
    requirement: Requirement


@dataclass(frozen=True)
class Statement:
    claim: Claim
    category: Category
    alternate: str | None = None

    def options(self) -> list[str]:
        return [self.claim.text, self.alternate] if self.alternate else [self.claim.text]

    def render(self, text: str) -> NarrativeClaim:
        return NarrativeClaim(text=text, citations=self.claim.citations, category=self.category)


class State(TypedDict, total=False):
    request: ResearchRequest
    steps: list[str]
    limitations: list[str]
    intent: Intent
    event: EventId | None
    cutoff: date
    plans: list[Plan]
    official: list[SeriesResult]
    curated: list[tuple[CuratedEvidence, Claim]]
    corpus_sha256: str | None
    missing_context: bool
    evidence: list[SeriesEvidence | CuratedEvidence]
    statements: list[Statement]
    draft: Draft
    narrative: list[NarrativeClaim]
    model_id: str | None
    response: ResearchV2Response


def _note(state: State, text: str) -> list[str]:
    return list(dict.fromkeys([*state["limitations"], text]))


def interpret_with_llm(state: State) -> dict:
    try:
        return {"intent": Intent.model_validate(v2_gemini.interpret(state["request"]))}
    except Exception:
        return {"limitations": _note(state, "Gemini could not interpret this question safely. Review the question and local model configuration.")}


def validate_scope(state: State) -> dict:
    request, intent = state["request"], state["intent"]
    cutoff = state["cutoff"]
    if (intent.event and request.event_id and intent.event != request.event_id
            or intent.purpose == "uncertain"):
        return {"limitations": _note(state, "The event or research intent could not be reconciled with the selected scope.")}
    event = intent.event or request.event_id
    start, end = request.start, cutoff
    if event:
        event_start, event_end = EVENT_WINDOWS[event]
        start, end = max(start, event_start), min(end, event_end)
    if end < start:
        return {"limitations": _note(state, "The selected period has no available days in the historical scope.")}

    limitations = state["limitations"]
    if intent.unresolved:
        limitations = _note(state, "Part of the question could not be interpreted confidently.")
    plans = []
    for requirement in intent.requirements:
        plan = _plan(requirement, request, MacroWindow(start=start, end=end))
        if plan is None:
            limitations.append("A requirement has an unsupported or ambiguous series definition, operation or period.")
        elif plan not in plans:
            plans.append(plan)
    return {"event": event, "plans": plans, "limitations": list(dict.fromkeys(limitations))}


def _plan(requirement: Requirement, request: ResearchRequest, bounds: MacroWindow) -> Plan | None:
    key = requirement.indicator
    if (key is None or request.indicator_id not in {None, key}
            or requirement.excerpt not in request.question or not requirement.excerpt.strip()
            or not matches_definition(key, requirement)):
        return None
    operation = requirement.operation
    if operation == "other" or (operation == "relative_change" and key not in {"eur-usd", "m3-stock"}):
        return None
    frequency = CATALOG[key].frequency
    operands = requirement.operands
    if operation in {"difference", "relative_change"}:
        if (len(operands) != 2 or operands[0].end >= operands[1].start
                or operands[0].start < bounds.start or operands[1].end > bounds.end
                or not all(exact_period(period, frequency) for period in operands)):
            return None
        window = MacroWindow(start=operands[0].start, end=operands[1].end)
        if requirement.period and requirement.period != window:
            return None
    else:
        if operands:
            return None
        window = requirement.period or bounds
        if operation == "point" and (requirement.period is None or not exact_period(window, frequency)):
            return None
    if not bounds.start <= window.start <= window.end <= bounds.end:
        return None
    return Plan(key, window, requirement)


def read_official_statistics(state: State) -> dict:
    official = []
    limitations = state["limitations"].copy()
    windows = {}
    for plan in state["plans"]:
        if plan.requirement.operation == "causal_effect":
            continue
        window_key = (plan.indicator, plan.window.start, plan.window.end)
        try:
            if window_key not in windows:
                # Reuse the legacy reader with narrowed, catalog-validated bounds.
                request = ResearchRequest(question=state["request"].question, start=plan.window.start, end=plan.window.end)
                windows[window_key] = read_statistics((plan.indicator,), request)[plan.indicator]
        except Exception:
            windows[window_key] = None
        history = windows[window_key]
        if history is None:
            limitations.append("The local official data reader was unavailable for a requirement.")
            continue
        try:
            result = summarize_requirement(history, plan.indicator, plan.window, plan.requirement)
        except (ArithmeticError, ValueError):
            result = None
        if result:
            official.append(result)
        else:
            limitations.append("The required official observations are missing or not comparable.")
    return {"official": official, "limitations": list(dict.fromkeys(limitations))}


def retrieve_curated_evidence(state: State) -> dict:
    curated = {}
    limitations = state["limitations"].copy()
    event = state.get("event")
    fingerprint = None
    missing_context = False
    if event:
        seen = set()
        for plan in state["plans"]:
            if plan.requirement.operation not in {"trend", "causal_effect"}:
                continue
            key = (plan.indicator, plan.window.start, plan.window.end)
            if key in seen:
                continue
            seen.add(key)
            try:
                results = v2_retrieval.retrieve(state["request"].question, event, plan.indicator, plan.window, state["cutoff"])
            except Exception:
                missing_context = True
                limitations.append("Approved documentary retrieval was unavailable for a requirement.")
                continue
            if results:
                fingerprint = v2_retrieval.APPROVED_SNAPSHOT
                for source, claim in results:
                    curated[source.id] = (source, claim)
            else:
                missing_context = True
                limitations.append(f"The approved corpus has no applicable documentary context for {CATALOG[plan.indicator].title}.")
    return {"curated": list(curated.values()), "limitations": list(dict.fromkeys(limitations)),
            "corpus_sha256": fingerprint, "missing_context": missing_context}


def evaluate_support(state: State) -> dict:
    official = state["official"]
    curated = state["curated"]
    limitations = state["limitations"].copy()
    if state["intent"].purpose == "causal" or any(
        plan.requirement.operation == "causal_effect" for plan in state["plans"]
    ):
        limitations.append("The available evidence does not identify the event's causal effect or quantify how much it changed the indicator.")
    if state.get("event") == "generative-ai" and any(plan.indicator == "real-gdp-qoq" for plan in state["plans"]):
        limitations.append("The approved corpus has no direct evidence of a generative AI effect on euro-area GDP. EU adoption evidence is proxy context.")
    if official:
        limitations.append("Official observations use the local capture vintage. The period filter does not reconstruct the data available at the time.")
    if any(result.provisional for result in official):
        limitations.append("At least one cited observation is estimated or provisional.")
    if curated:
        limitations.append("Documentary context refers to the source's own geography, period and release vintage.")

    series: dict[str, SeriesEvidence] = {}
    for result in official:
        source = result.evidence
        previous = series.get(source.id)
        if previous:
            source = source.model_copy(update={
                "period_start": min(previous.period_start, source.period_start),
                "period_end": max(previous.period_end, source.period_end),
            })
        series[source.id] = source
    facts = [Statement(result.fact, "official observation") for result in official]
    calculations = [Statement(result.calculation, "calculated from official observations",
                              f"The official observations show that {result.calculation.text[0].lower()}{result.calculation.text[1:]}")
                    for result in official if result.calculation]
    context = []
    for source, claim in curated:
        category = ("institutional interpretation" if source.claim_type == "institutional_interpretation"
                    else "proxy context" if source.relation == "proxy" else "documentary context")
        prefix = {"institutional interpretation": "In its policy assessment, ",
                  "proxy context": "As context on adoption, ",
                  "documentary context": "In the historical release, "}[category]
        context.append(Statement(claim, category, prefix + claim.text if len(prefix + claim.text) <= 600 else None))
    statements = list({(row.claim.text, tuple(row.claim.citations)): row
                       for row in [*facts, *calculations, *context]}.values())
    return {"evidence": [*series.values(), *(source for source, _ in curated)], "statements": statements,
            "limitations": list(dict.fromkeys(limitations))}


def compose_answer(state: State) -> dict:
    evidence = {item.id: item for item in state["evidence"]}
    payload = [{
        "evidence_id": index, "category": item.category, "sentences": item.options(),
        "sources": [evidence[key].model_dump(mode="json") for key in item.claim.citations],
    } for index, item in enumerate(state["statements"], start=1)]
    try:
        return {"draft": v2_gemini.compose(payload), "model_id": configured_model()}
    except Exception:
        return {"limitations": _note(state, "Gemini drafting was unavailable. This summary uses verified statements.")}


def validate_answer(state: State) -> dict:
    statements = state["statements"]
    evidence = {item.id for item in state["evidence"]}
    if (len(evidence) != len(state["evidence"]) or any(
        not set(row.claim.citations) <= evidence for row in statements
    )):
        return {"statements": [], "evidence": [],
                "limitations": _note(state, "The server could not verify the available citations.")}
    fallback = {"narrative": [row.render(row.claim.text) for row in statements], "model_id": None}
    if "draft" not in state:
        return fallback
    try:
        draft = Draft.model_validate(state["draft"])
        ids = [row.evidence_id for row in draft.sentences]
        if sorted(ids) != list(range(1, len(statements) + 1)):
            raise ValueError("Invalid statement inventory")
        groups = {"official observation": 0, "calculated from official observations": 1}
        order = [groups.get(statements[key - 1].category, 2) for key in ids]
        if order != sorted(order):
            raise ValueError("Invalid statement order")
        claims = []
        for sentence in draft.sentences:
            statement = statements[sentence.evidence_id - 1]
            # An exact allowed sentence binds wording, numbers, units and
            # references together. A valid citation alone cannot authorize text.
            if sentence.text not in statement.options():
                raise ValueError("Unverified sentence")
            claims.append(statement.render(sentence.text))
        return {"narrative": claims}
    except ValueError:
        return {**fallback, "limitations": _note(state, "The draft failed evidence validation. This summary uses verified statements.")}


def finish(state: State) -> dict:
    request = state["request"]
    statements = state.get("statements", [])
    narrative = state.get("narrative", [])
    official = state.get("official", [])
    limitations = state["limitations"]
    intent = state.get("intent")
    plans = state.get("plans", [])
    partial = bool(intent and (
        intent.unresolved or intent.purpose == "causal" or len(plans) < len(intent.requirements)
        or len(official) < len(plans) or state.get("missing_context")
    ))
    model_id = state.get("model_id")
    # Keep a single headline per indicator. Multiple comparisons remain in
    # their individually dated statements.
    movements: dict[IndicatorId, ObservedChange] = {}
    if narrative:
        for result in official:
            if result.movement:
                movements.setdefault(result.movement.indicator_id, result.movement)
    return {"response": ResearchV2Response(
        status="answered" if narrative else "insufficient_evidence",
        support=("partial" if partial else "supported") if narrative else "insufficient",
        question=request.question, start=request.start, end=request.end, publication_cutoff=state["cutoff"],
        indicator_id=request.indicator_id, event_id=request.event_id or state.get("event"),
        graph_steps=[*state["steps"], "finish"], limitations=limitations, evidence=state.get("evidence", []),
        corpus_sha256=state.get("corpus_sha256"),
        facts=[row.claim for row in statements if row.category == "official observation"],
        calculations=[row.claim for row in statements if row.category == "calculated from official observations"],
        interpretation=[row.claim for row in statements if row.category not in {
            "official observation", "calculated from official observations",
        }],
        narrative=narrative, narrative_kind=("gemini" if model_id else "deterministic") if narrative else None,
        model_id=model_id, observed_changes=list(movements.values()),
    )}


def _traced(node: Callable[[State], dict]) -> Callable[[State], dict]:
    @wraps(node)
    def record(state: State) -> dict:
        return {**node(state), "steps": [*state["steps"], node.__name__]}
    return record


@lru_cache(maxsize=1)
def workflow():
    graph = StateGraph(State)
    for node in (interpret_with_llm, validate_scope, read_official_statistics, retrieve_curated_evidence,
                 evaluate_support, compose_answer, validate_answer, finish):
        graph.add_node(node.__name__, _traced(node))
    graph.add_edge(START, "interpret_with_llm")
    graph.add_conditional_edges("interpret_with_llm", lambda state: "validate_scope" if state.get("intent") else "finish",
                                ["validate_scope", "finish"])
    graph.add_conditional_edges("validate_scope", lambda state: "read_official_statistics" if state.get("plans") else "finish",
                                ["read_official_statistics", "finish"])
    graph.add_edge("read_official_statistics", "retrieve_curated_evidence")
    graph.add_edge("retrieve_curated_evidence", "evaluate_support")
    graph.add_conditional_edges("evaluate_support", lambda state: "compose_answer" if state["statements"] else "finish",
                                ["compose_answer", "finish"])
    graph.add_edge("compose_answer", "validate_answer")
    graph.add_edge("validate_answer", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


def run(request: ResearchRequest) -> ResearchV2Response:
    cutoff = min(request.end, datetime.now(timezone.utc).date())
    with tracing_context(enabled=False):
        return workflow().invoke({"request": request, "steps": [], "limitations": [], "cutoff": cutoff},
                                 config={"recursion_limit": 10})["response"]
