"""Per-project API tokens stored as hashes, shared by replicas through one JSON file.

Mutations lock a companion file before reading, then atomically replace the JSON.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import secrets
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

    def create(self, name: str, projects: list[str] | None = None) -> dict:
        """The plaintext token is returned once and never stored. `projects` limits which project models it may use;
        None (or empty) = all."""
        token = PREFIX + secrets.token_urlsafe(32)
        row = {"id": secrets.token_hex(4), "name": name, "created": int(time.time()),
               "last_used": None, "requests": 0, "hash": _hash(token)}
        if projects:
            row["projects"] = sorted(set(projects))
        with self._edit() as rows:
            self._save([*rows, row])
        return {**{k: v for k, v in row.items() if k != "hash"}, "token": token}

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
                    # shortcut: uses between writes are counted in memory, so a crash loses up to a minute of them.
                    self._pending[row["id"]] = self._pending.get(row["id"], 0) + 1
                    # The persisted value shares the throttle across replicas/restarts.
                    if row.get("last_used") is None or now - row["last_used"] >= 60:
                        row["last_used"] = now
                        row["requests"] = row.get("requests", 0) + self._pending.pop(row["id"])
                        self._save(rows)
                    return row
        return None
