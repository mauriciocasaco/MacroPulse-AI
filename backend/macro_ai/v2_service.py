"""Bounded Research v2 graph. Only the server authorizes answer statements."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache, wraps
from typing import TypedDict, get_args

import httpx
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from psycopg import OperationalError
from psycopg.errors import QueryCanceled
from pydantic import ValidationError

from ..macro_data.api import MacroHistory, MacroWindow
from ..macro_data.contracts import CATALOG
from ..research_logging import research_stage
from . import v2_gemini, v2_retrieval
from .editorial_presentation import ENGLISH_SUMMARIES
from .models import EVENT_WINDOWS, Claim, EventId, IndicatorId, ObservedChange, ResearchRequest
from .statistics import read_statistics
from .v2_models import (
    Category, CuratedEvidence, Draft, FallbackReason, GeneralDraft, GeneralFallbackAnswer, GeneralFallbackFailure,
    Intent, NarrativeClaim, NarrativeStyle, Requirement, ResearchDiagnostic, ResearchV2Response, SeriesEvidence,
)
from .v2_statistics import DEFINITIONS, SeriesResult, exact_period, matches_definition, summarize_requirement


# Exact templates from QuestionComposer, question-events and macro-chart.
_EVENT_QUESTIONS: dict[EventId, str] = {
    "covid": "How did the COVID-19 pandemic affect",
    "ukraine": "How did the war in Ukraine affect",
    "generative-ai": "How did the rise of generative AI and LLMs affect",
}
_INDICATOR_LABELS: dict[IndicatorId, str] = {
    "eur-usd": "EUR/USD rate",
    "m3-stock": "Eurozone M3",
    "deposit-rate": "ECB rate",
    "real-gdp-qoq": "Eurozone GDP",
    "hicp-yoy": "Eurozone Inflation",
}
_PRESET_QUESTIONS = {
    f"{prefix} {label}?": (indicator, event)
    for event, prefix in {**_EVENT_QUESTIONS, None: "What happened to"}.items()
    for indicator, label in _INDICATOR_LABELS.items()
}


@dataclass(frozen=True)
class Plan:
    indicator: IndicatorId
    window: MacroWindow
    requirement: Requirement


_NARRATIVE_GROUPS: dict[Category, int] = {
    "official observation": 0,
    "documentary context": 1,
    "institutional interpretation": 1,
    "proxy context": 1,
    "calculated from official observations": 2,
}
_DRAFT_LIMITATION = "The draft failed evidence validation. This summary uses verified evidence."
_CAUSAL_LIMITATION = "This analysis does not estimate the event's causal effect."


@dataclass(frozen=True)
class Statement:
    claim: Claim
    category: Category
    prose: tuple[str, str]

    def render(self, style: NarrativeStyle) -> NarrativeClaim:
        text = self.prose[0 if style == "direct" else 1]
        if len(text) > 600:
            text = self.claim.text
        return NarrativeClaim(text=text, citations=self.claim.citations, category=self.category)


@dataclass(frozen=True)
class ResearchRuntime:
    read: Callable[[IndicatorId, MacroWindow], MacroHistory]
    interpret: Callable[[ResearchRequest], Intent]
    compose: Callable[[list[dict]], Draft]
    model_id: str
    supplemental_retriever: Callable[..., list[tuple[CuratedEvidence, Claim]]] | None = None
    capture_vintage: str = "DEMO"
    dynamic_editorial: bool = False
    knowledge_cutoff: date | None = None
    generate_general: Callable[..., GeneralDraft] | None = None


def configured_model() -> str:
    from .gemini import configured_model as local_model

    return local_model()


class State(TypedDict, total=False):
    runtime: ResearchRuntime
    request: ResearchRequest
    steps: list[str]
    limitations: list[str]
    intent: Intent
    event: EventId | None
    cutoff: date
    period_end: date
    plans: list[Plan]
    official: list[SeriesResult]
    curated: list[tuple[CuratedEvidence, Claim]]
    corpus_sha256: str | None
    missing_context: bool
    evidence: list[SeriesEvidence | CuratedEvidence]
    statements: list[Statement]
    draft: Draft
    causal_question: bool
    narrative: list[NarrativeClaim]
    model_id: str | None
    response: ResearchV2Response
    diagnostics: list[ResearchDiagnostic]


# A technical failure takes precedence over gaps in any of the other requirements.
_DIAGNOSTIC_PRIORITY: tuple[ResearchDiagnostic, ...] = (
    "citation_validation_failed", "interpretation_unavailable", "evidence_reader_unavailable",
    "ambiguous_interpretation", "unsupported_operation_or_series", "missing_official_observations",
    "insufficient_documentary_evidence", "unsupported_causal_inference",
)
_FALLBACK_REASONS = frozenset(get_args(FallbackReason))


def _note(state: State, text: str) -> list[str]:
    return list(dict.fromkeys([*state["limitations"], text]))


def _preset_intent(question: str) -> Intent | None:
    preset = _PRESET_QUESTIONS.get(question)
    if preset is None:
        return None
    indicator, event = preset
    definition = DEFINITIONS[indicator]
    return Intent(event=event, purpose="causal" if event else "descriptive", requirements=[
        Requirement(excerpt=question, indicator=indicator, geography=definition.geography[0],
                    measure=definition.measure, operation="trend"),
    ])


def interpret_with_llm(state: State) -> dict:
    if runtime := state.get("runtime"):
        return {"intent": Intent.model_validate(runtime.interpret(state["request"]))}
    try:
        return {"intent": Intent.model_validate(v2_gemini.interpret(state["request"]))}
    except Exception:
        return {"diagnostics": ["interpretation_unavailable"], "limitations": _note(
            state, "Gemini could not interpret this question safely. Review the question and local model configuration.",
        )}


def validate_scope(state: State) -> dict:
    request, intent = state["request"], state["intent"]
    invalid_excerpts = {item.excerpt for item in intent.requirements
                        if not item.excerpt.strip() or item.excerpt not in request.question}
    diagnostics: list[ResearchDiagnostic] = ["interpretation_unavailable"] if invalid_excerpts else []
    if (intent.event and request.event_id and intent.event != request.event_id
            or intent.purpose == "uncertain"):
        return {"diagnostics": [*diagnostics, "ambiguous_interpretation"], "limitations": _note(
            state, "The event or research intent could not be reconciled with the selected scope.",
        )}
    event = intent.event or request.event_id
    start, end = request.start, state["period_end"]
    if event:
        event_start, event_end = EVENT_WINDOWS[event]
        start, end = max(start, event_start), min(end, event_end)
    if end < start:
        return {"event": event, "diagnostics": [*diagnostics, "missing_official_observations"], "limitations": _note(
            state, "The selected period has no available days in the historical scope.",
        )}

    limitations = state["limitations"].copy()
    if intent.unresolved:
        diagnostics.append("ambiguous_interpretation")
        limitations = _note(state, "Part of the question could not be interpreted confidently.")
    plans = []
    for requirement in intent.requirements:
        if requirement.excerpt in invalid_excerpts:
            limitations.append("The question interpreter returned a requirement that could not be verified.")
            continue
        plan = _plan(requirement, request, MacroWindow(start=start, end=end))
        if plan is None:
            diagnostics.append("unsupported_operation_or_series")
            limitations.append("A requirement has an unsupported or ambiguous series definition, operation or period.")
        elif plan not in plans:
            plans.append(plan)
    return {"event": event, "plans": plans, "limitations": list(dict.fromkeys(limitations)),
            "diagnostics": diagnostics}


def _plan(requirement: Requirement, request: ResearchRequest, bounds: MacroWindow) -> Plan | None:
    key = requirement.indicator
    if key is None or request.indicator_id not in {None, key} or not matches_definition(key, requirement):
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


def _read_local_history(key: IndicatorId, window: MacroWindow, question: str) -> MacroHistory | None:
    try:
        request = ResearchRequest(question=question, start=window.start, end=window.end)
        return read_statistics((key,), request)[key]
    except Exception:
        return None


def read_official_statistics(state: State) -> dict:
    runtime = state.get("runtime")
    official = []
    limitations = state["limitations"].copy()
    diagnostics = state["diagnostics"].copy()
    windows = {}
    for plan in state["plans"]:
        if plan.requirement.operation == "causal_effect":
            diagnostics.append("unsupported_causal_inference")
            continue
        window_key = (plan.indicator, plan.window.start, plan.window.end)
        if window_key not in windows:
            windows[window_key] = (runtime.read(plan.indicator, plan.window) if runtime else
                                   _read_local_history(plan.indicator, plan.window, state["request"].question))
        history = windows[window_key]
        if history is None:
            diagnostics.append("evidence_reader_unavailable")
            limitations.append("The local official data reader was unavailable for a requirement.")
            continue
        try:
            with research_stage("langgraph.summarize_requirement"):
                result = summarize_requirement(history, plan.indicator, plan.window, plan.requirement)
        except (ArithmeticError, ValueError):
            diagnostics.append("evidence_reader_unavailable")
            limitations.append("The server could not validate or calculate the official observations for a requirement.")
            continue
        if result:
            official.append(result)
        else:
            diagnostics.append("missing_official_observations")
            limitations.append("The required official observations are missing or not comparable.")
    return {"official": official, "limitations": list(dict.fromkeys(limitations)), "diagnostics": diagnostics}


def retrieve_curated_evidence(state: State) -> dict:
    curated = {}
    limitations = state["limitations"].copy()
    diagnostics = state["diagnostics"].copy()
    event = state.get("event")
    fingerprint = None
    runtime = state.get("runtime")
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
                arguments = (state["request"].question, event, plan.indicator, plan.window, state["cutoff"])
                dynamic = runtime and runtime.dynamic_editorial
                results = []
                if dynamic and runtime.supplemental_retriever:
                    try:
                        results = runtime.supplemental_retriever(*arguments)
                    except (OperationalError, QueryCanceled, TimeoutError):
                        diagnostics.append("evidence_reader_unavailable")
                        limitations.append("Live editorial retrieval was unavailable.")
                if not any(source.claim_version for source, _ in results):
                    fallback = (v2_retrieval.retrieve(*arguments, strategy="tfidf") if runtime
                                else v2_retrieval.retrieve(*arguments))
                    if fallback:
                        fingerprint = v2_retrieval.APPROVED_SNAPSHOT
                        results += fallback
                if runtime and not dynamic and runtime.supplemental_retriever:
                    results += runtime.supplemental_retriever(*arguments)
            except Exception:
                if "runtime" in state:
                    raise
                missing_context = True
                diagnostics.append("evidence_reader_unavailable")
                limitations.append("Approved documentary retrieval was unavailable for a requirement.")
                continue
            if results:
                for source, claim in results:
                    curated[source.id] = (source, claim)
            else:
                missing_context = True
                diagnostics.append("insufficient_documentary_evidence")
                limitations.append(f"The approved corpus has no applicable documentary context for {CATALOG[plan.indicator].title}.")
    # Four requirements can each contribute two official statements. Leave
    # room for up to eight documentary statements in the bounded draft.
    return {"curated": list(curated.values())[:8], "limitations": list(dict.fromkeys(limitations)),
            "corpus_sha256": fingerprint, "missing_context": missing_context, "diagnostics": diagnostics}


def evaluate_support(state: State) -> dict:
    official = state["official"]
    curated = state["curated"]
    limitations = state["limitations"].copy()
    causal_question = state["intent"].purpose == "causal" or any(
        plan.requirement.operation == "causal_effect" for plan in state["plans"]
    )
    runtime = state.get("runtime")
    if causal_question or (curated and runtime and runtime.dynamic_editorial):
        limitations.append("The available evidence does not identify the event's causal effect or quantify how much it changed the indicator.")
    if state.get("event") == "generative-ai" and any(plan.indicator == "real-gdp-qoq" for plan in state["plans"]):
        limitations.append("The approved corpus has no direct evidence of a generative AI effect on euro-area GDP. EU adoption evidence is proxy context.")
    if official:
        vintage = runtime.capture_vintage if runtime else "local"
        limitations.append(f"Official observations use the {vintage} capture vintage."
                           + ("" if runtime and runtime.knowledge_cutoff else
                              " Historical availability can differ because of later revisions."))
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
    dynamic = runtime and runtime.dynamic_editorial
    historical_gdp = dynamic and any(
        source.claim_version and source.relation == "direct" and source.measure == "real_gdp_qoq"
        for source, _ in curated
    )
    facts = []
    for result in official:
        prose = result.observation_prose
        if historical_gdp and result.evidence.id == "series:real-gdp-qoq":
            captured = result.evidence.updated_at
            vintage = f" captured on {captured.date().isoformat()}" if captured else ""
            series_label = "Eurostat EA21 series" if runtime.knowledge_cutoff else "Revised Eurostat EA21 series"
            intro = f"{series_label}{vintage}."
            prose = (f"{intro} {prose[0]}", f"{intro} {prose[1]}")
        facts.append(Statement(result.fact, "official observation", prose))
    calculations = [Statement(result.calculation, "calculated from official observations", result.calculation_prose)
                    for result in official if result.calculation and result.calculation_prose]
    context = []
    for source, claim in curated:
        category = ("proxy context" if source.relation == "proxy"
                    else "institutional interpretation" if source.claim_type == "institutional_interpretation"
                    else "documentary context")
        text = claim.text
        if dynamic and source.claim_version:
            text = ENGLISH_SUMMARIES.get(source.claim_version) or (
                f"See the cited {source.attribution} publication for the approved statement "
                "in its original language."
            )
            if source.relation == "direct" and source.measure == "real_gdp_qoq":
                text = f"Historical release estimate. {text}"
        bridge = ("The documentary evidence provides indirect context." if source.relation == "proxy"
                  else "The institutional record adds context.")
        context.append(Statement(claim, category, (text, f"{bridge} {text}")))
    statements = list({(row.claim.text, tuple(row.claim.citations)): row
                       for row in [*facts, *context, *calculations]}.values())
    return {"evidence": [*series.values(), *(source for source, _ in curated)], "statements": statements,
            "causal_question": causal_question,
            "limitations": list(dict.fromkeys(limitations))}


def compose_answer(state: State) -> dict:
    payload = [{
        "statement_id": index, "category": item.category, "statement": item.claim.text,
    } for index, item in enumerate(state["statements"], start=1)]
    runtime = state.get("runtime")
    try:
        if runtime:
            with research_stage("langgraph.compose_draft"):
                return {"draft": runtime.compose(payload), "model_id": runtime.model_id}
        return {"draft": v2_gemini.compose(payload), "model_id": configured_model()}
    except ValidationError:
        return {"limitations": _note(state, _DRAFT_LIMITATION)}
    except (TimeoutError, httpx.HTTPError, ValueError, LookupError, TypeError):
        # The server already owns the checked facts and citations. A transient
        # provider failure or malformed provider output must not turn those
        # facts into a 503/504; validate_answer safely renders them itself.
        # Research stage logging records the actual provider failure upstream.
        return {"limitations": _note(state, "Gemini drafting was unavailable. This summary uses verified evidence.")}
    except Exception:
        # Unknown public runtime defects must still surface; do not mask them.
        if runtime:
            raise
        return {"limitations": _note(state, "Gemini drafting was unavailable. This summary uses verified evidence.")}


def validate_answer(state: State) -> dict:
    statements = state["statements"]
    evidence = {item.id for item in state["evidence"]}
    if (len(evidence) != len(state["evidence"]) or any(
        not row.claim.citations or not set(row.claim.citations) <= evidence for row in statements
    )):
        return {"statements": [], "evidence": [], "model_id": None,
                "diagnostics": [*state.get("diagnostics", []), "citation_validation_failed"],
                "limitations": _note(state, "The server could not verify the available citations.")}

    # Even well-formed model output can only select server-owned realizations.
    # Each paragraph keeps its category and citations from the same statement.
    limitations = state["limitations"]
    narrative = []
    model_id = None
    if "draft" in state:
        try:
            draft = Draft.model_validate(state["draft"])
            ids = [paragraph.statement_id for paragraph in draft.paragraphs]
            if sorted(ids) != list(range(1, len(statements) + 1)):
                raise ValueError("Invalid statement inventory")
            ordered = [statements[key - 1] for key in ids]
            groups = [_NARRATIVE_GROUPS[row.category] for row in ordered]
            if groups != sorted(groups):
                raise ValueError("Invalid statement order")
            for index, (paragraph, statement) in enumerate(zip(draft.paragraphs, ordered, strict=True)):
                if paragraph.style == "framed" and index and groups[index] == groups[index - 1]:
                    raise ValueError("Repeated frame")
                narrative.append(statement.render(paragraph.style))
            model_id = state.get("model_id")
        except ValueError:
            narrative = []
            limitations = _note(state, _DRAFT_LIMITATION)

    if not narrative:
        previous = None
        for statement in statements:
            group = _NARRATIVE_GROUPS[statement.category]
            narrative.append(statement.render("framed" if group != previous else "direct"))
            previous = group

    if narrative and state.get("causal_question"):
        closing = narrative[-1]
        text = f"{closing.text} {_CAUSAL_LIMITATION}"
        if len(text) <= 600:
            narrative[-1] = closing.model_copy(update={"text": text})
    return {"limitations": limitations, "narrative": narrative, "model_id": model_id}


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
    diagnostics = state["diagnostics"]
    reason = None if narrative else next(
        (code for code in _DIAGNOSTIC_PRIORITY if code in diagnostics), "no_verified_statements",
    )
    # Keep a single headline per indicator. Multiple comparisons remain in
    # their individually dated statements.
    movements: dict[IndicatorId, ObservedChange] = {}
    if narrative:
        for result in official:
            if result.movement:
                movements.setdefault(result.movement.indicator_id, result.movement)
    return {"response": ResearchV2Response(
        status="answered" if narrative else "insufficient_evidence",
        insufficiency_reason=reason,
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


def _after_finish(state: State) -> str:
    runtime = state.get("runtime")
    response = state["response"]
    if (runtime and runtime.generate_general is not None and response.status == "insufficient_evidence"
            and response.insufficiency_reason in _FALLBACK_REASONS):
        return "generate_general_fallback"
    return END


def generate_general_fallback(state: State) -> dict:
    runtime = state["runtime"]
    response = state["response"]
    metadata = {"reason": response.insufficiency_reason, "model_id": runtime.model_id}
    try:
        with research_stage("langgraph.general_fallback_attempt"):
            draft = GeneralDraft.model_validate(runtime.generate_general(
                state["request"], response.insufficiency_reason, event=state.get("event"),
                knowledge_cutoff=runtime.knowledge_cutoff,
            ))
            fallback = GeneralFallbackAnswer(**metadata, answer=draft.answer)
    except TimeoutError:
        fallback = GeneralFallbackFailure(**metadata, error="timeout")
    except httpx.HTTPError:
        fallback = GeneralFallbackFailure(**metadata, error="provider_unavailable")
    except (ValueError, LookupError, TypeError, AttributeError):
        fallback = GeneralFallbackFailure(**metadata, error="invalid_response")
    return {"response": response.model_copy(update={
        "general_fallback": fallback, "graph_steps": [*response.graph_steps, "generate_general_fallback"],
    })}


def _traced(node: Callable[[State], dict]) -> Callable[[State], dict]:
    @wraps(node)
    def record(state: State) -> dict:
        with research_stage(f"langgraph.{node.__name__}"):
            return {**node(state), "steps": [*state["steps"], node.__name__]}
    return record


@lru_cache(maxsize=1)
def workflow():
    graph = StateGraph(State)
    for node in (interpret_with_llm, validate_scope, read_official_statistics, retrieve_curated_evidence,
                 evaluate_support, compose_answer, validate_answer, finish, generate_general_fallback):
        graph.add_node(node.__name__, _traced(node))
    graph.add_conditional_edges(START, lambda state: "validate_scope" if state.get("intent") else "interpret_with_llm",
                                ["validate_scope", "interpret_with_llm"])
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
    graph.add_conditional_edges("finish", _after_finish, ["generate_general_fallback", END])
    graph.add_edge("generate_general_fallback", END)
    return graph.compile()


def run(request: ResearchRequest, *, runtime: ResearchRuntime | None = None) -> ResearchV2Response:
    today = datetime.now(timezone.utc).date()
    cutoff = min(request.end, today)
    if runtime and runtime.dynamic_editorial:
        cutoff = min(runtime.knowledge_cutoff or today, today)
    state: State = {"request": request, "steps": [], "limitations": [], "diagnostics": [], "cutoff": cutoff,
                    "period_end": min(request.end, cutoff)}
    if intent := _preset_intent(request.question):
        state.update(intent=intent, steps=["interpret_preset"])
    if runtime is not None:
        state["runtime"] = runtime
    with tracing_context(enabled=False), research_stage("langgraph.invoke"):
        return workflow().invoke(state, config={"recursion_limit": 10})["response"]
