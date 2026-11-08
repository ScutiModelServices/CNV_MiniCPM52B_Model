"""Missing host Ollama weights remain unavailable until the explicit admin action."""
import json
import httpx
from fastapi.testclient import TestClient
from app import create_app
from ollama import OllamaClient


def test_missing_model_never_pulls_then_admin_download_recovers():
    installed = False
    calls = []
    def handler(request):
        nonlocal installed
        calls.append(request.url.path)
        if request.url.path == "/api/pull":
            installed = True
            return httpx.Response(200, json={"status":"success"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models":[{"name":"m:latest","digest":"downloaded"}] if installed else []})
        if request.method == "GET":
            return httpx.Response(404, json={"error":"no registry entry"})
        if not installed:
            return httpx.Response(404, json={"error":"private/path"})
        return httpx.Response(200, json={"message":{"content":"hello"}})
    backend = OllamaClient("http://fake", "m", transport=httpx.MockTransport(handler))
    with TestClient(create_app(backend, admin_password="admin", warm=True)) as http:
        headers = {"X-Internal-Token":"admin"}
        token = http.post("/admin/tokens", headers=headers, json={"name":"test"}).json()["token"]
        public = {"X-Internal-Token":token}
        body = {"messages":[{"role":"user","content":"hello"}]}
        assert http.get("/health").json()["ready"] is False
        failed = http.post("/v1/chat", headers=public, json=body)
        assert failed.status_code == 503 and "Download" in failed.json()["detail"]
        assert "private/path" not in failed.text
        assert "/api/pull" not in calls
        assert http.post("/admin/updates/check", headers=headers).status_code == 200
        assert "/api/pull" not in calls
        assert http.post("/admin/updates/weights", headers=headers).status_code == 200
        assert http.get("/health").json()["ready"] is True
        assert http.post("/v1/chat", headers=public, json=body).status_code == 200
