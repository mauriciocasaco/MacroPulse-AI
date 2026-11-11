"""Extractive narrative grounding: every model sentence has a cited source."""
from __future__ import annotations

from .models import Claim, GeminiDraft

# These transitions add no empirical or causal claim. Everything else must be
# copied in full from the supplied, citation-local approved statements.
TRANSITIONS = ("In this period, ", "For context, ", "The evidence also shows: ", "Separately, ")

def validate_narrative(output: object, statements: list[Claim], required: list[Claim]) -> list[Claim]:
    draft = output if isinstance(output, GeminiDraft) else GeminiDraft.model_validate(output)
    supported = sorted(statements, key=lambda item: len(item.text), reverse=True)
    covered: set[str] = set()
    used_kinds: set[str] = set()
    for claim in draft.narrative:
        if len(set(claim.citations)) != len(claim.citations):
            raise ValueError("duplicate narrative citation")
        remaining = claim.text
        used: set[str] = set()
        while remaining:
            remaining = remaining.lstrip()
            transition = next((item for item in TRANSITIONS if remaining.startswith(item)), None)
            if transition:
                remaining = remaining[len(transition):]
            source = next((item for item in supported if remaining.startswith(item.text)
                           and set(item.citations) <= set(claim.citations)), None)
            if source is None:
                raise ValueError("narrative contains an unsupported statement")
            covered.add(source.text)
            used.update(source.citations)
            remaining = remaining[len(source.text):]
        if used != set(claim.citations):
            raise ValueError("narrative citation is not used by its statement")
        used_kinds.update(citation.split(':', 1)[0] for citation in used)
    if not all(item.text in covered for item in required) or used_kinds != {"series", "doc"}:
        raise ValueError("narrative omits required statistics or institutional context")
    return draft.narrative


def document_statements(documents) -> list[Claim]:
    statements = []
    for document in documents:
        # The corpus consists of reviewed English summaries; never silently
        # treat an incomplete chunk tail as a complete supported proposition.
        sentences = document.metadata.get("approved_sentences", [])
        for sentence in sentences:
            if len(sentence) >= 10 and sentence.endswith((".", "!", "?")) and sentence in document.page_content:
                statements.append(Claim(text=f"Published {document.metadata['published_on']}: {sentence}",
                                        citations=[document.id]))
    return statements
