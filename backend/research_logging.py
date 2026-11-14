"""Safe timing and error events for the public Research request path."""

from asyncio import CancelledError
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
from time import monotonic


# Use the server's logger configuration so INFO reaches Render under uvicorn.
_logger = logging.getLogger("uvicorn.error.macropulse.research")
research_id: ContextVar[str | None] = ContextVar("public_research_id", default=None)


def research_event(stage: str, started: float, error_type: str | None = None,
                   *, http_status: int | None = None) -> None:
    record = {
        "event": "public_research_stage",
        "component": "render",
        "research_id": research_id.get(),
        "stage": stage,
        "outcome": "error" if error_type else "ok",
        "duration_ms": round((monotonic() - started) * 1000, 2),
    }
    if error_type:
        record["error_type"] = error_type
    if http_status is not None:
        record["http_status"] = http_status
    # Never serialize the exception, its args, or its traceback.
    _logger.log(logging.WARNING if error_type else logging.INFO, "%s", json.dumps(record))


@contextmanager
def research_stage(stage: str, *, startup: bool = False):
    if research_id.get() is None and not startup:
        yield
        return
    started = monotonic()
    try:
        yield
    except (Exception, CancelledError) as error:
        research_event(stage, started, type(error).__name__)
        raise
    else:
        research_event(stage, started)
