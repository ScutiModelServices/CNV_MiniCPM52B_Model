"""Async client for Ollama's /api/chat, narrowed to what the service needs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, AsyncIterator
from urllib.parse import quote, urlparse

import httpx


class OllamaError(RuntimeError):
    """Ollama is down, refused the request, or sent something unusable. `status` is the HTTP status it answered with."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float = 120.0, keep_alive: str = "30m",
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.model = model
        self.same_machine = urlparse(url).hostname in ("127.0.0.1", "localhost", "::1")  # Ollama runs where this service sees the memory
        self.num_thread: int | None = None  # CPU threads Ollama uses per request; None = Ollama decides
        self._keep_alive = keep_alive
        self._timeout = timeout
        self._tags_lock = asyncio.Lock()
        self._tags: frozenset[str] = frozenset()
        self._tags_at: float | None = None
        self._digests: dict[str, str] = {}
        self.generation = 0  # part of the answer cache key; changes when the model is unloaded for new weights
        self._http = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._http.aclose()

    async def warm(self) -> None:
        """Load the configured model without generating a turn; keep it resident for later requests."""
        await self.chat([], tools=None, schema=None, thinking=False, temperature=0.0, max_tokens=1)

    async def cached_sha(self) -> str | None:
        """Read the local manifest digest from Ollama's tags; missing model is distinct from unreachable Ollama."""
        response = await self._http.get("/api/tags", timeout=3.0)
        response.raise_for_status()
        for entry in response.json().get("models", []):
            if entry.get("name") in (self.model, f"{self.model}:latest"):
                digest = entry.get("digest")
                if not isinstance(digest, str) or not digest:
                    raise OllamaError("Ollama tags omitted the model digest")
                return digest.removeprefix("sha256:").lower()
        return None

    async def remote_sha(self) -> str:
        """Hash remote manifest bytes, matching Ollama's local manifest digest, without fetching blobs."""
        name = self.model
        host = "registry.ollama.ai"
        if name.startswith(("hf.co/", "huggingface.co/")):
            host, name = name.split("/", 1)
        name, separator, tag = name.partition(":")
        if not separator:
            tag = "latest"
        if host == "registry.ollama.ai" and "/" not in name:
            name = "library/" + name
        url = f"https://{host}/v2/{quote(name, safe='/')}/manifests/{quote(tag, safe='')}"
        response = await self._http.get(url, timeout=15.0, follow_redirects=True,
                                        headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
        response.raise_for_status()
        manifest = response.json()
        if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2
                or not isinstance(manifest.get("config"), dict) or not isinstance(manifest.get("layers"), list)):
            raise OllamaError("Registry returned an invalid model manifest")
        return hashlib.sha256(response.content).hexdigest()

    async def pull(self) -> None:
        """Ollama reuses unchanged blobs; allow a long download without buffering progress events."""
        from progress import PullTracker

        tracker = PullTracker(self.model)
        try:
            async with self._http.stream("POST", "/api/pull", json={"model": self.model, "stream": True}, timeout=1800.0) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    tracker.feed(line)
        except (httpx.HTTPError, RuntimeError) as error:
            raise OllamaError(f"Ollama pull failed: {error}") from None
        finally:
            tracker.close()
        if tracker.status != "success":
            raise OllamaError("Ollama did not finish pulling the model")

    async def unload(self) -> None:
        """Unload the resident model so subsequent requests use the newly pulled weights."""
        self.generation += 1
        await self._post("/api/chat", {"model": self.model, "messages": [], "stream": False, "keep_alive": 0})

    def digest(self, name: str) -> str | None:
        """The digest Ollama listed for `name` at the last inventory lookup, or None."""
        return self._digests.get(name) or self._digests.get(f"{name}:latest") or None

    async def downloaded(self) -> set[str]:
        """Ollama model names on disk; empty when Ollama cannot be reached."""
        try:
            return set(await self.installed_models(0))
        except OllamaError:
            return set()

    async def model_ready(self) -> bool:
        """True when Ollama answers and has the model pulled."""
        try:
            names = await self.installed_models()
        except OllamaError:
            return False
        return self.model in names or f"{self.model}:latest" in names

    async def installed_models(self, max_age: float = 30.0) -> frozenset[str]:
        """Inventory no older than `max_age` seconds (0 = always ask Ollama); concurrent callers share a refresh.
        Only successful lookups are cached."""
        async with self._tags_lock:
            if self._tags_at is not None and time.monotonic() - self._tags_at < max_age:
                return self._tags
            try:
                response = await self._http.get("/api/tags", timeout=3.0)
            except httpx.TimeoutException as error:
                raise OllamaError("Ollama timed out", 504) from error
            except httpx.HTTPError as error:
                raise OllamaError(f"Ollama could not be reached: {error}") from error
            except ValueError as error:
                raise OllamaError("Ollama returned invalid model inventory") from error
            if not response.is_success:
                raise OllamaError(f"Ollama refused inventory lookup ({response.status_code})", response.status_code)
            try:
                payload = response.json()
                entries = payload["models"]
                if not isinstance(entries, list) or any(
                        not isinstance(entry, dict) or not isinstance(entry.get("name"), str) for entry in entries):
                    raise ValueError("Invalid models")
                names = frozenset(entry["name"] for entry in entries)
            except (ValueError, KeyError, TypeError) as error:
                raise OllamaError("Ollama returned invalid model inventory") from error
            self._tags = names
            self._digests = {entry["name"]: str(entry.get("digest") or "") for entry in entries}
            self._tags_at = time.monotonic()
            return names

    def _chat_body(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, schema: dict[str, Any] | None,
                   thinking: bool, temperature: float, max_tokens: int, model: str | None, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model if model is not None else self.model, "messages": messages, "stream": stream, "think": thinking,
                                "keep_alive": self._keep_alive,
                                "options": {"temperature": temperature, "num_predict": max_tokens}}
        if self.num_thread:
            body["options"]["num_thread"] = self.num_thread
        if tools:
            body["tools"] = tools
        if schema is not None:
            body["format"] = schema
        return body

    async def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
                   schema: dict[str, Any] | None, thinking: bool, temperature: float, max_tokens: int,
                   model: str | None = None) -> dict[str, Any]:
        body = self._chat_body(messages, tools, schema, thinking, temperature, max_tokens, model, False)
        return await self._post("/api/chat", body, timeout=self._timeout)

    async def chat_stream(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
                          schema: dict[str, Any] | None, thinking: bool, temperature: float, max_tokens: int,
                          model: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Ollama's chunks as they arrive: `message` holds a piece of `content`, `thinking` or `tool_calls`; the last
        chunk has `done` and the usage. The timeout applies to each wait for a chunk, not to the whole answer."""
        body = self._chat_body(messages, tools, schema, thinking, temperature, max_tokens, model, True)
        try:
            async with self._http.stream("POST", "/api/chat", json=body, timeout=self._timeout) as response:
                if not response.is_success:
                    raw = await response.aread()
                    try:
                        detail = json.loads(raw).get("error")
                    except ValueError:
                        detail = None
                    raise OllamaError(f"Ollama refused the request ({response.status_code}): {detail or raw[:200].decode('utf-8', 'replace')}",
                                      response.status_code)
                async for text in response.aiter_lines():
                    if not text.strip():
                        continue
                    chunk = json.loads(text)
                    if chunk.get("error"):
                        raise OllamaError(f"Ollama refused the request: {chunk['error']}")
                    yield chunk
        except httpx.TimeoutException as error:
            raise OllamaError("Ollama timed out", 504) from error
        except httpx.HTTPError as error:
            raise OllamaError(f"Ollama could not be reached: {error}") from error
        except ValueError as error:
            raise OllamaError("Ollama sent an unreadable answer") from error

    async def _post(self, path: str, body: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        """Apply the same timeout and error mapping to chat and model lifecycle calls."""
        try:
            response = await self._http.post(path, json=body, timeout=timeout)
        except httpx.TimeoutException as error:
            raise OllamaError("Ollama timed out", 504) from error
        except httpx.HTTPError as error:
            raise OllamaError(f"Ollama could not be reached: {error}") from error
        if not response.is_success:
            try:
                detail = response.json().get("error")
            except ValueError:
                detail = None
            raise OllamaError(f"Ollama refused the request ({response.status_code}): {detail or response.text[:200]}",
                              response.status_code)
        result = response.json()
        if result.get("error"):
            raise OllamaError(f"Ollama refused the request: {result['error']}")
        return result
