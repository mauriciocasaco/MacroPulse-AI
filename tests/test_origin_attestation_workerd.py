"""Opt-in integration against the actual Worker and Durable Object in local workerd."""

import base64
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from threading import Barrier
from time import time

from fastapi.testclient import TestClient
import httpx
import pytest

from test_origin_attestation import HEADERS, PATH, mock_transport, origin, refsign
from backend import public_demo


@pytest.fixture
def workerd(tmp_path):
    entry = os.environ.get("MACROPULSE_MINIFLARE_ENTRY")
    if not entry:
        pytest.skip("set MACROPULSE_MINIFLARE_ENTRY to run the local workerd integration")
    root = Path(__file__).resolve().parents[1]
    script = root / "tests/fixtures/security/workerd-bridge.mjs"
    runtime_environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "TEMP", "TMP", "TMPDIR")
                           if key in os.environ}
    runtime_environment["MACROPULSE_MINIFLARE_ENTRY"] = str(Path(entry).resolve())
    with subprocess.Popen(
        ["node", str(script), str(tmp_path / "durable-state")], cwd=root, env=runtime_environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
    ) as process:
        try:
            for line in process.stdout:
                if line.startswith('{"fixturePort":'):
                    host = f"http://127.0.0.1:{json.loads(line)['fixturePort']}"
                    break
            else:
                pytest.fail("local workerd did not start")
            yield host
        finally:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def test_worker_durable_object_and_fastapi_converge_on_one_execution(workerd, origin, monkeypatch):
    monkeypatch.setattr(public_demo, "time", time)
    callback_count = 0
    simultaneous = None

    def relay(request):
        nonlocal callback_count
        assert request.url.scheme == "https" and request.url.host == "demo.example"
        callback_count += 1
        if simultaneous is not None:
            simultaneous.wait(timeout=2)
        with httpx.Client(trust_env=False, timeout=2) as client:
            result = client.post(workerd + request.url.path, content=request.content, headers={
                name: request.headers[name]
                for name in ("Authorization", "Content-Type", "X-MacroPulse-Verify-Key-Id")
            })
            return httpx.Response(result.status_code)

    mock_transport(monkeypatch, relay)

    def mint(scope, reject=False):
        response = httpx.post(workerd + "/admit", json={"scope": scope, "reject": reject}, trust_env=False)
        response.raise_for_status()
        return response.json()

    assert mint("public", reject=True) == {"status": 403, "wire": None}
    admitted = mint("public")
    assert admitted["status"] == 503
    wire = admitted["wire"]
    headers = wire["headers"]
    body = base64.b64decode(wire["body"])
    # HTTP field names are case-insensitive. Reconstruct reference signer inputs.
    header_names = {name.lower(): name for name in HEADERS}
    signed = {header_names[name]: value for name, value in headers.items()}
    assert signed["X-MacroPulse-Origin-Scope"] == "public"

    def run(client):
        return client.post(PATH, headers=headers, content=body).status_code

    with TestClient(origin.app()) as first, TestClient(origin.app()) as second:
        assert first.post(PATH, content=body).status_code == 403
        assert first.post(PATH, headers=headers, content=body + b" ").status_code == 403
        assert callback_count == 0
        changed = body + b" "
        assert first.post(PATH, headers=refsign(signed, changed), content=changed).status_code == 403
        wrong_scope = {**signed, "X-MacroPulse-Origin-Scope": "admin"}
        assert first.post(PATH, headers=refsign(wrong_scope, body), content=body).status_code == 403

        auth = json.loads(os.environ["MACROPULSE_RESEARCH_VERIFY_KEYS"])[0]
        # A leaked callback credential has no authority to invent a lease.
        invalid = "\n".join(("public", "48fe9360-ef69-4279-9b87-8af100f589c2",
                             sha256(body).hexdigest(), signed["X-MacroPulse-Origin-Timestamp"],
                             signed["X-MacroPulse-Research-Expires"]))
        reply = httpx.post(workerd + "/api/internal/research/consume-v1", content=invalid, trust_env=False, headers={
            "Authorization": "Bearer " + auth[1], "Content-Type": "text/plain", "X-MacroPulse-Verify-Key-Id": auth[0],
        })
        assert reply.status_code == 403

        simultaneous = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(run, (first, second))) == [200, 403]
        simultaneous = None
        assert run(first) == 403

    # Fresh FastAPI and workerd on the same persistent SQLite storage.
    assert httpx.post(workerd + "/restart", trust_env=False).status_code == 204
    with TestClient(origin.app()) as restarted:
        assert run(restarted) == 403
        assert origin.runner.call_count == 1
        admin = mint("admin")["wire"]
        assert admin["headers"]["x-macropulse-origin-scope"] == "admin"
        assert restarted.post(PATH, headers=admin["headers"], content=base64.b64decode(admin["body"])).status_code == 200

    assert origin.parser.call_count == origin.runner.call_count == 2
