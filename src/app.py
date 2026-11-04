"""HTTP face of MiniCPM5-2B (served by Ollama).

POST /v1/chat {"messages", "tools", "schema", "thinking", "temperature", "max_tokens"} runs one model turn. With
`tools` the model may answer with `tool_calls` for the caller to run; the caller appends the results as `tool`
messages and calls again, so the loop lives in the caller. With a JSON `schema` the reply is constrained to it and
returned parsed. Thinking is on unless the caller turns it off. GET /health reports whether Ollama has the model."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from pathlib import Path
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from cache import AnswerCache
from ollama import OllamaClient, OllamaError
from options import DEFAULT_MODEL, SETTINGS
import settings
from tokens import LatencyHistogram, TokenStore
from updates import Updates

_log = logging.getLogger(__name__)

PROJECT_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,47}$"


class ToolCall(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """`tool_calls` belongs to an assistant turn; a `tool` message carries one call's result (`tool_name` names it)."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field("", max_length=100_000)
    tool_calls: list[ToolCall] | None = None
    tool_name: str | None = Field(None, max_length=64)


class Tool(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    description: str = Field("", max_length=2000)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    messages: list[Message] = Field(min_length=1, max_length=64)
    project: str | None = Field(None, pattern=PROJECT_PATTERN)
    tools: list[Tool] | None = Field(None, max_length=32)
    json_schema: dict[str, Any] | None = Field(None, alias="schema")
    thinking: bool = True
    temperature: float = Field(0.0, ge=0, le=2)
    max_tokens: int = Field(2048, ge=1, le=8192)
    stream: bool = False  # true: the answer arrives as JSON lines while it is made


def _ollama_message(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [{"function": {"name": call.name, "arguments": call.arguments}} for call in message.tool_calls]
    if message.tool_name:
        out["tool_name"] = message.tool_name
    return out


def _ollama_tool(tool: Tool) -> dict[str, Any]:
    return {"type": "function", "function": tool.model_dump()}


class SettingsUpdate(BaseModel):
    values: dict[str, Any]  # None resets an option to its environment variable or default


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    projects: list[Annotated[str, Field(pattern=PROJECT_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)  # calls per minute
    expires_days: int | None = Field(None, ge=1, le=3650)


class TokenScope(BaseModel):
    projects: list[Annotated[str, Field(pattern=PROJECT_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)


class Threads(BaseModel):
    num_thread: int = Field(ge=1, le=256)


def create_app(client: OllamaClient | None = None, admin_password: str | None = None,
               tokens: TokenStore | None = None, warm: bool = False,
               updates: Updates | None = None, check_hours: float = 0, max_inflight: int | None = None,
               cache_size: int | None = None, config: settings.Config | None = None) -> FastAPI:
    """`admin_password` (MINICPM_ADMIN_PASSWORD) unlocks /admin/, where each project gets a token of its own; when it
    is set, every call except /health must send one of those tokens in the X-Internal-Token header. Unset = no
    auth at all (local use only). `tokens` holds the project tokens (MINICPM_TOKENS_FILE). `cache_size` > 0 keeps that
    many answers per replica (MINICPM_CACHE_SIZE). `config` (the admin page's settings) replaces `warm`, `check_hours`,
    `max_inflight` and `cache_size` and keeps them live; without it they stay fixed."""
    tokens = tokens or TokenStore(os.environ.get("MINICPM_TOKENS_FILE", "tokens.json"))
    client = client or OllamaClient(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                                    config.get("model") if config else os.environ.get("MINICPM_MODEL", DEFAULT_MODEL))

    def prefix() -> str:
        return config.get("project_prefix") if config else os.environ.get("MINICPM_PROJECT_PREFIX", "minicpm-")

    inflight = 0
    cache = AnswerCache()

    def cache_limit() -> int:
        """Answers this replica keeps; 0 = off."""
        return config.get("cache_size") if config else (
            cache_size if cache_size is not None else int(os.environ.get("MINICPM_CACHE_SIZE") or 0))

    def inflight_limit() -> int:
        """Calls this replica lets wait on Ollama at once; more get 429. 0 = 4 x OLLAMA_NUM_PARALLEL."""
        value = config.get("max_inflight") if config else (
            max_inflight if max_inflight is not None else int(os.environ.get("MINICPM_MAX_INFLIGHT") or 0))
        return value or 4 * int(os.environ.get("OLLAMA_NUM_PARALLEL") or 2)

    updates = updates or Updates(client.model, cached_sha=lambda: client.cached_sha(), remote_sha=lambda: client.remote_sha(),
                                 download=lambda: client.pull(), unload=lambda: client.unload())

    async def check_forever() -> None:
        """Checks every `update_check_hours` (0 = off). The value is read again every second, so the admin page can change it."""
        last = None
        while True:
            wait = (config.get("update_check_hours") if config else check_hours) * 3600
            now = time.monotonic()
            if wait > 0 and (last is None or now - last >= wait):
                last = now
                try:
                    status = await updates.check()
                    if status["error"]:
                        _log.warning("Update check failed: %s", status["error"])
                except Exception:
                    _log.exception("Update check failed")
            await asyncio.sleep(min(wait, 1.0) if wait > 0 else 1.0)

    async def threads_forever() -> None:
        """Applies a CPU thread count saved from another replica's admin page."""
        while True:
            saved = settings.load(tokens.settings_path).get("num_thread")
            if saved and saved != client.num_thread:
                client.num_thread = int(saved)
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background: list[asyncio.Task] = []
        try:
            if config.get("warm") if config else warm:
                try:
                    await client.warm()
                except Exception:
                    _log.exception("Ollama warm-up failed; continuing startup")
            if config or check_hours > 0:
                background.append(asyncio.create_task(check_forever()))
            if config:
                background.append(asyncio.create_task(threads_forever()))
            yield
        finally:
            for task in background:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.close()

    def authorize(request: Request, x_internal_token: str = Header("")) -> list[str] | None:
        """The projects the token is limited to, or None for all. Unset admin password = no auth."""
        if not admin_password:
            return None
        row = tokens.lookup(x_internal_token)
        if row is None:
            raise HTTPException(401, "Invalid token")
        wait = tokens.throttle(row)
        if wait:
            raise HTTPException(429, "Too many requests for this token", headers={"Retry-After": str(wait)})
        request.state.token_id = row["id"]
        return row.get("projects")

    def check_scope(allowed: list[str] | None, project: str | None) -> None:
        if project is not None and allowed is not None and project not in allowed:
            raise HTTPException(403, f"This token may not use project {project!r}")

    def admin(x_internal_token: str = Header("")) -> None:
        if not admin_password or not hmac.compare_digest(x_internal_token, admin_password):
            raise HTTPException(401, "Admin password required (set MINICPM_ADMIN_PASSWORD)")

    app = FastAPI(title="MiniCPM5-2B service", lifespan=lifespan)

    async def slot():
        nonlocal inflight
        limit = inflight_limit()
        if limit and inflight >= limit:
            raise HTTPException(429, "MiniCPM is busy: too many requests waiting", headers={"Retry-After": "5"})
        inflight += 1
        try:
            yield
        finally:
            inflight -= 1

    async def installed(name: str) -> bool:
        """Is `name` pulled? A miss re-asks Ollama (at most every 2 s) so a model made just now is found.
        If Ollama cannot list its models, say no: the caller serves the base model, whose own call reports a real outage."""
        try:
            for max_age in (30.0, 2.0):
                names = await client.installed_models(max_age)
                if name in names or f"{name}:latest" in names:
                    return True
        except OllamaError:
            pass
        return False

    @app.get("/health")
    async def health() -> dict[str, Any]:
        try:
            names = await client.installed_models(max_age=0)
        except OllamaError:
            names = frozenset()
        current = prefix()
        projects = {name.removesuffix(":latest") for name in names
                    if name.startswith(current)
                    and re.fullmatch(PROJECT_PATTERN, name.removesuffix(":latest")[len(current):])}
        return {"status": "ok", "model": client.model,
                "ready": client.model in names or f"{client.model}:latest" in names,
                "num_thread": client.num_thread, "project_models": sorted(projects)}

    def http_error(error: OllamaError) -> HTTPException:
        if error.status == 400:
            return HTTPException(422, str(error))
        if error.status == 503:  # Ollama's own queue is full
            return HTTPException(429, str(error), headers={"Retry-After": "5"})
        return HTTPException(504 if error.status == 504 else 502, str(error))

    async def cache_key(body: ChatRequest, model: str) -> str:
        """Everything that shapes the answer. The model's digest (from the inventory, at most 30 s old) is in it, so a
        project model that was replaced is not answered from the old model's cache."""
        try:
            await client.installed_models(30.0)
        except OllamaError:
            pass
        part = [client.generation, client.digest(model), model, [m.model_dump() for m in body.messages],
                [t.model_dump() for t in body.tools or []], body.json_schema, body.thinking, body.max_tokens]
        return hashlib.sha256(json.dumps(part, sort_keys=True, default=str).encode()).hexdigest()

    def line(event: dict[str, Any]) -> str:
        return json.dumps(event, ensure_ascii=False) + "\n"

    def ndjson(lines: Any, cached: str) -> StreamingResponse:
        return StreamingResponse(lines, media_type="application/x-ndjson", headers={"X-Cache": cached})

    async def replay(result: dict[str, Any]):
        """A kept answer in the shape of a stream: the whole text in one piece, then `done`."""
        if result.get("thinking"):
            yield line({"type": "thinking", "text": result["thinking"]})
        if result["content"]:
            yield line({"type": "content", "text": result["content"]})
        yield line({"type": "done", **result})

    async def live_lines(chunks: Any, first: dict[str, Any] | None, model: str, fallback: bool, started: float,
                         schema: dict[str, Any] | None, key: str | None, size: int):
        """One JSON line per piece of the answer, then `done` with the same body a call without `stream` returns.
        A failure after the first piece can only be sent as an `error` line."""
        content: list[str] = []
        thinking: list[str] = []
        calls: list[dict[str, Any]] = []
        last: dict[str, Any] = {}
        try:
            async def pieces():
                if first is not None:
                    yield first
                async for chunk in chunks:
                    yield chunk

            async for chunk in pieces():
                message = chunk.get("message") or {}
                for field, parts in (("thinking", thinking), ("content", content)):
                    if message.get(field):
                        parts.append(message[field])
                        yield line({"type": field, "text": message[field]})
                calls.extend(message.get("tool_calls") or [])
                if chunk.get("done"):
                    last = chunk
            reply = {"message": {"content": "".join(content), "thinking": "".join(thinking) or None, "tool_calls": calls},
                     "done_reason": last.get("done_reason"), "prompt_eval_count": last.get("prompt_eval_count", 0),
                     "eval_count": last.get("eval_count", 0)}
            result = build(reply, model, fallback, started, schema)
        except OllamaError as error:
            yield line({"type": "error", "status": http_error(error).status_code, "message": str(error)})
            return
        except HTTPException as error:
            yield line({"type": "error", "status": error.status_code, "detail": error.detail})
            return
        except Exception:
            _log.exception("MiniCPM chat failed")
            yield line({"type": "error", "status": 500, "message": "MiniCPM could not answer"})
            return
        finally:
            await chunks.aclose()
        if key:
            cache.put(key, result, size)
        yield line({"type": "done", **result})

    def build(reply: dict[str, Any], model: str, fallback: bool, started: float, schema: dict[str, Any] | None) -> dict[str, Any]:
        message = reply.get("message") or {}
        content = message.get("content") or ""
        calls = [{"id": call.get("id") or f"call_{index}", "name": call["function"]["name"],
                  "arguments": call["function"].get("arguments") or {}}
                 for index, call in enumerate(message.get("tool_calls") or [])]
        # `length`: the token budget ran out, often inside the thinking, so `content` may be empty.
        finish = "tool_calls" if calls else "length" if reply.get("done_reason") == "length" else "stop"
        result: dict[str, Any] = {
            "content": content, "thinking": message.get("thinking") or None, "tool_calls": calls,
            "finish_reason": finish, "model": model, "fallback": fallback,
            "usage": {"input_tokens": reply.get("prompt_eval_count", 0), "output_tokens": reply.get("eval_count", 0)},
            "duration_ms": round((time.perf_counter() - started) * 1000)}
        if schema is not None:
            try:
                result["parsed"] = json.loads(content)
            except ValueError:
                # Cut off by max_tokens or malformed; the caller can still see `content`.
                raise HTTPException(502, {"message": "The model did not return valid JSON", "content": content}) from None
        return result

    @app.post("/v1/chat")
    async def chat(body: ChatRequest, response: Response, allowed: list[str] | None = Depends(authorize),
                   _slot: None = Depends(slot)) -> Any:
        check_scope(allowed, body.project)
        if body.tools and body.json_schema is not None:
            raise HTTPException(422, "Send either tools or a schema, not both")
        started = time.perf_counter()
        size = cache_limit() if body.temperature == 0 else 0  # a higher temperature asks for a different answer each time
        options: dict[str, Any] = dict(tools=[_ollama_tool(t) for t in body.tools] if body.tools else None,
                                       schema=body.json_schema, thinking=body.thinking,
                                       temperature=body.temperature, max_tokens=body.max_tokens)
        try:
            model, fallback = client.model, False
            if body.project is not None:
                candidate = prefix() + body.project
                if await installed(candidate):
                    model = candidate
                else:
                    fallback = True
            key = await cache_key(body, model) if size else None
            kept = cache.get(key, size) if key else None
            if kept is not None:
                kept = {**kept, "duration_ms": round((time.perf_counter() - started) * 1000)}
                if body.stream:
                    return ndjson(replay(kept), "hit")
                response.headers["X-Cache"] = "hit"
                return kept
            messages = [_ollama_message(m) for m in body.messages]
            if body.stream:
                chunks = client.chat_stream(messages, model=model, **options)
                try:
                    first = await anext(chunks, None)  # the first piece is waited for here, so a refusal is still an HTTP error
                except BaseException:
                    await chunks.aclose()
                    raise
                return ndjson(live_lines(chunks, first, model, fallback, started, body.json_schema, key, size), "miss")
            reply = await client.chat(messages, model=model, **options)
        except OllamaError as error:
            raise http_error(error) from None
        except Exception:
            _log.exception("MiniCPM chat failed")
            raise HTTPException(500, "MiniCPM could not answer") from None
        result = build(reply, model, fallback, started, body.json_schema)
        if key:
            cache.put(key, result, size)
        response.headers["X-Cache"] = "miss"
        return result

    @app.put("/admin/threads", dependencies=[Depends(admin)])
    def set_threads(body: Threads) -> dict[str, Any]:
        """Applies to this replica now and is saved; the other replicas apply it within a few seconds. It beats MINICPM_NUM_THREAD."""
        client.num_thread = body.num_thread
        settings.save(tokens.settings_path, num_thread=body.num_thread)
        return {"num_thread": client.num_thread}

    @app.get("/admin/workers", dependencies=[Depends(admin)])
    def workers() -> dict[str, Any]:
        """Read-only: replicas and Ollama's parallel requests are Docker Compose settings the service cannot change."""
        def number(name: str) -> int | None:
            value = os.environ.get(name, "").strip()
            return int(value) if value.isdigit() else None

        return {"replicas": number("MINICPM_REPLICAS"), "ollama_parallel": number("OLLAMA_NUM_PARALLEL")}

    @app.get("/admin/updates", dependencies=[Depends(admin)])
    async def update_status() -> dict[str, Any]:
        """Return the last check without I/O; zero check hours disables only the timer."""
        return {"enabled": True, **updates.status()}

    @app.post("/admin/updates/check", dependencies=[Depends(admin)])
    async def check_updates() -> dict[str, Any]:
        return {"enabled": True, **await updates.check()}

    @app.post("/admin/updates/weights", dependencies=[Depends(admin)])
    async def update_weights() -> dict[str, Any]:
        try:
            return {"enabled": True, **await updates.update_weights()}
        except Exception as error:
            _log.exception("Weight update failed")
            raise HTTPException(502, f"Weight update failed: {error}") from None

    @app.get("/admin/tokens", dependencies=[Depends(admin)])
    def list_tokens() -> dict[str, Any]:
        return {"tokens": tokens.list()}

    @app.post("/admin/tokens", dependencies=[Depends(admin)])
    def create_token(body: NewToken) -> dict[str, Any]:
        return tokens.create(body.name.strip(), body.projects, body.rate, body.expires_days)

    @app.get("/admin/settings", dependencies=[Depends(admin)])
    def get_settings() -> dict[str, Any]:
        return {"settings": config.describe() if config else []}

    @app.put("/admin/settings", dependencies=[Depends(admin)])
    def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        """Saves options; null resets one. Every replica reads the shared file, so a change applies everywhere within
        a second, except options marked `restart`."""
        if config is None:
            raise HTTPException(409, "Settings are fixed in this setup")
        try:
            config.set(body.values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"settings": config.describe()}

    @app.put("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def update_token(token_id: str, body: TokenScope) -> dict[str, bool]:
        if not tokens.update(token_id, body.projects, body.rate):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    latency = LatencyHistogram()

    @app.middleware("http")
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            latency.observe(time.perf_counter() - started)
        token_id = getattr(request.state, "token_id", None)
        if token_id:
            await run_in_threadpool(tokens.record, token_id, (time.perf_counter() - started) * 1000, response.status_code >= 500)
        return response

    @app.get("/metrics", dependencies=[Depends(admin)])
    def metrics() -> PlainTextResponse:
        """Prometheus text per token, from the shared token file (up to a minute behind). Send the admin password."""
        return PlainTextResponse(tokens.metrics("minicpm") + latency.render("minicpm"))

    @app.delete("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def revoke_token(token_id: str) -> dict[str, bool]:
        if not tokens.revoke(token_id):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    web = Path(os.environ.get("MINICPM_WEB_DIR", Path(__file__).resolve().parents[1] / "web"))
    if web.is_dir():
        app.mount("/admin", StaticFiles(directory=web, html=True))

    return app


def main() -> None:
    import uvicorn

    store = TokenStore(os.environ.get("MINICPM_TOKENS_FILE", "tokens.json"))
    config = settings.Config(store.settings_path, SETTINGS)
    client = OllamaClient(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"), config.get("model"))
    saved = settings.load(store.settings_path)
    threads = saved.get("num_thread") or os.environ.get("MINICPM_NUM_THREAD", "").strip()
    if threads:
        client.num_thread = int(threads)
    app = create_app(client=client, admin_password=os.environ.get("MINICPM_ADMIN_PASSWORD") or None,
                     tokens=store, config=config)
    uvicorn.run(app, host=os.environ.get("MINICPM_HOST", "127.0.0.1"), port=int(os.environ.get("MINICPM_PORT", "9130")))


if __name__ == "__main__":
    main()
