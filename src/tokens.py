"""Per-project API tokens stored as hashes, shared by replicas through one JSON file.

Mutations lock a companion file before reading, then atomically replace the JSON.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

PREFIX = "minicpm_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self.settings_path = self._path.with_name("settings.json")
        self._pending: dict[str, int] = {}  # uses not yet written, per token id
        self._hits: dict[str, deque] = {}  # recent call times per token id, for the rate limit
        self._stats: dict[str, list] = {}  # per token id: [timed, errors, ms] not yet written
        self._flushed = 0.0
        self._guard = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            return json.loads(self._path.read_text("utf-8"))
        except FileNotFoundError:
            return []

    @contextmanager
    def _edit(self):
        """Serialize read-modify-write across threads and service replicas."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.with_suffix(self._path.suffix + ".lock").open("a+b") as lock:
            if os.name == "nt":
                import msvcrt

                if lock.tell() == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield self._load()
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self, rows: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(rows, indent=2), "utf-8")
        os.replace(temp, self._path)

    def list(self) -> list[dict]:
        return [{**{k: v for k, v in row.items() if k != "hash"},
                 "last_used": row.get("last_used"), "requests": row.get("requests", 0)}
                for row in self._load()]

    def create(self, name: str, projects: list[str] | None = None, rate: int | None = None,
               expires_days: int | None = None) -> dict:
        """The plaintext token is returned once and never stored. `projects` limits which project models it may use;
        None (or empty) = all."""
        token = PREFIX + secrets.token_urlsafe(32)
        row = {"id": secrets.token_hex(4), "name": name, "created": int(time.time()),
               "last_used": None, "requests": 0, "hash": _hash(token)}
        if projects:
            row["projects"] = sorted(set(projects))
        if rate:
            row["rate"] = rate  # calls per minute
        if expires_days:
            row["expires"] = row["created"] + expires_days * 86400
        with self._edit() as rows:
            self._save([*rows, row])
        return {**{k: v for k, v in row.items() if k != "hash"}, "token": token}

    def update(self, token_id: str, projects: list[str] | None, rate: int | None) -> bool:
        """Replaces the token's project limit and rate limit (None or empty = no limit)."""
        with self._edit() as rows:
            for row in rows:
                if row["id"] == token_id:
                    for key, value in (("projects", sorted(set(projects or []))), ("rate", rate)):
                        if value:
                            row[key] = value
                        else:
                            row.pop(key, None)
                    self._save(rows)
                    return True
        return False

    def throttle(self, row: dict) -> int | None:
        """Seconds to wait when the token is over its calls per minute, else None. Counted per replica,
        so with several replicas behind the balancer the real limit can be up to that many times higher."""
        rate = row.get("rate")
        if not rate:
            return None
        now = time.monotonic()
        with self._guard:
            hits = self._hits.setdefault(row["id"], deque())
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= rate:
                return max(1, int(60 - (now - hits[0])) + 1)
            hits.append(now)
        return None

    def record(self, token_id: str, ms: float, failed: bool) -> None:
        """Counts one finished call; written with the other counters at most once a minute (a crash loses that minute)."""
        with self._guard:
            seen = self._stats.setdefault(token_id, [0, 0, 0.0])
            seen[0] += 1
            seen[1] += int(failed)
            seen[2] += ms
            now = time.time()
            if now - self._flushed < 60:
                return
            self._flushed = now
            pending, self._stats = self._stats, {}
        with self._edit() as rows:
            for row in rows:
                if row["id"] in pending:
                    timed, errors, total = pending[row["id"]]
                    row["timed"] = row.get("timed", 0) + timed
                    row["errors"] = row.get("errors", 0) + errors
                    row["latency_ms"] = round(row.get("latency_ms", 0) + total)
            self._save(rows)

    def metrics(self, prefix: str) -> str:
        """Prometheus text, one series per token; only as fresh as the last write to the token file."""
        def label(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

        rows = self.list()
        lines: list[str] = []
        for suffix, key in (("requests_total", "requests"), ("errors_total", "errors"),
                            ("latency_ms_sum", "latency_ms"), ("latency_ms_count", "timed")):
            lines.append(f"# TYPE {prefix}_{suffix} counter")
            lines += [f'{prefix}_{suffix}{{token="{label(row["name"])}"}} {row.get(key, 0)}' for row in rows]
        return "\n".join(lines) + "\n"

    def revoke(self, token_id: str) -> bool:
        with self._edit() as rows:
            kept = [row for row in rows if row["id"] != token_id]
            if len(kept) != len(rows):
                self._save(kept)
            return len(kept) != len(rows)

    def valid(self, token: str) -> bool:
        return self.lookup(token) is not None

    def lookup(self, token: str) -> dict | None:
        """The token's row (`projects` present when it is limited), or None when it is not a live token."""
        digest = _hash(token)
        with self._edit() as rows:
            for row in rows:
                if secrets.compare_digest(digest, row["hash"]):
                    now = int(time.time())
                    if row.get("expires") and row["expires"] <= now:
                        return None
                    # shortcut: uses between writes are counted in memory, so a crash loses up to a minute of them.
                    self._pending[row["id"]] = self._pending.get(row["id"], 0) + 1
                    # The persisted value shares the throttle across replicas/restarts.
                    if row.get("last_used") is None or now - row["last_used"] >= 60:
                        row["last_used"] = now
                        row["requests"] = row.get("requests", 0) + self._pending.pop(row["id"])
                        self._save(rows)
                    return row
        return None
