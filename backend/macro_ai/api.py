"""Loopback-only HTTP entry point for optional macro research."""

from __future__ import annotations

import asyncio
import os
import re
from threading import BoundedSemaphore

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from ..macro_data.api import _require_local
from .models import ResearchRequest, ResearchResponse


MAX_BODY = 4096
_SLOT = BoundedSemaphore(1)
_LOCAL_ORIGIN = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::([1-9][0-9]{0,4}))?")


class _ResearchRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            _require_local(request)
            if os.environ.get("MARKETPULSE_MACRO_AI_ENABLED") != "true":
                raise HTTPException(status_code=404, detail="Macro research route unavailable")
            origins = request.headers.getlist("origin")
            if origins:
                origin = _LOCAL_ORIGIN.fullmatch(origins[0])
                if len(origins) != 1 or not origin or (origin[1] and int(origin[1]) > 65535):
                    raise HTTPException(status_code=403, detail="Forbidden")
            if request.query_params:
                raise HTTPException(status_code=422, detail="Invalid macro research request")
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise HTTPException(status_code=415, detail="Expected JSON")
            received = 0
            deadline = asyncio.get_running_loop().time() + 3

            async def receive():
                nonlocal received
                try:
                    async with asyncio.timeout_at(deadline):
                        message = await request.receive()
                except TimeoutError:
                    raise HTTPException(status_code=408, detail="Macro research request timed out") from None
                received += len(message.get("body", b""))
                if received > MAX_BODY:
                    raise HTTPException(status_code=413, detail="Macro research request too large")
                return message

            try:
                response = await handler(Request(request.scope, receive))
            except RequestValidationError:
                raise HTTPException(status_code=422, detail="Invalid macro research request") from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return bounded


router = APIRouter(prefix="/macro", route_class=_ResearchRoute)


@router.post("/analysis")
def analysis(request: ResearchRequest) -> ResearchResponse:
    if not _SLOT.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Macro research is busy")
    try:
        # Keep optional dependencies outside application startup and DEMO.
        from .service import workflow
        return workflow().run(request)
    except Exception:
        raise HTTPException(status_code=503, detail="Macro research unavailable") from None
    finally:
        _SLOT.release()
