"""Bounded Gemini Developer API client used by the public Research v2 profile."""

from asyncio import run, timeout
from functools import partial
import json
from random import uniform
from time import monotonic, sleep
from typing import get_args

import httpx

from ..research_logging import research_stage
from .v2_gemini import (
    _COMPOSE, _GENERAL, _INTERPRET, check_egress, composition_inputs, general_inputs, interpretation_inputs,
)
from .v2_models import Draft, FallbackModelId, GeneralDraft, Intent


# Both models are covered by the Worker reservation. Review prices before
# adding a model or changing these limits.
MODELS = frozenset(get_args(FallbackModelId))
INPUT_TOKENS = 8_000
# Headroom for any provider framing/schema bytes absent from countTokens.
# Static schemas are bounded as well, so changes cannot silently exceed it.
PREFLIGHT_TOKENS = 6_000
SCHEMA_BYTES = 4_000
OUTPUT_TOKENS = 2_400
GENERAL_OUTPUT_TOKENS = 800
GEMINI_STAGE_SECONDS = 25
GEMINI_HTTP_SECONDS = 20
GENERAL_DEADLINE_HEADROOM = 2
MAX_GENERATIONS = 2
# One extra generation is permitted exclusively for a transient interpretation
# failure. This is still one admitted Research request, not an HTTP retry.
INTERPRET_FAILOVER_MODEL = "gemini-3.5-flash-lite"
INTERPRET_FAILOVER_SECONDS = 10
INTERPRET_FAILOVER_HEADROOM = 30
# Lite is designed for low-latency structured extraction. Never let three
# attempts each consume the legacy 20-second HTTP timeout.
LITE_INTERPRET_ATTEMPTS = 3
LITE_INTERPRET_SECONDS = 8
LITE_INTERPRET_HEADROOM = 30
LITE_COMPOSE_SECONDS = 10
LITE_GENERAL_SECONDS = 12
MAX_PROVIDER_BODY = 64 * 1024


def _retryable_lite_interpret_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in (500, 502, 503, 504)
    return isinstance(error, (TimeoutError, httpx.RequestError))


def _provider_status(error: BaseException) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    return None


_gemini_stage = partial(research_stage, provider_status=_provider_status)


