import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from ollama import OllamaClient

BODY = {"messages": [{"role": "user", "content": "hi"}]}


def client(handler, admin_password=None, **options):
    ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
    test_client = TestClient(create_app(ollama, admin_password, **options))
    test_client.ollama = ollama
    return test_client


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
    assert ready.get("/health").json() == {"status": "ok", "model": "m", "ready": True, "num_thread": None, "project_models": []}
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
    config = captured["config"]
    assert (config.get("warm"), config.get("update_check_hours")) == expected


def test_a_token_limited_to_projects_refuses_other_projects(tmp_path):
    from tokens import TokenStore

    def handler(request):
        return httpx.Response(200, json={"models": []}) if request.url.path == "/api/tags" else reply()

    ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
    c = TestClient(create_app(ollama, "admin", tokens=TokenStore(tmp_path / "t.json")))
    admin = {"X-Internal-Token": "admin"}
    assert c.post("/admin/tokens", json={"name": "x", "projects": ["Bad Name"]}, headers=admin).status_code == 422
    made = c.post("/admin/tokens", json={"name": "p", "projects": ["app-b", "app-a"]}, headers=admin).json()
    assert made["projects"] == ["app-a", "app-b"]
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["projects"] == ["app-a", "app-b"]
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/chat", json={**BODY, "project": "app-a"}, headers=use).status_code == 200
    assert c.post("/v1/chat", json={**BODY, "project": "other"}, headers=use).status_code == 403
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 200  # no project is always allowed
    open_token = c.post("/admin/tokens", json={"name": "all"}, headers=admin).json()["token"]
    assert c.post("/v1/chat", json={**BODY, "project": "other"}, headers={"X-Internal-Token": open_token}).status_code == 200


def test_rate_limit_edit_stats_and_metrics(tmp_path):
    from tokens import TokenStore
    store = TokenStore(tmp_path / "t.json")
    ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(lambda request: reply()))
    c = TestClient(create_app(ollama, "admin", tokens=store))
    admin = {"X-Internal-Token": "admin"}
    made = c.post("/admin/tokens", json={"name": "p", "rate": 1}, headers=admin).json()
    assert made["rate"] == 1
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 200
    limited = c.post("/v1/chat", json=BODY, headers=use)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert c.put(f"/admin/tokens/{made['id']}", json={"projects": ["a"], "rate": None}, headers=admin).status_code == 200
    assert c.post("/v1/chat", json=BODY, headers=use).status_code == 200  # the limit is gone
    assert c.put("/admin/tokens/nope", json={}, headers=admin).status_code == 404
    row = store.list()[0]
    assert row["projects"] == ["a"] and "rate" not in row and row["timed"] >= 1
    assert c.get("/metrics").status_code == 401
    assert 'minicpm_requests_total{token="p"}' in c.get("/metrics", headers=admin).text
    assert 'minicpm_request_seconds_bucket{le="+Inf"}' in c.get("/metrics", headers=admin).text


