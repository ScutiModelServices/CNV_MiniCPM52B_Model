import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from ollama import OllamaClient

BODY = {"messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.parametrize("project", ["", "Upper", "../x", "a" * 49, "a\n", 123])
def test_invalid_project_is_rejected_before_ollama(project):
    def handler(request):
        pytest.fail("Invalid project reached Ollama")
    backend = OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))
    assert TestClient(create_app(backend)).post("/v1/chat", json={**BODY, "project": project}).status_code == 422


@pytest.mark.parametrize("project,installed,expected,fallback", [
    (None, [], "base", False),
    ("alpha", ["minicpm-alpha:latest"], "minicpm-alpha", False),
    ("0_test-x", ["minicpm-0_test-x"], "minicpm-0_test-x", False),
    ("a" * 48, ["minicpm-" + "a" * 48], "minicpm-" + "a" * 48, False),
    ("missing", ["minicpm-other:latest"], "base", True),
    ("alpha", ["minicpm-alpha:old"], "base", True),
])
def test_project_routes_only_installed_models(monkeypatch, project, installed, expected, fallback):
    monkeypatch.delenv("SERVICE_PROJECT_PREFIX", raising=False)
    seen = []
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in installed]})
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"message": {"content": "ok"}})
    backend = OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))
    c = TestClient(create_app(backend))
    body = c.post("/v1/chat", json={**BODY, **({"project": project} if project is not None else {})}).json()
    assert (body["model"], body["fallback"]) == (expected, fallback)
    assert seen == [expected]
    assert backend.model == "base"


def test_custom_prefix_health_inventory_and_default_readiness(monkeypatch):
    monkeypatch.setenv("SERVICE_PROJECT_PREFIX", "custom-")
    names = ["custom-beta:latest", "custom-alpha", "custom-beta", "custom-no:old", "custom-Upper", "minicpm-x"]
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in names]})
        assert json.loads(request.content)["model"] == "custom-alpha"
        return httpx.Response(200, json={"message": {"content": "ok"}})
    c = TestClient(create_app(OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))))
    health = c.get("/health").json()
    assert health["ready"] is False
    assert health["project_models"] == ["custom-alpha", "custom-beta"]
    assert c.post("/v1/chat", json={**BODY, "project": "alpha"}).json()["fallback"] is False


@pytest.mark.parametrize("failure", [400, 503, "timeout", "down", "malformed"])
def test_unreadable_inventory_falls_back_to_base_and_health_stays_up(failure):
    def handler(request):
        if request.url.path != "/api/tags":
            assert json.loads(request.content)["model"] == "base"
            return httpx.Response(200, json={"message": {"content": "ok"}})
        if failure == "timeout":
            raise httpx.ReadTimeout("slow")
        if failure == "down":
            raise httpx.ConnectError("down")
        if failure == "malformed":
            return httpx.Response(200, text="not json")
        return httpx.Response(failure, json={"error": "failed"})
    c = TestClient(create_app(OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))))
    body = c.post("/v1/chat", json={**BODY, "project": "alpha"}).json()
    assert (body["model"], body["fallback"]) == ("base", True)
    health = c.get("/health").json()
    assert health["ready"] is False and health["project_models"] == []


def test_tags_cache_shares_concurrent_lookup_and_refreshes_after_30_seconds(monkeypatch):
    import ollama
    now = [0.0]
    monkeypatch.setattr(ollama.time, "monotonic", lambda: now[0])
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        await asyncio.sleep(0)
        name = "minicpm-alpha" if len(calls) == 1 else "minicpm-beta:latest"
        return httpx.Response(200, json={"models": [{"name": name}]})
    async def check():
        backend = OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))
        try:
            results = await asyncio.gather(*(backend.installed_models() for _ in range(8)))
            assert results == [{"minicpm-alpha"}] * 8
            now[0] = 29.9
            assert await backend.installed_models() == {"minicpm-alpha"}
            now[0] = 30.0
            assert await backend.installed_models() == {"minicpm-beta:latest"}
            assert calls == ["/api/tags", "/api/tags"]
        finally:
            await backend.close()
    asyncio.run(check())


def test_failed_inventory_lookup_is_retried_instead_of_cached_as_fallback():
    calls = []
    def handler(request):
        if request.url.path == "/api/tags":
            calls.append(request.url.path)
            if len(calls) == 1:
                return httpx.Response(503, json={"error": "temporary failure"})
            return httpx.Response(200, json={"models": [{"name": "minicpm-alpha:latest"}]})
        return httpx.Response(200, json={"message": {"content": "ok"}})
    c = TestClient(create_app(OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))))
    assert c.post("/v1/chat", json={**BODY, "project": "alpha"}).json()["fallback"] is True
    result = c.post("/v1/chat", json={**BODY, "project": "alpha"}).json()
    assert result["model"] == "minicpm-alpha" and result["fallback"] is False


def test_model_created_after_the_cache_filled_is_found_on_a_miss(monkeypatch):
    import ollama
    now = [0.0]
    monkeypatch.setattr(ollama.time, "monotonic", lambda: now[0])
    names = []
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in names]})
        return httpx.Response(200, json={"message": {"content": "ok"}})
    c = TestClient(create_app(OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))))
    assert c.post("/v1/chat", json={**BODY, "project": "alpha"}).json()["fallback"] is True
    names.append("minicpm-alpha:latest")  # `ollama create` ran; the cached list is still empty
    now[0] = 1.0  # a miss within 2 s of the last lookup does not hit Ollama again
    assert c.post("/v1/chat", json={**BODY, "project": "alpha"}).json()["fallback"] is True
    now[0] = 2.0
    assert c.post("/v1/chat", json={**BODY, "project": "alpha"}).json()["fallback"] is False


def test_health_ready_reflects_a_model_pulled_moments_ago():
    names = []
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": n} for n in names]})
    c = TestClient(create_app(OllamaClient("http://ollama", "base", transport=httpx.MockTransport(handler))))
    assert c.get("/health").json()["ready"] is False
    names.append("base:latest")
    assert c.get("/health").json()["ready"] is True
