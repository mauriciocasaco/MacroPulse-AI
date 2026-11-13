"""Bounded Gemini Developer API client used by the public Research v2 profile."""

from asyncio import run, timeout
import json
from time import monotonic

import httpx

from .v2_gemini import _COMPOSE, _INTERPRET, composition_inputs, interpretation_inputs
from .v2_models import Draft, Intent


# Both models are covered by the Worker reservation. Review prices before
# adding a model or changing these limits.
MODELS = frozenset({"gemini-3.6-flash", "gemini-3.5-flash-lite"})
INPUT_TOKENS = 8_000
# Headroom for any provider framing/schema bytes absent from countTokens.
# Static schemas are bounded as well, so changes cannot silently exceed it.
PREFLIGHT_TOKENS = 6_000
SCHEMA_BYTES = 4_000
OUTPUT_TOKENS = 2_400
MAX_GENERATIONS = 2
MAX_PROVIDER_BODY = 64 * 1024


class PublicGemini:
    def __init__(self, *, api_key: str, model: str, deadline: float):
        self._key = api_key
        self._model = model
        self._deadline = deadline
        self._calls = 0

    def interpret(self, request) -> Intent:
        inputs = interpretation_inputs(request, self._key)
        return self._generate(_INTERPRET, inputs, Intent)

    def compose(self, evidence: list[dict]) -> Draft:
        return self._generate(_COMPOSE, composition_inputs(evidence), Draft)

    def _generate(self, template, inputs, schema):
        if self._calls >= MAX_GENERATIONS:
            raise ValueError("Research generation limit reached")
        self._calls += 1
        system, user = template.format_messages(**inputs)
        response_schema = schema.model_json_schema()
        if len(json.dumps(response_schema, separators=(",", ":")).encode("utf-8")) > SCHEMA_BYTES:
            raise ValueError("Research schema exceeds its size limit")
        payload = {
            "systemInstruction": {"parts": [{"text": system.content}]},
            "contents": [{"role": "user", "parts": [{"text": user.content}]}],
            "generationConfig": {
                "candidateCount": 1, "maxOutputTokens": OUTPUT_TOKENS,
                "responseMimeType": "application/json", "responseJsonSchema": response_schema,
                "thinkingConfig": {"thinkingLevel": "MINIMAL", "includeThoughts": False},
            },
        }
        try:
            raw = run(self._invoke(payload))
        except httpx.TimeoutException:
            raise TimeoutError("Gemini timed out") from None
        return schema.model_validate_json(raw)

    async def _invoke(self, payload: dict) -> str:
        model = f"models/{self._model}"
        async with timeout(min(15, max(0, self._deadline - monotonic()))):
            async with httpx.AsyncClient(
                base_url="https://generativelanguage.googleapis.com/v1beta/",
                headers={"x-goog-api-key": self._key, "Accept": "application/json",
                         "Accept-Encoding": "identity"},
                trust_env=False, follow_redirects=False, timeout=12,
            ) as client:
                counts = await self._post(client, f"{model}:countTokens",
                                          {"generateContentRequest": {"model": model, **payload}})
                tokens = counts.get("totalTokens")
                if type(tokens) is not int or not 0 < tokens <= PREFLIGHT_TOKENS:
                    raise ValueError("Research prompt exceeds the input token limit")
                result = await self._post(client, f"{model}:generateContent", payload)
                usage = result["usageMetadata"]
                prompt, output, thinking = (usage.get(key, 0) for key in (
                    "promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount",
                ))
                if (any(type(value) is not int or value < 0 for value in (prompt, output, thinking))
                        or not 0 < prompt <= INPUT_TOKENS or not 0 < output + thinking <= OUTPUT_TOKENS):
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
