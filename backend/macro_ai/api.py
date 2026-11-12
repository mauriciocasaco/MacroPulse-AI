"""Loopback-only HTTP entry point for optional macro research."""

from __future__ import annotations

import asyncio
from hmac import compare_digest
from ipaddress import IPv4Address
import os
import re
from threading import BoundedSemaphore
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from ..macro_data.api import _require_local
from .config import research_v2_enabled
from .models import ResearchRequest, ResearchResponse
from .v2_models import ResearchV2Response


MAX_BODY = 4096
_SLOT = BoundedSemaphore(1)
_LOCAL_ORIGIN = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::([1-9][0-9]{0,4}))?")
_LAN_AUTH = "x-marketpulse-lan-auth"
_LAN_TOKEN = re.compile(r"[a-f0-9]{64}")


def _authorized_lan_origin(origin: str, credential: str) -> bool:
    if os.environ.get("MARKETPULSE_MODE", "local") != "local" or os.environ.get("MARKETPULSE_LAN_DEV") != "true":
        return False
    ip = os.environ.get("MARKETPULSE_LAN_IP", "")
    try:
        IPv4Address(ip)
    except ValueError:
        return False
    if (not re.match(r"(?:10\.|172\.(?:1[6-9]|2[0-9]|3[01])\.|192\.168\.)", ip)
            or origin != f"http://{ip}:5173"):
        return False
    token = os.environ.get("MARKETPULSE_LAN_TOKEN", "")
    return bool(_LAN_TOKEN.fullmatch(token) and _LAN_TOKEN.fullmatch(credential) and compare_digest(token, credential))


def _require_research_origin(request: Request) -> None:
    origins = request.headers.getlist("origin")
    credentials = request.headers.getlist(_LAN_AUTH)
    if credentials:
        if len(origins) == len(credentials) == 1 and _authorized_lan_origin(origins[0], credentials[0]):
            return
    elif not origins:
        return
    elif len(origins) == 1:
        origin = _LOCAL_ORIGIN.fullmatch(origins[0])
        if origin and (not origin[1] or int(origin[1]) <= 65535):
            return
    raise HTTPException(status_code=403, detail="Forbidden")


class _ResearchRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            _require_local(request)
            if os.environ.get("MARKETPULSE_MACRO_AI_ENABLED") != "true":
                raise HTTPException(status_code=404, detail="Macro research route unavailable")
            _require_research_origin(request)
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


class ResearchSubmission(ResearchRequest):
    research_version: Literal["v2"] | None = None


@router.post("/analysis")
def analysis(submission: ResearchSubmission, http_request: Request) -> ResearchV2Response | ResearchResponse:
    if submission.research_version == "v2" and (
        not research_v2_enabled() or _LAN_AUTH in http_request.headers
    ):
        raise HTTPException(status_code=404, detail="Research v2 unavailable")
    if not _SLOT.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Macro research is busy")
    try:
        # Keep optional dependencies outside application startup and DEMO.
        request = ResearchRequest.model_validate(submission.model_dump(exclude={"research_version"}))
        if submission.research_version == "v2":
            from .v2_service import run
            return run(request)
        from .service import workflow
        return workflow().run(request)
    except Exception:
        raise HTTPException(status_code=503, detail="Macro research unavailable") from None
    finally:
        _SLOT.release()
