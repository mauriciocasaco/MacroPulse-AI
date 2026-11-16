"""Authenticate and redeem a Worker-issued Research attestation."""

from asyncio import timeout
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import hmac
from ipaddress import ip_address
import json
import re

from starlette.requests import Request

RESEARCH_PATH = "/macro/analysis"
_KEY_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_HEX = re.compile(r"[a-f0-9]{64}")
_UUID4 = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}")
_EPOCH_MS = re.compile(r"[1-9][0-9]{12}")
_IP = re.compile(r"[0-9a-fA-F:.]{1,45}")
_HOSTNAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True)
class OriginConfig:
    origin_keys: Mapping[str, bytes] = field(repr=False)
    verify_kid: str
    verify_key: str = field(repr=False)
    callback_url: str
    sensitive_values: tuple[str, ...] = field(repr=False)


def _parse_keys(raw: str) -> dict[str, str]:
    if len(raw) > 512:
        raise ValueError
    entries = json.loads(raw)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 2:
        raise ValueError
    keys: dict[str, str] = {}
    for pair in entries:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError
        kid, key = pair
        if not isinstance(kid, str) or not _KEY_ID.fullmatch(kid) or kid in keys:
            raise ValueError
        if not isinstance(key, str) or not _HEX.fullmatch(key) or key in keys.values():
            raise ValueError
        keys[kid] = key
    return keys


def origin_config(env: Mapping[str, str], legacy_key: str) -> OriginConfig | None:
    try:
        origin_keys = _parse_keys(env.get("MACROPULSE_RESEARCH_ORIGIN_KEYS", ""))
        verify_keys = _parse_keys(env.get("MACROPULSE_RESEARCH_VERIFY_KEYS", ""))
        verify_kid = env.get("MACROPULSE_RESEARCH_VERIFY_ACTIVE_KID", "")
        hostname = env.get("MACROPULSE_RESEARCH_HOSTNAME", "")
        secret_values = (*origin_keys.values(), *verify_keys.values())
        if (len(set(secret_values)) != len(secret_values) or legacy_key in secret_values
                or verify_kid not in verify_keys or len(hostname) > 253 or not _HOSTNAME.fullmatch(hostname)):
            return None
        return OriginConfig(
            {kid: bytes.fromhex(key) for kid, key in origin_keys.items()}, verify_kid, verify_keys[verify_kid],
            f"https://{hostname}/api/internal/research/consume-v1", secret_values,
        )
    except (KeyError, ValueError, RecursionError):
        return None


def _header(request: Request, name: bytes, pattern: re.Pattern[str]) -> str:
    values = [value for key, value in request.scope["headers"] if key.lower() == name]
    if len(values) != 1:
        raise ValueError
    value = values[0].decode("ascii")
    if not pattern.fullmatch(value):
        raise ValueError
    return value


@dataclass(frozen=True)
class Attestation:
    kid: str
    timestamp: int
    nonce: str = field(repr=False)
    scope: str
    expires: int
    research_id: str
    client_ip: str = field(repr=False)
    signature: bytes = field(repr=False)

    @classmethod
    def from_request(cls, request: Request) -> "Attestation":
        client_ip = _header(request, b"x-marketpulse-client-ip", _IP)
        ip_address(client_ip)
        return cls(
            _header(request, b"x-macropulse-origin-key-id", _KEY_ID),
            int(_header(request, b"x-macropulse-origin-timestamp", _EPOCH_MS)),
            _header(request, b"x-macropulse-origin-nonce", _UUID4),
            _header(request, b"x-macropulse-origin-scope", re.compile(r"public|admin")),
            int(_header(request, b"x-macropulse-research-expires", _EPOCH_MS)),
            _header(request, b"x-macropulse-research-id", _UUID4),
            client_ip,
            bytes.fromhex(_header(request, b"x-macropulse-origin-signature", _HEX)),
        )

    def authenticate(self, config: OriginConfig, body: bytes, now_ms: int) -> str | None:
        if not now_ms - 10_000 <= self.timestamp <= now_ms + 2_000 or self.expires <= self.timestamp:
            return None
        key = config.origin_keys.get(self.kid)
        if key is None:
            return None
        canonical = "\n".join((
            "macropulse/render-research/v1", self.kid, "POST", RESEARCH_PATH, "",
            "application/json", sha256(body).hexdigest(), str(self.timestamp), self.nonce,
            self.scope, str(self.expires), self.research_id, self.client_ip,
        )).encode("ascii")
        if not hmac.compare_digest(self.signature, hmac.digest(key, canonical, "sha256")):
            return None
        return sha256(canonical).hexdigest()


async def consume_grant(config: OriginConfig, attestation: Attestation, commitment: str) -> bool | None:
    """Return True on consumption, False on denial, and None on service failure."""
    # The Research requirements already include httpx. Read-only deployments can omit it.
    try:
        import httpx
    except ImportError:
        return None

    body = "\n".join((
        attestation.scope, attestation.nonce, commitment, str(attestation.timestamp), str(attestation.expires),
    )).encode("ascii")
    try:
        async with timeout(2.5), httpx.AsyncClient(
            follow_redirects=False, trust_env=False, verify=True, timeout=2.5,
        ) as client:
            async with client.stream("POST", config.callback_url, content=body, headers={
                "X-MacroPulse-Verify-Key-Id": config.verify_kid,
                "Authorization": f"Bearer {config.verify_key}",
                "Content-Type": "text/plain",
                "Cache-Control": "no-store",
            }) as response:
                if response.status_code == 204:
                    return True
                if response.status_code == 403:
                    return False
    except (TimeoutError, httpx.HTTPError):
        pass
    return None