class PublicGemini:
    def __init__(self, *, api_key: str, model: str, deadline: float):
        if model not in MODELS:
            raise ValueError("Unsupported Research model")
        self._key = api_key
        self._model = model
        self._deadline = deadline
        self._calls = 0
        self._interpret_failover_allowance = 0

    def interpret(self, request, *, knowledge_cutoff=None) -> Intent:
        with _gemini_stage("gemini.interpret"):
            inputs = interpretation_inputs(request, knowledge_cutoff=knowledge_cutoff)
            if self._model == INTERPRET_FAILOVER_MODEL:
                return self._interpret_lite_with_retries(inputs)
            try:
                return self._generate(_INTERPRET, inputs, Intent)
            except (TimeoutError, httpx.HTTPError) as error:
                if self._model == INTERPRET_FAILOVER_MODEL:
                    raise
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in (
                    429, 500, 502, 503, 504,
                ):
                    raise
                # Retain enough time for official-data retrieval, final
                # composition, validation and serialization. Do not turn a
                # single provider stall into a 65-second research timeout.
                if self._deadline - monotonic() <= INTERPRET_FAILOVER_HEADROOM + 1:
                    raise
                self._interpret_failover_allowance = 1
                with _gemini_stage("gemini.interpret_failover"):
                    return self._generate(
                        _INTERPRET, inputs, Intent,
                        model_override=INTERPRET_FAILOVER_MODEL,
                        stage_seconds=INTERPRET_FAILOVER_SECONDS,
                        deadline_headroom=INTERPRET_FAILOVER_HEADROOM,
                    )

    def _interpret_lite_with_retries(self, inputs: dict[str, str]) -> Intent:
        for attempt in range(LITE_INTERPRET_ATTEMPTS):
            # Each additional generation is allowed ONLY after the preceding
            # Lite interpretation attempt failed with a transient error.
            self._interpret_failover_allowance = attempt
            try:
                with _gemini_stage(f"gemini.interpret_lite_attempt_{attempt + 1}"):
                    return self._generate(
                        _INTERPRET, inputs, Intent,
                        stage_seconds=LITE_INTERPRET_SECONDS,
                        deadline_headroom=LITE_INTERPRET_HEADROOM,
                    )
            except Exception as error:
                if not _retryable_lite_interpret_error(error) or attempt + 1 == LITE_INTERPRET_ATTEMPTS:
                    raise
                delay = 0.2 * (2 ** attempt) + uniform(0, 0.1)
                if self._deadline - monotonic() <= (
                    LITE_INTERPRET_HEADROOM + LITE_INTERPRET_SECONDS + delay
                ):
                    raise
                sleep(delay)
        raise AssertionError("Unreachable: all attempts terminate or return")

    def compose(self, evidence: list[dict]) -> Draft:
        with _gemini_stage("gemini.compose"):
            return self._generate(
                _COMPOSE, composition_inputs(evidence), Draft,
                stage_seconds=LITE_COMPOSE_SECONDS
                if self._model == INTERPRET_FAILOVER_MODEL else GEMINI_STAGE_SECONDS,
            )

    def general(self, request, reason, *, event, knowledge_cutoff) -> GeneralDraft:
        with _gemini_stage("gemini.general"):
            inputs = general_inputs(request, reason, event=event, knowledge_cutoff=knowledge_cutoff)
            return self._generate(
                _GENERAL, inputs, GeneralDraft, output_tokens=GENERAL_OUTPUT_TOKENS,
                deadline_headroom=GENERAL_DEADLINE_HEADROOM,
                stage_seconds=LITE_GENERAL_SECONDS
                if self._model == INTERPRET_FAILOVER_MODEL else GEMINI_STAGE_SECONDS,
            )

    def _generate(self, template, inputs, schema, *, output_tokens=OUTPUT_TOKENS, deadline_headroom=0,
                  model_override=None, stage_seconds=GEMINI_STAGE_SECONDS):
        if self._calls >= MAX_GENERATIONS + self._interpret_failover_allowance:
            raise ValueError("Research generation limit reached")
        check_egress(inputs, self._key)
        self._calls += 1
        system, user = template.format_messages(**inputs)
        response_schema = schema.model_json_schema()
        if len(json.dumps(response_schema, separators=(",", ":")).encode("utf-8")) > SCHEMA_BYTES:
            raise ValueError("Research schema exceeds its size limit")
        payload = {
            "systemInstruction": {"parts": [{"text": system.content}]},
            "contents": [{"role": "user", "parts": [{"text": user.content}]}],
            "generationConfig": {
                "candidateCount": 1, "maxOutputTokens": output_tokens,
                "responseMimeType": "application/json", "responseJsonSchema": response_schema,
                "thinkingConfig": {"thinkingLevel": "MINIMAL", "includeThoughts": False},
            },
        }
        try:
            raw = run(self._invoke(payload, deadline_headroom=deadline_headroom,
                                   model_override=model_override, stage_seconds=stage_seconds))
        except httpx.TimeoutException:
            raise TimeoutError("Gemini timed out") from None
        with _gemini_stage("gemini.validate_schema"):
            return schema.model_validate_json(raw)

    async def _invoke(self, payload: dict, *, deadline_headroom: float,
                      model_override=None, stage_seconds=GEMINI_STAGE_SECONDS) -> str:
        model = f"models/{model_override or self._model}"
        now = monotonic()
        deadline = min(self._deadline - deadline_headroom, now + stage_seconds)
        remaining = deadline - now
        if remaining <= 0:
            raise TimeoutError("Research deadline reached")
        async with timeout(remaining):
            async with httpx.AsyncClient(
                base_url="https://generativelanguage.googleapis.com/v1beta/",
                headers={"x-goog-api-key": self._key, "Accept": "application/json",
                         "Accept-Encoding": "identity"},
                trust_env=False, follow_redirects=False,
                timeout=min(GEMINI_HTTP_SECONDS, stage_seconds),
            ) as client:
                with _gemini_stage("gemini.count_tokens"):
                    if monotonic() >= deadline:
                        raise TimeoutError("Gemini stage deadline reached")
                    counts = await self._post(client, f"{model}:countTokens",
                                              {"generateContentRequest": {"model": model, **payload}})
                    tokens = counts.get("totalTokens")
                    if type(tokens) is not int or not 0 < tokens <= PREFLIGHT_TOKENS:
                        raise ValueError("Research prompt exceeds the input token limit")
                    if monotonic() >= deadline:
                        raise TimeoutError("Gemini stage deadline reached")
                with _gemini_stage("gemini.generate_content"):
                    result = await self._post(client, f"{model}:generateContent", payload)
                    if monotonic() >= deadline:
                        raise TimeoutError("Gemini stage deadline reached")
                    usage = result["usageMetadata"]
                    prompt, output, thinking = (usage.get(key, 0) for key in (
                        "promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount",
                    ))
                    if (any(type(value) is not int or value < 0 for value in (prompt, output, thinking))
                            or not 0 < prompt <= INPUT_TOKENS
                            or not 0 < output + thinking <= payload["generationConfig"]["maxOutputTokens"]):
                        raise ValueError("Unexpected provider token usage")
                    candidate, = result["candidates"]
                    if candidate["finishReason"] != "STOP":
                        raise ValueError("Incomplete provider response")
                    parts = candidate["content"]["parts"]
                    if not parts or any(set(part) - {"text", "thoughtSignature"} for part in parts):
                        raise ValueError("Unexpected provider response parts")
                    return "".join(part["text"] for part in parts)

    @staticmethod
    async def _post(client: httpx.AsyncClient, endpoint: str, payload: dict) -> dict:
        async with client.stream("POST", endpoint, json=payload) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_PROVIDER_BODY:
                    raise ValueError("Provider response exceeds the response limit")
                body.extend(chunk)
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Unexpected provider response")
        return result
