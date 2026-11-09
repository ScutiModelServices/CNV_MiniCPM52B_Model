"""The Ollama client itself, against a mock transport: update checks, the model inventory and error mapping.
The HTTP service on top of it is covered in test_app.py."""

import asyncio
import hashlib
import json

import httpx
import pytest

from ollama import OllamaClient, OllamaError

MANIFEST = json.dumps({"schemaVersion": 2, "config": {}, "layers": []}).encode()


def make(handler, model="m"):
    return OllamaClient("http://ollama", model, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def tags(*entries):
    return httpx.Response(200, json={"models": [{"name": name, "digest": digest} for name, digest in entries]})


def test_the_local_digest_is_read_from_the_tags_without_its_prefix():
    client = make(lambda request: tags(("other", "sha256:aa"), ("m:latest", "sha256:ABC123")))
    assert run(client.cached_sha()) == "abc123"
    assert run(make(lambda request: tags(("other", "sha256:aa"))).cached_sha()) is None  # not pulled yet
    with pytest.raises(OllamaError):
        run(make(lambda request: tags(("m", ""))).cached_sha())


def test_the_remote_digest_is_the_hash_of_the_registry_manifest_and_the_url_follows_the_model_name():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=MANIFEST)

    assert run(make(handler, "minicpm5").remote_sha()) == hashlib.sha256(MANIFEST).hexdigest()
    run(make(handler, "someone/model:q4").remote_sha())
    run(make(handler, "hf.co/user/repo:Q4_K_M").remote_sha())
    assert seen == ["https://registry.ollama.ai/v2/library/minicpm5/manifests/latest",
                    "https://registry.ollama.ai/v2/someone/model/manifests/q4",
                    "https://hf.co/v2/user/repo/manifests/Q4_K_M"]


def test_an_invalid_registry_manifest_is_refused():
    with pytest.raises(OllamaError):
        run(make(lambda request: httpx.Response(200, json={"schemaVersion": 1})).remote_sha())


def test_pull_needs_a_success_status_and_unload_drops_the_resident_model_and_changes_the_generation():
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "success"} if request.url.path == "/api/pull" else {})

    client = make(handler)
    run(client.pull())
    before = client.generation
    run(client.unload())
    assert client.generation == before + 1
    assert sent[0] == ("/api/pull", {"model": "m", "stream": True})
    assert sent[1][0] == "/api/chat" and sent[1][1]["keep_alive"] == 0 and sent[1][1]["messages"] == []
    with pytest.raises(OllamaError):
        run(make(lambda request: httpx.Response(200, json={"status": "pulling"})).pull())


def test_the_inventory_is_kept_for_max_age_and_records_digests():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return tags(("m:latest", "sha256:aa"), ("minicpm-p", "sha256:bb"))

    client = make(handler)
    assert run(client.installed_models()) == {"m:latest", "minicpm-p"}
    run(client.installed_models())
    assert calls == ["/api/tags"]  # the second call came from the kept inventory
    run(client.installed_models(max_age=0))
    assert len(calls) == 2
    assert client.digest("m") == "sha256:aa" and client.digest("minicpm-p") == "sha256:bb" and client.digest("nope") is None
    assert run(client.model_ready()) is True


def test_a_failed_inventory_lookup_is_an_error_and_is_not_kept():
    state = {"up": False}

    def handler(request):
        return tags(("m", "d")) if state["up"] else httpx.Response(500)

    client = make(handler)
    with pytest.raises(OllamaError) as down:
        run(client.installed_models())
    assert down.value.status == 500 and run(client.model_ready()) is False
    state["up"] = True
    assert run(client.model_ready()) is True  # the failure was not cached
    for bad in ({"models": "x"}, {"models": [{"digest": "d"}]}, {}):
        with pytest.raises(OllamaError):
            run(make(lambda request, bad=bad: httpx.Response(200, json=bad)).installed_models())


def test_transport_failures_map_to_ollama_errors_with_a_status():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(OllamaError) as slow:
        run(make(timeout).chat([], tools=None, schema=None, thinking=False, temperature=0, max_tokens=1))
    assert slow.value.status == 504
    with pytest.raises(OllamaError) as refused:
        run(make(down).unload())
    assert "could not be reached" in str(refused.value)


def test_an_error_body_from_ollama_is_passed_on_with_its_status():
    client = make(lambda request: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(OllamaError) as error:
        run(client.chat([], tools=None, schema=None, thinking=False, temperature=0, max_tokens=1))
    assert error.value.status == 404 and "model not found" in str(error.value)


def test_a_stream_yields_chunks_and_an_error_chunk_stops_it():
    def handler(request):
        lines = [{"message": {"content": "a"}}, {"message": {"content": "b"}, "done": True}]
        return httpx.Response(200, content="\n".join(json.dumps(line) for line in lines) + "\n\n")

    async def collect(client):
        return [chunk async for chunk in client.chat_stream([], tools=None, schema=None, thinking=False, temperature=0, max_tokens=5)]

    assert [chunk["message"]["content"] for chunk in run(collect(make(handler)))] == ["a", "b"]
    broken = make(lambda request: httpx.Response(200, content=json.dumps({"error": "boom"}) + "\n"))
    with pytest.raises(OllamaError) as error:
        run(collect(broken))
    assert "boom" in str(error.value)
    with pytest.raises(OllamaError) as refused:
        run(collect(make(lambda request: httpx.Response(503, json={"error": "loading"}))))
    assert refused.value.status == 503


def test_the_chat_body_carries_the_thread_count_tools_and_schema_only_when_set():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "x"}})

    client = make(handler)
    run(client.chat([{"role": "user", "content": "hi"}], tools=None, schema=None, thinking=False, temperature=0.5, max_tokens=7))
    client.num_thread = 3
    run(client.chat([], tools=[{"type": "function"}], schema={"type": "object"}, thinking=True, temperature=0, max_tokens=1, model="other"))
    plain, full = bodies
    assert plain["options"] == {"temperature": 0.5, "num_predict": 7} and "tools" not in plain and "format" not in plain and plain["think"] is False
    assert full["model"] == "other" and full["options"]["num_thread"] == 3 and full["tools"] and full["format"] == {"type": "object"}
