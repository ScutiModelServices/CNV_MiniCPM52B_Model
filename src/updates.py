"""Update state and sequencing for one shared Ollama model; checks never download weights."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import time
from typing import Any, Awaitable, Callable


class Updates:
    """Inject async digest, download and unload operations; serialize checks and updates per replica."""

    def __init__(self, model: str, *, cached_sha: Callable[[], Awaitable[str | None]],
                 remote_sha: Callable[[], Awaitable[str]], download: Callable[[], Awaitable[None]],
                 unload: Callable[[], Awaitable[None]]) -> None:
        self._model = model
        self._cached_sha, self._remote_sha = cached_sha, remote_sha
        self._download, self._unload = download, unload
        self._lock = asyncio.Lock()
        self._unload_pending = False
        self._status: dict[str, Any] = {"checked_at": None, "error": None, "package": None, "weights": []}

    def status(self) -> dict[str, Any]:
        """Return the last result without I/O or exposing mutable internal state."""
        return {**deepcopy(self._status),
                "description": "Looks for newer model weights on the remote registry without downloading.",
                "reload_note": "Weights downloaded; Ollama unloaded the old copy. All replicas use the new weights on their next request."}

    async def check(self) -> dict[str, Any]:
        async with self._lock:
            return await self._check()

    async def _check(self) -> dict[str, Any]:
        status: dict[str, Any] = {"checked_at": time.time(), "error": None, "package": None, "weights": []}
        try:
            cached, latest = await self._cached_sha(), await self._remote_sha()
            status["weights"] = [{"repo": self._model, "cached": cached, "latest": latest,
                                  "update": cached != latest or self._unload_pending}]
        except Exception as error:
            status["error"] = f"{type(error).__name__}: {error}"
        self._status = status
        return self.status()

    async def update_weights(self) -> dict[str, Any]:
        """Pull changed weights, then unload the old resident copy for every API replica's next request."""
        async with self._lock:
            try:
                status = await self._check()
                if status["error"]:
                    raise RuntimeError(status["error"])
                if any(item["cached"] != item["latest"] for item in status["weights"]):
                    # The pull can commit even if its response is lost; keep unload pending on failure.
                    self._unload_pending = True
                    await self._download()
                # Always unload on this explicit admin action. A retry may reach a different replica
                # after a successful pull or failed unload; equal disk digests do not prove residency.
                self._unload_pending = True
                await self._unload()
                self._unload_pending = False
                return await self._check()
            except Exception as error:
                self._status = {**self._status, "error": f"{type(error).__name__}: {error}"}
                raise