def test_requests_over_the_in_flight_limit_get_429():
    import asyncio

    async def main():
        release = asyncio.Event()

        async def handler(request):
            await release.wait()
            return reply()

        ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
        app = create_app(ollama, max_inflight=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc") as http:
            first = asyncio.create_task(http.post("/v1/chat", json=BODY))
            await asyncio.sleep(0.2)
            second = await http.post("/v1/chat", json=BODY)
            release.set()
            return second, await first

    second, first = asyncio.run(main())
    assert second.status_code == 429 and second.headers["Retry-After"] == "5"
    assert first.status_code == 200


def test_a_full_ollama_queue_becomes_429():
    response = client(lambda request: httpx.Response(503, text="server busy")).post("/v1/chat", json=BODY)
    assert response.status_code == 429 and response.headers["Retry-After"] == "5"


def test_settings_are_admin_only_checked_and_apply_live(tmp_path):
    from options import SETTINGS
    from settings import Config
    from tokens import TokenStore
    config = Config(tmp_path / "s.json", SETTINGS)
    models = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "acme-demo"}]})
        models.append(json.loads(request.content)["model"])
        return reply()

    ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
    c = TestClient(create_app(ollama, "admin", tokens=TokenStore(tmp_path / "t.json"), config=config))
    admin = {"X-Internal-Token": "admin"}
    assert c.get("/admin/settings").status_code == 401
    rows = {row["key"]: row for row in c.get("/admin/settings", headers=admin).json()["settings"]}
    assert rows["project_prefix"]["value"] == "minicpm-" and rows["model"]["restart"] is True
    use = {"X-Internal-Token": c.post("/admin/tokens", json={"name": "p"}, headers=admin).json()["token"]}
    put = lambda values: c.put("/admin/settings", json={"values": values}, headers=admin)  # noqa: E731
    assert c.post("/v1/chat", json={**BODY, "project": "demo"}, headers=use).json()["fallback"] is True
    assert put({"project_prefix": "acme-"}).status_code == 200
    body = c.post("/v1/chat", json={**BODY, "project": "demo"}, headers=use).json()
    assert body["model"] == "acme-demo" and body["fallback"] is False
    assert put({"max_inflight": -1}).status_code == 422 and put({"model": ""}).status_code == 422
    assert put({"nope": 1}).status_code == 422
    assert put({"project_prefix": None}).json()["settings"][2]["source"] == "default"
    fixed = TestClient(create_app(OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler)), "admin"))
    assert fixed.get("/admin/settings", headers=admin).json() == {"settings": []}
    assert fixed.put("/admin/settings", json={"values": {}}, headers=admin).status_code == 409


def test_a_saved_thread_count_reaches_other_replicas(tmp_path):
    import time
    import settings
    from options import SETTINGS
    from tokens import TokenStore
    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(lambda request: reply()))
    with TestClient(create_app(ollama, "admin", tokens=store, config=config)):
        settings.save(store.settings_path, num_thread=6)
        for _ in range(300):
            if ollama.num_thread == 6:
                break
            time.sleep(0.02)
    assert ollama.num_thread == 6


def pieces(*chunks):
    """An Ollama streaming answer: one JSON object per line."""
    return httpx.Response(200, content="".join(json.dumps(chunk) + "\n" for chunk in chunks).encode())


def events(response):
    return [json.loads(text) for text in response.text.splitlines()]


def part(content="", thinking="", **extra):
    return {"message": {"role": "assistant", "content": content, **({"thinking": thinking} if thinking else {}), **extra}, "done": False}


FINAL = {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 3, "eval_count": 5}


def test_stream_sends_json_lines_then_the_same_body_as_a_call_without_stream():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return pieces(part(thinking="hm"), part("hel"), part("lo"), FINAL)

    reply = client(handler).post("/v1/chat", json={**BODY, "stream": True})
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("application/x-ndjson")
    assert seen["stream"] is True
    sent = events(reply)
    assert [(e["type"], e.get("text")) for e in sent[:-1]] == [("thinking", "hm"), ("content", "hel"), ("content", "lo")]
    done = sent[-1]
    assert done["type"] == "done" and done["content"] == "hello" and done["thinking"] == "hm" and done["finish_reason"] == "stop"
    assert done["usage"] == {"input_tokens": 3, "output_tokens": 5} and done["model"] == "m" and done["fallback"] is False


def test_stream_collects_tool_calls_and_a_schema_answer():
    call = {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}
    done = events(client(lambda r: pieces(part(tool_calls=[call]), FINAL)).post("/v1/chat", json={**BODY, "tools": [WEATHER], "stream": True}))[-1]
    assert done["finish_reason"] == "tool_calls" and done["tool_calls"] == [{"id": "call_0", "name": "get_weather", "arguments": {"city": "Paris"}}]
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    done = events(client(lambda r: pieces(part('{"ok":'), part(" true}"), FINAL)).post("/v1/chat", json={**BODY, "schema": schema, "stream": True}))[-1]
    assert done["parsed"] == {"ok": True}


