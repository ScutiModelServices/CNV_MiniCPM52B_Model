import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from ollama import OllamaClient

BODY = {"messages": [{"role": "user", "content": "hi"}]}


def client(handler, admin_password=None):
    ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
    return TestClient(create_app(ollama, admin_password))


def reply(content="hello", **extra):
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content, **extra},
                                     "prompt_eval_count": 3, "eval_count": 5})


def test_chat_forwards_options_and_reports_usage():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply(thinking="hmm")

    body = client(handler).post("/v1/chat", json={**BODY, "thinking": True, "temperature": 0.3, "max_tokens": 50}).json()
    assert body["content"] == "hello" and body["thinking"] == "hmm"
    assert body["usage"] == {"input_tokens": 3, "output_tokens": 5}
    assert seen["think"] is True and seen["options"] == {"temperature": 0.3, "num_predict": 50} and "format" not in seen


def test_thinking_is_on_by_default_and_can_be_turned_off():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["think"])
        return reply()

    c = client(handler)
    c.post("/v1/chat", json=BODY)
    c.post("/v1/chat", json={**BODY, "thinking": False})
    assert seen == [True, False]


WEATHER = {"name": "get_weather", "description": "Weather for a city",
           "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}


def test_tools_are_sent_and_tool_calls_come_back_normalised():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]}})

    body = client(handler).post("/v1/chat", json={**BODY, "tools": [WEATHER]}).json()
    assert seen["tools"] == [{"type": "function", "function": {**WEATHER}}]
    assert body["tool_calls"] == [{"id": "call_0", "name": "get_weather", "arguments": {"city": "Paris"}}]
    assert body["finish_reason"] == "tool_calls"


