import asyncio
import hashlib
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import DEFAULT_MODEL, create_app
from ollama import OllamaClient, OllamaError
from updates import Updates


def run(operation):
    return asyncio.run(operation)


def make(cached="old", remote="new", fail_remote=False, fail_pull=False, fail_unload=False):
    state = {"cached": cached}
    events = []

    async def local_sha():
        return state["cached"]

    async def remote_sha():
        if fail_remote:
            raise OSError("no network")
        return remote

    async def pull():
        events.append("pull")
        if fail_pull:
            raise OSError("disk full")
        state["cached"] = remote

    async def unload():
        events.append("unload")
        if fail_unload:
            raise OSError("unload failed")

    updates = Updates(DEFAULT_MODEL, cached_sha=local_sha, remote_sha=remote_sha, download=pull, unload=unload)
    return updates, state, events


def test_check_reports_new_weights_without_downloading():
    updates, _, events = make()
    status = run(updates.check())
    assert status["package"] is None and status["checked_at"] is not None
    assert status["weights"] == [{"repo": DEFAULT_MODEL, "cached": "old", "latest": "new", "update": True}]
    assert updates.status() == status and events == []


def test_check_failure_is_reported_not_raised():
    updates, _, events = make(fail_remote=True)
    assert run(updates.check())["error"] == "OSError: no network"
    assert events == []


def test_missing_local_model_is_an_update():
    updates, *_ = make(cached=None)
    assert run(updates.check())["weights"][0]["update"] is True


def test_update_downloads_then_unloads_shared_model():
    updates, _, events = make()
    assert run(updates.update_weights())["weights"][0]["update"] is False
    assert events == ["pull", "unload"]


def test_update_with_nothing_new_downloads_nothing():
    updates, _, events = make(cached="new")
    assert run(updates.update_weights())["weights"][0]["update"] is False
    assert events == ["unload"]  # explicit update action also recovers an unload failed on another replica


def test_explicit_download_still_runs_when_online_comparison_failed():
    updates, _, events = make(fail_remote=True)
    status = run(updates.update_weights())
    assert events == ["pull", "unload"]
    assert status["weights"][0]["cached"] == "new"
    assert "no network" in status["error"]


def test_failed_pull_does_not_unload_and_records_error():
    updates, _, events = make(fail_pull=True)
    with pytest.raises(OSError, match="disk full"):
        run(updates.update_weights())
    assert events == ["pull"]
    assert "disk full" in updates.status()["error"]


def test_failed_unload_is_retried_before_reporting_success():
    updates, _, events = make(fail_unload=True)
    for _ in range(2):
        with pytest.raises(OSError, match="unload failed"):
            run(updates.update_weights())
    assert events == ["pull", "unload", "unload"]
    assert "unload failed" in updates.status()["error"]


def test_checks_and_downloads_are_serialized():
    async def scenario():
        active = 0
        peak = 0

        async def remote():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "new"

        updates, *_ = make()
        updates._remote_sha = remote
        await asyncio.gather(updates.check(), updates.update_weights(), updates.check())
        assert peak == 1

    run(scenario())


def client(updates, check_hours=0):
    backend = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    return TestClient(create_app(backend, "secret", updates=updates, check_hours=check_hours))


def test_update_routes_are_admin_only():
    updates, *_ = make()
    http = client(updates)
    for method, path in (("get", "/admin/updates"), ("post", "/admin/updates/check"), ("post", "/admin/updates/weights")):
        assert getattr(http, method)(path).status_code == 401
    assert http.get("/admin/updates", headers={"X-Internal-Token": "secret"}).json()["checked_at"] is None


def test_admin_checks_then_downloads():
    updates, _, events = make()
    http, admin = client(updates), {"X-Internal-Token": "secret"}
    assert http.post("/admin/updates/check", headers=admin).json()["weights"][0]["update"] is True
    body = http.post("/admin/updates/weights", headers=admin).json()
    assert body["enabled"] is True and body["weights"][0]["update"] is False
    assert events == ["pull", "unload"]