def test_stream_errors_before_the_first_piece_are_http_errors_and_later_ones_are_error_lines():
    assert client(lambda r: httpx.Response(400, json={"error": "bad request"})).post("/v1/chat", json={**BODY, "stream": True}).status_code == 422
    assert client(lambda r: httpx.Response(503, json={"error": "queue full"})).post("/v1/chat", json={**BODY, "stream": True}).status_code == 429
    broken = client(lambda r: pieces(part("par"), {"error": "model crashed"})).post("/v1/chat", json={**BODY, "stream": True})
    assert broken.status_code == 200 and events(broken)[0] == {"type": "content", "text": "par"}
    assert events(broken)[-1]["type"] == "error" and "model crashed" in events(broken)[-1]["message"]
    bad_json = events(client(lambda r: pieces(part("not json"), FINAL)).post("/v1/chat", json={**BODY, "schema": {"type": "object"}, "stream": True}))
    assert bad_json[-1]["type"] == "error" and bad_json[-1]["status"] == 502 and bad_json[-1]["detail"]["content"] == "not json"


def cached(chat_reply=None, digest="d1"):
    """A client with the cache on and an Ollama that counts its chat calls and lists the model with a digest."""
    state = {"chats": 0, "digest": digest}

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m", "digest": state["digest"]}]})
        state["chats"] += 1
        return chat_reply(request) if chat_reply else reply(f"answer {state['chats']}")

    return client(handler, cache_size=8), state


def test_the_same_request_is_answered_from_the_cache():
    c, state = cached()
    first, second = c.post("/v1/chat", json=BODY), c.post("/v1/chat", json=BODY)
    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("miss", "hit")
    assert second.json()["content"] == first.json()["content"] == "answer 1" and state["chats"] == 1
    c.post("/v1/chat", json={**BODY, "thinking": False})  # any change in the request is another answer
    c.post("/v1/chat", json={**BODY, "max_tokens": 7})
    c.post("/v1/chat", json={"messages": [{"role": "user", "content": "other"}]})
    assert state["chats"] == 4


def test_only_temperature_zero_is_kept_and_the_cache_can_be_off():
    c, state = cached()
    for _ in range(2):
        assert c.post("/v1/chat", json={**BODY, "temperature": 0.5}).headers["X-Cache"] == "miss"
    assert state["chats"] == 2
    off = client(lambda r: reply())  # no cache_size: off
    assert [off.post("/v1/chat", json=BODY).headers["X-Cache"] for _ in range(2)] == ["miss", "miss"]


def test_a_replaced_model_is_not_answered_from_the_old_models_cache():
    c, state = cached()
    c.post("/v1/chat", json=BODY)
    assert c.post("/v1/chat", json=BODY).headers["X-Cache"] == "hit"
    state["digest"] = "d2"  # the model was pulled again
    c.ollama._tags_at = None  # the service asks Ollama for the inventory at most every 30 s; make that moment come
    assert c.post("/v1/chat", json=BODY).headers["X-Cache"] == "miss"
    c.ollama.generation += 1  # unloaded for new weights on this replica
    assert c.post("/v1/chat", json=BODY).headers["X-Cache"] == "miss"


def test_stream_fills_the_cache_and_a_kept_answer_streams_back_in_one_piece():
    c, state = cached(lambda r: pieces(part("one "), part("two"), FINAL))
    streamed = c.post("/v1/chat", json={**BODY, "stream": True})
    kept = c.post("/v1/chat", json={**BODY, "stream": True})
    assert (streamed.headers["X-Cache"], kept.headers["X-Cache"]) == ("miss", "hit") and state["chats"] == 1
    sent = events(kept)
    assert [e["type"] for e in sent] == ["content", "done"] and sent[0]["text"] == "one two" and sent[1]["content"] == "one two"
    assert events(streamed)[-1]["content"] == "one two"


def test_cache_size_is_a_setting_that_applies_live(tmp_path):
    import settings
    from options import SETTINGS
    from tokens import TokenStore

    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    c = client(lambda r: httpx.Response(200, json={"models": [{"name": "m", "digest": "d"}]}) if r.url.path == "/api/tags" else reply(),
               admin_password="admin", tokens=store, config=config)
    admin = {"X-Internal-Token": "admin"}
    use = {"X-Internal-Token": c.post("/admin/tokens", json={"name": "t"}, headers=admin).json()["token"]}
    assert [c.post("/v1/chat", json=BODY, headers=use).headers["X-Cache"] for _ in range(2)] == ["miss", "hit"]
    assert c.put("/admin/settings", json={"values": {"cache_size": 0}}, headers=admin).status_code == 200
    assert c.post("/v1/chat", json=BODY, headers=use).headers["X-Cache"] == "miss"