def test_tool_round_trip_messages_reach_ollama():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply("It is sunny.")

    messages = [{"role": "user", "content": "Weather in Paris?"},
                {"role": "assistant", "tool_calls": [{"id": "call_0", "name": "get_weather", "arguments": {"city": "Paris"}}]},
                {"role": "tool", "tool_name": "get_weather", "content": "sunny"}]
    body = client(handler).post("/v1/chat", json={"messages": messages, "tools": [WEATHER]}).json()
    assert seen["messages"][1] == {"role": "assistant", "content": "",
                                   "tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]}
    assert seen["messages"][2] == {"role": "tool", "content": "sunny", "tool_name": "get_weather"}
    assert body["tool_calls"] == [] and body["finish_reason"] == "stop"


def test_tools_and_schema_together_are_refused():
    r = client(lambda request: reply()).post("/v1/chat", json={**BODY, "tools": [WEATHER], "schema": {"type": "object"}})
    assert r.status_code == 422


def test_running_out_of_tokens_is_reported():
    r = client(lambda request: httpx.Response(200, json={"message": {"content": "", "thinking": "hm"}, "done_reason": "length"}))
    assert r.post("/v1/chat", json=BODY).json()["finish_reason"] == "length"


def test_schema_constrains_and_parses_the_reply():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply('{"ok": true}')

    body = client(handler).post("/v1/chat", json={**BODY, "schema": schema}).json()
    assert body["parsed"] == {"ok": True} and seen["format"] == schema


def test_invalid_json_under_a_schema_is_502_with_the_content():
    r = client(lambda request: reply('{"ok": tr')).post("/v1/chat", json={**BODY, "schema": {"type": "object"}})
    assert r.status_code == 502 and r.json()["detail"]["content"] == '{"ok": tr'


def test_ollama_errors_map_to_http_statuses():
    def refuse(code):
        return client(lambda request: httpx.Response(code, json={"error": "nope"}))

    assert refuse(400).post("/v1/chat", json=BODY).status_code == 422
    assert refuse(404).post("/v1/chat", json=BODY).status_code == 502

    def down(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    assert client(down).post("/v1/chat", json=BODY).status_code == 502
    assert client(slow).post("/v1/chat", json=BODY).status_code == 504


def test_request_is_validated():
    c = client(lambda request: reply())
    assert c.post("/v1/chat", json={"messages": []}).status_code == 422
    assert c.post("/v1/chat", json={**BODY, "max_tokens": 0}).status_code == 422


def test_health_reports_model_readiness():
    ready = client(lambda request: httpx.Response(200, json={"models": [{"name": "m:latest"}]}))
    assert ready.get("/health").json() == {"status": "ok", "model": "m", "ready": True, "num_thread": None}
    assert client(lambda request: httpx.Response(200, json={"models": []})).get("/health").json()["ready"] is False


def test_each_project_gets_its_own_revocable_token(tmp_path):
    from tokens import TokenStore
    c = TestClient(create_app(OllamaClient('http://ollama', 'm', transport=httpx.MockTransport(lambda r: reply())), 'admin', TokenStore(tmp_path / 't.json')))
    path = "/v1/ask" if "MINICPM" == "LAYA" else "/v1/chat"
    admin = {"X-Internal-Token": "admin"}
    assert c.get("/admin/tokens").status_code == 401
    made = c.post("/admin/tokens", json={"name": "proj"}, headers=admin).json()
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["last_used"] is None
    use = {"X-Internal-Token": made["token"]}
    assert c.post(path, json=BODY, headers=use).status_code == 200
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["last_used"] is not None
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["requests"] == 1
    assert c.get("/admin/tokens", headers=use).status_code == 401  # a project token is not an admin
    assert "token" not in c.get("/admin/tokens", headers=admin).json()["tokens"][0]
    assert c.delete(f"/admin/tokens/{made['id']}", headers=admin).status_code == 200
    assert c.post(path, json=BODY, headers=use).status_code == 401


def test_num_thread_is_sent_to_ollama_reported_and_set_by_the_admin():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply()

    c = client(handler, "admin")
    assert c.get("/health").json()["num_thread"] is None
    assert c.put("/admin/threads", json={"num_thread": 2}).status_code == 401
    assert c.put("/admin/threads", json={"num_thread": 0}, headers={"X-Internal-Token": "admin"}).status_code == 422
    assert c.put("/admin/threads", json={"num_thread": 2}, headers={"X-Internal-Token": "admin"}).json() == {"num_thread": 2}
    assert c.get("/health").json()["num_thread"] == 2
    made = c.post("/admin/tokens", json={"name": "p"}, headers={"X-Internal-Token": "admin"}).json()
    c.post("/v1/chat", json=BODY, headers={"X-Internal-Token": made["token"]})
    assert seen["options"]["num_thread"] == 2



def test_workers_are_read_only_and_admin_only(monkeypatch):
    monkeypatch.setenv("MINICPM_REPLICAS", "3")
    monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
    c = client(lambda request: reply(), "admin")
    assert c.get("/admin/workers").status_code == 401
    assert c.get("/admin/workers", headers={"X-Internal-Token": "admin"}).json() == {"replicas": 3, "ollama_parallel": None}


def test_saved_threads_are_loaded_only_by_main(tmp_path, monkeypatch):
    import app
    import settings
    from tokens import TokenStore
    store = TokenStore(tmp_path / "tokens.json")
    settings.save(store.settings_path, num_thread=7)
    monkeypatch.setenv("MINICPM_TOKENS_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MINICPM_NUM_THREAD", "2")
    backend = OllamaClient("http://ollama", "m")
    create_app(backend, tokens=store)
    assert backend.num_thread is None
    captured = {}
    monkeypatch.setattr(app, "create_app", lambda **kw: captured.update(kw) or object())
    monkeypatch.setattr("uvicorn.run", lambda *args, **kw: None)
    app.main()
    assert captured["client"].num_thread == 7


class WarmClient:
    model = "m"
    num_thread = None

    def __init__(self, fail=False):
        self.fail = fail
        self.loaded = 0
        self.closed = False

    async def warm(self):
        self.loaded += 1
        if self.fail:
            raise OSError("Ollama unavailable")

    async def close(self):
        self.closed = True


def test_startup_warms_model_and_closes_client():
    backend = WarmClient()
    with TestClient(create_app(backend, warm=True)):
        assert backend.loaded == 1
    assert backend.closed


def test_warmup_failure_is_logged_without_stopping_service(caplog):
    backend = WarmClient(fail=True)
    with TestClient(create_app(backend, warm=True)) as http:
        assert http.get("/admin/").status_code == 200
    assert "Ollama unavailable" in caplog.text
    assert backend.closed


def test_warmup_is_optional():
    backend = WarmClient()
    with TestClient(create_app(backend, warm=False)):
        assert backend.loaded == 0
    assert backend.closed


def test_warmup_sends_empty_chat_with_long_keep_alive():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return reply()
    backend = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
    with TestClient(create_app(backend, warm=True)):
        pass
    assert len(seen) == 1
    assert seen[0]["model"] == "m" and seen[0]["messages"] == []
    assert seen[0]["keep_alive"] == "30m" and seen[0]["stream"] is False


@pytest.mark.parametrize("environment,expected", [({}, (True, 6)),
    ({"MINICPM_WARM": "0", "MINICPM_UPDATE_CHECK_HOURS": "0"}, (False, 0))])
def test_main_wires_warmup_and_update_hours(monkeypatch, environment, expected):
    import app
    captured = {}
    for name in ("MINICPM_WARM", "MINICPM_UPDATE_CHECK_HOURS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(app, "create_app", lambda **kw: captured.update(kw) or object())
    monkeypatch.setattr("uvicorn.run", lambda *args, **kw: None)
    app.main()
    assert (captured["warm"], captured["check_hours"]) == expected