def test_failed_download_is_502_with_reason():
    updates, *_ = make(fail_pull=True)
    http = client(updates)
    response = http.post("/admin/updates/weights", headers={"X-Internal-Token": "secret"})
    assert response.status_code == 502 and "disk full" in response.json()["detail"]


def test_background_check_runs_at_start():
    updates, *_ = make()
    with client(updates, check_hours=1) as http:
        for _ in range(100):
            if http.get("/admin/updates", headers={"X-Internal-Token": "secret"}).json()["checked_at"]:
                break
            time.sleep(0.02)
    assert updates.status()["checked_at"] is not None


def test_zero_hours_disables_timer_but_keeps_manual_checks():
    updates, *_ = make()
    with client(updates, check_hours=0) as http:
        assert updates.status()["checked_at"] is None
        assert http.post("/admin/updates/check", headers={"X-Internal-Token": "secret"}).json()["checked_at"]


def test_client_compares_manifest_bytes_to_tags_digest_without_pull():
    manifest = b'{"schemaVersion":2,"config":{},"layers":[]}'
    digest = hashlib.sha256(manifest).hexdigest()
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url)))
        if request.url.host == "hf.co":
            return httpx.Response(200, content=manifest)
        return httpx.Response(200, json={"models": [{"name": DEFAULT_MODEL, "digest": "sha256:" + digest}]})

    async def scenario():
        backend = OllamaClient("http://ollama", DEFAULT_MODEL, transport=httpx.MockTransport(handler))
        try:
            assert await backend.remote_sha() == digest
            assert await backend.cached_sha() == digest
        finally:
            await backend.close()

    run(scenario())
    assert seen == [("GET", "https://hf.co/v2/openbmb/MiniCPM5-2B-GGUF/manifests/Q4_K_M"),
                    ("GET", "http://ollama/api/tags")]


def test_client_pull_then_unload_payloads():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "success"})

    async def scenario():
        backend = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
        try:
            await backend.pull()
            await backend.unload()
        finally:
            await backend.close()

    run(scenario())
    assert seen == [("/api/pull", {"model": "m", "stream": False}),
                    ("/api/chat", {"model": "m", "messages": [], "stream": False, "keep_alive": 0})]


@pytest.mark.parametrize("payload", [{"error": "disk full"}, {"status": "incomplete"}])
def test_client_rejects_unsuccessful_pull_even_with_http_200(payload):
    async def scenario():
        backend = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        try:
            with pytest.raises(OllamaError):
                await backend.pull()
        finally:
            await backend.close()
    run(scenario())


def test_client_rejects_invalid_remote_manifest():
    async def scenario():
        backend = OllamaClient("http://ollama", DEFAULT_MODEL,
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": "bad"})))
        try:
            with pytest.raises(OllamaError, match="manifest"):
                await backend.remote_sha()
        finally:
            await backend.close()
    run(scenario())


def test_retry_unloads_when_pull_committed_but_response_was_lost():
    updates, state, events = make()

    async def download_with_lost_response():
        events.append("pull")
        state["cached"] = "new"
        raise OSError("response lost after pull")

    updates._download = download_with_lost_response
    with pytest.raises(OSError, match="response lost"):
        run(updates.update_weights())
    assert events == ["pull"]
    status = run(updates.update_weights())
    assert events == ["pull", "unload"]
    assert status["weights"][0]["update"] is False


def test_another_replica_can_retry_unload_after_a_lost_pull_response():
    updates, state, events = make()

    async def lost_response():
        events.append("pull")
        state["cached"] = "new"
        raise OSError("response lost")

    async def cached_sha():
        return state["cached"]

    async def remote_sha():
        return "new"

    async def unload():
        events.append("unload")

    updates._download = lost_response
    with pytest.raises(OSError):
        run(updates.update_weights())
    retry_replica = Updates(DEFAULT_MODEL, cached_sha=cached_sha, remote_sha=remote_sha,
                            download=lost_response, unload=unload)
    assert run(retry_replica.update_weights())["weights"][0]["update"] is False
    assert events == ["pull", "unload"]
