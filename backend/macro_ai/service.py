"""A bounded LangGraph research workflow with explicit evidence gates."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
from typing import NamedTuple, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from ..macro_data.api import MacroHistory
from .gemini import invoke_gemini
from .models import Claim, Evidence, ResearchRequest, ResearchResponse, selected_citations
from .retrieval import CorpusIndex, document_evidence, tokens
from .statistics import Summary, read_statistics, summarize


_LOGGER = logging.getLogger(__name__)


class Plan(NamedTuple):
    label: str
    topics: tuple[str, ...]
    indicators: tuple[str, ...]


class State(TypedDict, total=False):
    request: ResearchRequest
    plan: Plan | None
    indicator_ids: tuple[str, ...]
    summary: Summary
    documents: list[Document]
    evidence: list[Evidence]
    interpretation: list[Claim]
    issue: str | None
    sufficient: bool
    result: ResearchResponse
    steps: list[str]


class WorkflowFailure(RuntimeError):
    def __init__(self, steps: list[str]):
        super().__init__("macro research unavailable")
        self.steps = steps


def _interpret(question: str) -> Plan | None:
    terms = tokens(question)
    words = set(terms)
    phrase = f" {' '.join(terms)} "
    covid = bool(words & {"covid", "pandemia", "pandemic", "coronavirus"})
    inflation = bool(words & {"inflacion", "inflation", "hicp", "iapc"}
                     or " consumer prices " in phrase or " precios al consumidor " in phrase
                     or " consumer price " in phrase)
    if ({"eur", "usd"} <= words or "eurusd" in words
            or ("dolar" in words and "euro" in words) or ("dollar" in words and "euro" in words)):
        return Plan("Evolución del EUR/USD", ("fx", "covid") if covid else ("fx",), ("eur-usd",))
    if ("m3" in words or any(alias in phrase for alias in (
        " oferta monetaria ", " masa monetaria ", " agregado monetario ", " money supply ", " broad money ",
    ))):
        return Plan("Relación descriptiva del saldo de M3 y el IAPC", ("money", "inflation"),
                    ("m3-stock", "hicp-yoy")) if inflation else Plan(
            "Evolución del saldo de M3", ("money",), ("m3-stock",),
        )
    if words & {"pib", "gdp"} or " gross domestic product " in phrase or " producto interno bruto " in phrase:
        return Plan("Crecimiento intertrimestral del PIB real", ("gdp", "covid") if covid else ("gdp",),
                    ("real-gdp-qoq",))
    if inflation:
        topics = ("inflation", "ukraine") if words & {"ucrania", "ukraine", "guerra", "war"} else ("inflation",)
        return Plan("Evolución de la inflación general IAPC", topics, ("hicp-yoy",))
    if (words & {"deposito", "deposit", "tasa", "tasas", "rates"}
            or words & {"interes", "interest", "ecb", "bce"} and words & {"tipo", "tipos", "rate"}):
        return Plan("Evolución de la tasa de facilidad de depósito", ("rates",), ("deposit-rate",))
    return None


def _step(state: State, name: str) -> list[str]:
    return [*state.get("steps", []), name]


def _render_interpretation(
    selections: list[tuple[str, ...]], evidence: list[Evidence], documents: list[Document],
) -> list[Claim]:
    by_id = {item.id: item for item in evidence}
    summaries = {document.id: document.page_content for document in documents}
    claims = []
    seen = set()
    for selection in selections:
        series = [by_id[citation] for citation in selection if by_id[citation].kind == "series"]
        names = ", ".join(item.title for item in series)
        heading = f"Contexto institucional para {names}. "
        for citation in selection:
            if citation not in summaries:
                continue
            references = (*[item.id for item in series], citation)
            if references in seen:
                continue
            seen.add(references)
            summary = ""
            for sentence in re.split(r"(?<=[.!?])\s+", summaries[citation]):
                if not sentence.endswith((".", "!", "?")):
                    break
                combined = f"{summary} {sentence}".strip()
                if len(heading) + len(combined) > 600:
                    break
                summary = combined
            if not summary:
                source = by_id[citation]
                summary = f"Documento seleccionado publicado el {source.published_on} por la fuente indicada."
            claims.append(Claim(text=heading + summary, citations=list(references)))
    return claims


class Workflow:
    def __init__(
        self, corpus: CorpusIndex,
        read: Callable[[tuple[str, ...], ResearchRequest], dict[str, MacroHistory]] = read_statistics,
        generate: Callable[[str, str], object] = invoke_gemini,
    ):
        self.corpus, self.read, self.generate = corpus, read, generate
        builder = StateGraph(State)
        builder.add_node("interpret", self._interpret)
        builder.add_node("select_indicators", self._select)
        builder.add_node("read_statistics", self._read)
        builder.add_node("retrieve_documents", self._retrieve)
        builder.add_node("assess_evidence", self._assess)
        builder.add_node("generate", self._generate)
        builder.add_node("finish", self._finish)
        builder.add_node("abstain", self._abstain)
        builder.add_node("handle_error", self._error)
        builder.add_edge(START, "interpret")
        builder.add_edge("interpret", "select_indicators")
        builder.add_conditional_edges("select_indicators", lambda s: "read_statistics" if s["indicator_ids"] else "abstain",
                                      ["read_statistics", "abstain"])
        builder.add_conditional_edges("read_statistics", lambda s: "handle_error" if s.get("issue") else "retrieve_documents",
                                      ["handle_error", "retrieve_documents"])
        builder.add_conditional_edges("retrieve_documents", lambda s: "handle_error" if s.get("issue") else "assess_evidence",
                                      ["handle_error", "assess_evidence"])
        builder.add_conditional_edges("assess_evidence", lambda s: "generate" if s["sufficient"] else "abstain",
                                      ["generate", "abstain"])
        builder.add_conditional_edges("generate", lambda s: "handle_error" if s.get("issue") else "finish",
                                      ["handle_error", "finish"])
        builder.add_edge("finish", END)
        builder.add_edge("abstain", END)
        builder.add_edge("handle_error", END)
        self.graph = builder.compile()

    def run(self, request: ResearchRequest) -> ResearchResponse:
        with tracing_context(enabled=False):
            final = self.graph.invoke({"request": request}, config={"recursion_limit": 12})
        if final.get("issue") or "result" not in final:
            _LOGGER.warning("Macro research failed (%s)", final.get("issue") or "missing_result")
            raise WorkflowFailure(final.get("steps", []))
        return final["result"]

    def _interpret(self, state: State) -> State:
        return {"plan": _interpret(state["request"].question), "steps": _step(state, "interpret")}

    def _select(self, state: State) -> State:
        plan = state["plan"]
        return {"indicator_ids": plan.indicators if plan else (), "steps": _step(state, "select_indicators")}

    def _read(self, state: State) -> State:
        steps = _step(state, "read_statistics")
        try:
            histories = self.read(state["indicator_ids"], state["request"])
            return {"summary": summarize(histories, state["indicator_ids"], state["request"]), "steps": steps}
        except Exception:
            return {"issue": "statistics_unavailable", "steps": steps}

    def _retrieve(self, state: State) -> State:
        steps = _step(state, "retrieve_documents")
        plan, request = state["plan"], state["request"]
        try:
            documents = self.corpus.retrieve(request.question, plan.topics, request.end)
            return {"documents": documents, "steps": steps}
        except Exception:
            return {"issue": "retrieval_unavailable", "steps": steps}

    def _assess(self, state: State) -> State:
        summary, documents = state["summary"], state["documents"]
        evidence = summary.evidence + [document_evidence(chunk) for chunk in documents]
        return {"evidence": evidence, "sufficient": summary.complete and bool(documents),
                "steps": _step(state, "assess_evidence")}

    def _generate(self, state: State) -> State:
        steps = _step(state, "generate")
        request, plan, summary = state["request"], state["plan"], state["summary"]
        # Send only canonical intent, public-statistic summaries and approved
        # corpus summaries. The original question remains local.
        prompt_question = f"{plan.label}, entre {request.start} y {request.end}"
        context = {
            "statistics": [claim.model_dump() for claim in (*summary.facts, *summary.calculations)],
            "documents": [{"id": chunk.id, "title": chunk.metadata["title"], "summary": chunk.page_content[:1100]}
                          for chunk in state["documents"]],
        }
        try:
            output = self.generate(prompt_question, json.dumps(context, ensure_ascii=False))
        except Exception:
            return {"issue": "provider_unavailable", "steps": steps}
        try:
            selections = selected_citations(output, state["evidence"])
            interpretation = _render_interpretation(selections, state["evidence"], state["documents"])
            return {"interpretation": interpretation, "steps": steps}
        except Exception:
            return {"issue": "draft_rejected", "steps": steps}

    def _finish(self, state: State) -> State:
        request, summary = state["request"], state["summary"]
        steps = _step(state, "finish")
        return {"result": ResearchResponse(
            status="answered", question=request.question, start=request.start, end=request.end,
            facts=summary.facts, calculations=summary.calculations, interpretation=state["interpretation"],
            limitations=summary.limitations, evidence=state["evidence"], graph_steps=steps,
        ), "steps": steps}

    def _abstain(self, state: State) -> State:
        request = state["request"]
        summary = state.get("summary")
        steps = _step(state, "abstain")
        message = ("La pregunta queda fuera del catálogo de indicadores." if state.get("plan") is None
                   else "La ventana requiere más observaciones válidas o contexto documental verificable.")
        return {"result": ResearchResponse(
            status="insufficient_evidence", question=request.question, start=request.start, end=request.end,
            facts=summary.facts if summary else [], calculations=summary.calculations if summary else [],
            limitations=[message, *(summary.limitations if summary else [])],
            evidence=state.get("evidence", []), graph_steps=steps,
        ), "steps": steps}

    def _error(self, state: State) -> State:
        return {"steps": _step(state, "handle_error")}


@lru_cache(maxsize=1)
def workflow() -> Workflow:
    return Workflow(CorpusIndex.load(Path(__file__).with_name("corpus.json")))
