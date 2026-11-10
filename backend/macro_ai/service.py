"""A bounded LangGraph research workflow with explicit evidence gates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
from typing import NamedTuple, TypedDict
import unicodedata

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from ..macro_data.api import MacroHistory
from .gemini import invoke_gemini
from .models import Claim, Evidence, ResearchRequest, ResearchResponse, selected_citations
from .retrieval import CorpusIndex, document_evidence, tokens
from .statistics import Summary, read_statistics, summarize


_LOGGER = logging.getLogger(__name__)

_HISTORICAL_QUESTIONS = re.compile(
    r"How did (?P<event>the COVID-19 pandemic|the war in Ukraine|the rise of generative AI and LLMs) "
    r"affect (?P<indicator>[^?]+)\??", re.IGNORECASE,
)
_EVENTS = {
    "covid": (date(2020, 1, 1), "COVID-19"),
    "ukraine": (date(2022, 2, 24), "guerra de Ucrania"),
    "generative-ai": (date(2022, 11, 30), "auge de IA generativa y LLMs"),
}


def _historical_event(question: str) -> str | None:
    words = set(tokens(question))
    if words & {"covid", "pandemia", "pandemic", "coronavirus"}:
        return "covid"
    if words & {"ukraine", "ucrania"}:
        return "ukraine"
    if "generative" in words or "generativa" in words or words & {"llm", "llms"}:
        return "generative-ai"
    return None

_EURO_AREA = re.compile(r"\b(?:euro (?:area|zone)|eurozona|eurozone|zona (?:del )?euro|ea21)\b")
_GDP = re.compile(r"\b(?:gross domestic product|producto (?:interno|interior) bruto|pib|gdp)\b")
_INFLATION = re.compile(r"\b(?:consumer prices?|precios al consumidor|inflacion|inflation|hicp|iapc)\b")
_M3 = re.compile(r"\b(?:oferta monetaria|masa monetaria|agregado monetario|money supply|broad money|m3)\b")
_RATES = re.compile(
    r"\b(?:tasas?|tipos?) (?:de (?:la )?facilidad de deposito|de deposito|de interes|"
    r"de politica monetaria|oficial(?:es)?|del (?:bce|ecb))\b|"
    r"\b(?:(?:(?:ecb|bce) )?deposit (?:facility(?: rates?)?|rates?)|"
    r"(?:interest|policy|official|ecb) rates?|dfr)\b",
)
_ECB_NAMES = re.compile(r"\b(?:european central bank|banco central europeo)\b")
_ECB_COUNCIL = re.compile(
    r"\b(?:(?:ecb|bce) governing council|consejo de gobierno del (?:ecb|bce))\b",
)
_RATE_SCOPES = frozenset({"bce", "ecb", "euroarea"})
_RATE_SCOPE_PRONOUNS = frozenset({"it", "its", "su", "sus"})
_BEFORE_INDICATOR = frozenset(
    "el la los las the of de del and with y con entre between did does do was is were are has had have "
    "about sobre in en for compare compara comparen comparar changed change cambio cambiaron "
    "movio movieron evolucion evoluciono evolucionaron happened ocurrio ocurria paso afecta afecto "
    "show muestra moved grew fell rose track describe explica cuanto como what how".split()
)
_AFTER_INDICATOR = frozenset(
    "and with y con after before following during since through until amid against "
    "tras durante despues antes desde hasta frente evoluciono evolucionaron cambiaron cambio "
    "change changed move moved grow grew recover recovered fall fell rise rose show "
    "increase increased decrease decreased cause affected affect aumento crecio cayo subio bajo".split()
)
_RATE_QUERY_WORDS = _BEFORE_INDICATOR | _AFTER_INDICATOR | _RATE_SCOPES | _RATE_SCOPE_PRONOUNS | frozenset(
    "a an to by por un una se there be been being will when why whether if could would might or o "
    "que cuando explicame explain tell me please you ever again recently already gradually rapidly "
    "high highest lowest level levels much done hizo "
    "raise raised hike hiked cut lower lowered reduce reduced set kept hold adjust adjusted update updated "
    "freeze froze maintain maintained decide decided cambia sube aumenta baja reduce redujo recorta recorto "
    "fija fijo mantiene mantuvo sostener sostuvo ajusta ajusto modifica modifico decide decidio "
    "announcement meeting decision anuncio reunion comunicado policy monetary politica monetaria "
    "pandemic pandemia covid crisis financial financiera "
    "first second third fourth primer segundo tercer cuarto quarter trimestre year years ano anos".split()
)
_SCOPE_COMPLEMENT = re.compile(
    r"(?:the |la |el )?(?:euroarea|ecb|bce|(?:19|20)\d{2}|pandemic|pandemia|covid|"
    r"(?:first|second|third|fourth) quarter|(?:primer|segundo|tercer|cuarto) trimestre)\b"
)


class Plan(NamedTuple):
    label: str
    topics: tuple[str, ...]
    indicators: tuple[str, ...]
    event: str | None = None


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
    abstention_reason: str
    result: ResearchResponse
    steps: list[str]


class WorkflowFailure(RuntimeError):
    def __init__(self, steps: list[str]):
        super().__init__("macro research unavailable")
        self.steps = steps


def _scope_supported(
    phrase: str, aliases: re.Pattern[str], *, prefix: frozenset[str] = frozenset(),
    suffix: frozenset[str] = frozenset(),
) -> bool:
    # Recognize adjacent qualifiers in the supported bilingual grammar.
    # Unknown qualifiers cannot silently select the euro-area aggregate.
    for match in aliases.finditer(phrase):
        left = phrase[:match.start()].split()
        while left and left[-1] in prefix | {"euroarea"}:
            left.pop()
        if left and left[-1] not in _BEFORE_INDICATOR:
            return False
        right = phrase[match.end():].split()
        while right and right[0] in suffix | {"euroarea"}:
            right.pop(0)
        if right and right[0] in {"change", "grow", "move", "recover", "cambio", "crecio", "evoluciono"}:
            right.pop(0)
        if not right:
            continue
        if right[0] in {"of", "de", "del", "in", "en", "for", "from", "between", "entre"}:
            if not _SCOPE_COMPLEMENT.match(" ".join(right[1:])):
                return False
        elif right[0] not in _AFTER_INDICATOR and not re.fullmatch(r"(?:19|20)\d{2}", right[0]):
            return False
    return True


def _rate_phrase(question: str) -> str:
    # Preserve unknown words, including single-letter and non-Latin names.
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", question.casefold()) if not unicodedata.combining(char)
    )
    folded = re.sub(r"(?<=\w)['’]s\b", "", folded)
    # Full ISO references are already checked against the request window.
    folded = re.sub(r"\b((?:19|20)\d{2})-\d{2}-\d{2}\b", r"\1", folded)
    phrase = " ".join(re.findall(r"[^\W_]+", folded))
    return _ECB_COUNCIL.sub("ecb", _ECB_NAMES.sub("ecb", _EURO_AREA.sub("euroarea", phrase)))


def _rate_query_supported(phrase: str) -> bool:
    # Every word outside recognized rate aliases must have a supported role
    # anywhere in the question. An unfamiliar actor or scope fails closed.
    residual = set(_RATES.sub(" ", phrase).split())
    if residual & _RATE_SCOPE_PRONOUNS and not _RATE_SCOPES.intersection(phrase.split()):
        return False
    return all(word in _RATE_QUERY_WORDS or re.fullmatch(r"(?:19|20)\d{2}", word) for word in residual)


def _dates_fit(request: ResearchRequest) -> bool:
    historical_crisis = re.compile(
        r"\b(?:after|following|tras|despu[eé]s de) (?:(?:the|la) )?"
        r"(?:(?P<before>(?:19|20)\d{2}) (?:financial )?crisis|"
        r"(?:financial )?crisis(?: financiera)? (?:of |de )?(?P<after>(?:19|20)\d{2}))\b",
        flags=re.IGNORECASE,
    )
    question = historical_crisis.sub(
        lambda match: "" if int(match["before"] or match["after"]) < request.start.year else match[0],
        request.question,
    )
    for reference in re.findall(r"\b(?:19|20)\d{2}(?:-\d{2}-\d{2})?\b", question):
        if len(reference) == 4:
            if not request.start.year <= int(reference) <= request.end.year:
                return False
        else:
            try:
                day = date.fromisoformat(reference)
            except ValueError:
                return False
            if not request.start <= day <= request.end:
                return False
    return True


def _interpret(question: str) -> Plan | None:
    historical = _HISTORICAL_QUESTIONS.fullmatch(question.strip())
    if historical:
        # Only remove the exact event wrapper; the remaining indicator/authority
        # still passes all existing scope checks. UI context never overrides it.
        plan = _interpret(historical['indicator'])
        if plan is None:
            return None
        event = _historical_event(historical['event'])
        return plan._replace(topics=(*plan.topics, event), event=event)
    # Keep dotted country initials as a qualifier instead of dropping their
    # one-letter tokens, while leaving normal sentence punctuation alone.
    normalized = re.sub(r"\b(?:[A-Za-z]\.)+[A-Za-z]\b\.?", lambda match: match[0].replace(".", ""), question)
    normalized = re.sub(r"\by\b", "and", normalized, flags=re.IGNORECASE)
    terms = tokens(normalized)
    words = set(terms)
    phrase = _EURO_AREA.sub("euroarea", f" {' '.join(terms)} ")
    covid = bool(words & {"covid", "pandemia", "pandemic", "coronavirus"})
    inflation = bool(_INFLATION.search(phrase))
    if inflation and (
        words & {"core", "subyacente"} or not _scope_supported(
            phrase, _INFLATION, prefix=frozenset({"headline", "overall", "annual"}),
            suffix=frozenset({"general", "europea", "interanual", "anual"}),
        )
    ):
        return None
    plans = []
    if ({"eur", "usd"} <= words or "eurusd" in words
            or ("dolar" in words and "euro" in words) or ("dollar" in words and "euro" in words)):
        plans.append(Plan("Evolución del EUR/USD", ("fx", "covid") if covid else ("fx",), ("eur-usd",)))
    money = bool(_M3.search(phrase))
    if money and not _scope_supported(
        phrase, _M3, prefix=frozenset({"saldo", "stock"}), suffix=frozenset({"stock"}),
    ):
        return None
    if money and inflation:
        plans.append(Plan("Relación descriptiva del saldo de M3 y el IAPC", ("money", "inflation"),
                          ("m3-stock", "hicp-yoy")))
    elif money:
        plans.append(Plan("Evolución del saldo de M3", ("money",), ("m3-stock",)))
    elif inflation:
        topics = ("inflation", "ukraine") if words & {"ucrania", "ukraine", "guerra", "war"} else ("inflation",)
        plans.append(Plan("Evolución de la inflación general IAPC", topics, ("hicp-yoy",)))
    if _GDP.search(phrase):
        if not _scope_supported(phrase, _GDP, prefix=frozenset({"real"}), suffix=frozenset({"real"})):
            return None
        plans.append(Plan("Crecimiento intertrimestral del PIB real", ("gdp", "covid") if covid else ("gdp",),
                          ("real-gdp-qoq",)))
    rate_phrase = _rate_phrase(normalized)
    if _RATES.search(rate_phrase):
        if not _rate_query_supported(rate_phrase):
            return None
        plans.append(Plan("Evolución de la tasa de facilidad de depósito", ("rates",), ("deposit-rate",)))
    return plans[0] if len(plans) == 1 else None


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
        for citation in selection:
            if citation not in summaries or citation in seen:
                continue
            seen.add(citation)
            source = by_id[citation]
            heading = f"Contexto documental publicado el {source.published_on}. "
            summary = ""
            for sentence in re.split(r"(?<=[.!?])\s+", summaries[citation]):
                if not sentence.endswith((".", "!", "?")):
                    break
                combined = f"{summary} {sentence}".strip()
                if len(heading) + len(combined) > 600:
                    break
                summary = combined
            if not summary:
                summary = "Consulta el documento en la fuente indicada."
            claims.append(Claim(text=heading + summary, citations=[citation]))
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
        request = state["request"]
        steps = _step(state, "interpret")
        if not _dates_fit(request):
            return {"plan": None, "steps": steps,
                    "abstention_reason": "La fecha indicada en la pregunta no coincide con la ventana solicitada."}
        plan = _interpret(request.question)
        event = _historical_event(request.question)
        if plan and event:
            plan = plan._replace(topics=tuple(dict.fromkeys((*plan.topics, event))), event=event)
            if request.end < _EVENTS[event][0]:
                return {"plan": None, "steps": steps,
                        "abstention_reason": "La ventana seleccionada termina antes del evento histórico consultado."}
        return {"plan": plan, "steps": steps}

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
            if plan.event:
                documents = [chunk for chunk in documents
                             if plan.event in chunk.metadata['topics']
                             and chunk.metadata['published_on'] >= _EVENTS[plan.event][0]]
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
        if plan.event:
            prompt_question += f"; contexto histórico: {_EVENTS[plan.event][1]}"
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
            limitations=[*summary.limitations, *self._event_limitations(state)],
            evidence=state["evidence"], graph_steps=steps,
        ), "steps": steps}

    def _abstain(self, state: State) -> State:
        request = state["request"]
        summary = state.get("summary")
        steps = _step(state, "abstain")
        message = state.get("abstention_reason") or (
            "La pregunta queda fuera del catálogo de indicadores." if state.get("plan") is None
            else "La ventana requiere más observaciones válidas o contexto documental verificable."
        )
        return {"result": ResearchResponse(
            status="insufficient_evidence", question=request.question, start=request.start, end=request.end,
            facts=summary.facts if summary else [], calculations=summary.calculations if summary else [],
            limitations=[message, *(summary.limitations if summary else []), *self._event_limitations(state)],
            evidence=state.get("evidence", []), graph_steps=steps,
        ), "steps": steps}

    @staticmethod
    def _event_limitations(state: State) -> list[str]:
        plan = state.get('plan')
        if not plan or not plan.event:
            return []
        onset, label = _EVENTS[plan.event]
        notes = [f"Referencia temporal de {label}: desde {onset}. Se conserva la ventana seleccionada; "
                 "los cambios observados y el contexto documental no identifican un efecto causal."]
        if not state.get('documents'):
            notes.append("El corpus revisado no contiene evidencia documental suficiente para este indicador y evento; "
                         "no se generan hipótesis ni se amplía el corpus automáticamente.")
        return notes

    def _error(self, state: State) -> State:
        return {"steps": _step(state, "handle_error")}


@lru_cache(maxsize=1)
def workflow() -> Workflow:
    return Workflow(CorpusIndex.load(Path(__file__).with_name("corpus.json")))
