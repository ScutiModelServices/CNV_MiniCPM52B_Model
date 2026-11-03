"""HTTP face of MiniCPM5-2B (served by Ollama).

POST /v1/chat {"messages", "tools", "schema", "thinking", "temperature", "max_tokens"} runs one model turn. With
`tools` the model may answer with `tool_calls` for the caller to run; the caller appends the results as `tool`
messages and calls again, so the loop lives in the caller. With a JSON `schema` the reply is constrained to it and
returned parsed. Thinking is on unless the caller turns it off. GET /health reports whether Ollama has the model."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
from pathlib import Path
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from ollama import OllamaClient, OllamaError
import settings
from tokens import TokenStore
from updates import Updates

_log = logging.getLogger(__name__)

DEFAULT_MODEL = "hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M"
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


def _ollama_message(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [{"function": {"name": call.name, "arguments": call.arguments}} for call in message.tool_calls]
    if message.tool_name:
        out["tool_name"] = message.tool_name
    return out


def _ollama_tool(tool: Tool) -> dict[str, Any]:
    return {"type": "function", "function": tool.model_dump()}


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
               updates: Updates | None = None, check_hours: float = 0, max_inflight: int | None = None) -> FastAPI:
    """`admin_password` (MINICPM_ADMIN_PASSWORD) unlocks /admin/, where each project gets a token of its own; when it
    is set, every call except /health must send one of those tokens in the X-Internal-Token header. Unset = no
    auth at all (local use only). `tokens` holds the project tokens (MINICPM_TOKENS_FILE)."""
    tokens = tokens or TokenStore(os.environ.get("MINICPM_TOKENS_FILE", "tokens.json"))
    client = client or OllamaClient(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                                    os.environ.get("MINICPM_MODEL", DEFAULT_MODEL))
    project_prefix = os.environ.get("MINICPM_PROJECT_PREFIX", "minicpm-")
    # Calls this replica lets wait on Ollama at once (MINICPM_MAX_INFLIGHT, default 4 x OLLAMA_NUM_PARALLEL); more get 429.
    limit = max_inflight if max_inflight is not None else int(
        os.environ.get("MINICPM_MAX_INFLIGHT") or 4 * int(os.environ.get("OLLAMA_NUM_PARALLEL") or 2))
    inflight = 0

    updates = updates or Updates(client.model, cached_sha=lambda: client.cached_sha(), remote_sha=lambda: client.remote_sha(),
                                 download=lambda: client.pull(), unload=lambda: client.unload())

    async def check_forever() -> None:
        while True:
            try:
                status = await updates.check()
                if status["error"]:
                    _log.warning("Update check failed: %s", status["error"])
            except Exception:
                _log.exception("Update check failed")
            await asyncio.sleep(check_hours * 3600)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background = None
        try:
            if warm:
                try:
                    await client.warm()
                except Exception:
                    _log.exception("Ollama warm-up failed; continuing startup")
            if check_hours > 0:
                background = asyncio.create_task(check_forever())
            yield
        finally:
            if background is not None:
                background.cancel()
                try:
                    await background
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
        projects = {name.removesuffix(":latest") for name in names
                    if name.startswith(project_prefix)
                    and re.fullmatch(PROJECT_PATTERN, name.removesuffix(":latest")[len(project_prefix):])}
        return {"status": "ok", "model": client.model,
                "ready": client.model in names or f"{client.model}:latest" in names,
                "num_thread": client.num_thread, "project_models": sorted(projects)}

    @app.post("/v1/chat")
    async def chat(body: ChatRequest, allowed: list[str] | None = Depends(authorize), _slot: None = Depends(slot)) -> dict[str, Any]:
        check_scope(allowed, body.project)
        if body.tools and body.json_schema is not None:
            raise HTTPException(422, "Send either tools or a schema, not both")
        started = time.perf_counter()
        try:
            model = client.model
            fallback = False
            if body.project is not None:
                candidate = project_prefix + body.project
                if await installed(candidate):
                    model = candidate
                else:
                    fallback = True
            reply = await client.chat([_ollama_message(m) for m in body.messages],
                                      tools=[_ollama_tool(t) for t in body.tools] if body.tools else None,
                                      schema=body.json_schema, thinking=body.thinking,
                                      temperature=body.temperature, max_tokens=body.max_tokens, model=model)
        except OllamaError as error:
            if error.status == 400:
                raise HTTPException(422, str(error)) from None
            if error.status == 503:  # Ollama's own queue is full
                raise HTTPException(429, str(error), headers={"Retry-After": "5"}) from None
            raise HTTPException(504 if error.status == 504 else 502, str(error)) from None
        except Exception:
            _log.exception("MiniCPM chat failed")
            raise HTTPException(500, "MiniCPM could not answer") from None
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
        if body.json_schema is not None:
            try:
                result["parsed"] = json.loads(content)
            except ValueError:
                # Cut off by max_tokens or malformed; the caller can still see `content`.
                raise HTTPException(502, {"message": "The model did not return valid JSON", "content": content}) from None
        return result

    @app.put("/admin/threads", dependencies=[Depends(admin)])
    def set_threads(body: Threads) -> dict[str, Any]:
        """Applies to this replica now and is saved; other replicas pick it up when they restart. It beats MINICPM_NUM_THREAD."""
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

    @app.put("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def update_token(token_id: str, body: TokenScope) -> dict[str, bool]:
        if not tokens.update(token_id, body.projects, body.rate):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    @app.middleware("http")
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        token_id = getattr(request.state, "token_id", None)
        if token_id:
            await run_in_threadpool(tokens.record, token_id, (time.perf_counter() - started) * 1000, response.status_code >= 500)
        return response

    @app.get("/metrics", dependencies=[Depends(admin)])
    def metrics() -> PlainTextResponse:
        """Prometheus text per token, from the shared token file (up to a minute behind). Send the admin password."""
        return PlainTextResponse(tokens.metrics("minicpm"))

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

    client = OllamaClient(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                          os.environ.get("MINICPM_MODEL", DEFAULT_MODEL))
    saved = settings.load(TokenStore(os.environ.get("MINICPM_TOKENS_FILE", "tokens.json")).settings_path)
    threads = saved.get("num_thread") or os.environ.get("MINICPM_NUM_THREAD", "").strip()
    if threads:
        client.num_thread = int(threads)
    app = create_app(client=client, admin_password=os.environ.get("MINICPM_ADMIN_PASSWORD") or None,
                     warm=os.environ.get("MINICPM_WARM", "1") == "1",
                     check_hours=float(os.environ.get("MINICPM_UPDATE_CHECK_HOURS", "6") or 0))
    uvicorn.run(app, host=os.environ.get("MINICPM_HOST", "127.0.0.1"), port=int(os.environ.get("MINICPM_PORT", "9130")))


if __name__ == "__main__":
    main()
