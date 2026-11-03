"""Async client for Ollama's /api/chat, narrowed to what the service needs."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

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
        self.num_thread: int | None = None  # CPU threads Ollama uses per request; None = Ollama decides
        self._keep_alive = keep_alive
        self._timeout = timeout
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
        result = await self._post("/api/pull", {"model": self.model, "stream": False}, timeout=1800.0)
        if result.get("status") != "success":
            raise OllamaError("Ollama did not finish pulling the model")

    async def unload(self) -> None:
        """Unload the resident model so subsequent requests use the newly pulled weights."""
        await self._post("/api/chat", {"model": self.model, "messages": [], "stream": False, "keep_alive": 0})

    async def model_ready(self) -> bool:
        """True when Ollama answers and has the model pulled."""
        try:
            response = await self._http.get("/api/tags", timeout=3.0)
            names = {entry.get("name") for entry in response.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return False
        return self.model in names or f"{self.model}:latest" in names

    async def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None,
                   schema: dict[str, Any] | None, thinking: bool, temperature: float, max_tokens: int) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False, "think": thinking,
                                "keep_alive": self._keep_alive,
                                "options": {"temperature": temperature, "num_predict": max_tokens}}
        if self.num_thread:
            body["options"]["num_thread"] = self.num_thread
        if tools:
            body["tools"] = tools
        if schema is not None:
            body["format"] = schema
        return await self._post("/api/chat", body, timeout=self._timeout)

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
