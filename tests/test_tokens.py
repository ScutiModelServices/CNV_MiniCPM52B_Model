import json
import time
from concurrent.futures import ThreadPoolExecutor

from tokens import TokenStore


def test_new_token_has_null_last_used(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    made = store.create("project")
    assert made["last_used"] is None
    assert store.list() == [{"id": made["id"], "name": "project", "created": made["created"], "last_used": None, "requests": 0}]


def test_authenticated_use_is_persisted_and_writes_are_throttled(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    store = TokenStore(path)
    first = store.create("first")["token"]
    second = store.create("second")["token"]
    monkeypatch.setattr("tokens.time.time", lambda: 1000)
    assert not store.valid("wrong")
    assert all(row["last_used"] is None for row in store.list())
    assert store.valid(first)
    assert json.loads(path.read_text())[0]["last_used"] == 1000
    # A different replica sees the persisted timestamp and also skips writing.
    replica = TokenStore(path)
    monkeypatch.setattr("tokens.time.time", lambda: 1059)
    before = path.stat().st_mtime_ns
    assert replica.valid(first)
    assert path.stat().st_mtime_ns == before
    assert store.valid(second)
    assert json.loads(path.read_text())[1]["last_used"] == 1059
    monkeypatch.setattr("tokens.time.time", lambda: 1060)
    assert store.valid(first)
    assert json.loads(path.read_text())[0]["last_used"] == 1060


def test_old_token_file_loads_and_tracks_next_use(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    store = TokenStore(path)
    made = store.create("legacy")
    rows = json.loads(path.read_text())
    rows[0].pop("last_used", None)
    path.write_text(json.dumps(rows))
    reloaded = TokenStore(path)
    assert reloaded.list()[0]["last_used"] is None
    monkeypatch.setattr("tokens.time.time", lambda: 2000)
    assert reloaded.valid(made["token"])
    assert reloaded.list()[0]["last_used"] == 2000


def test_simultaneous_authentication_writes_once(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    token = TokenStore(path).create("busy")["token"]
    writes = []
    save = TokenStore._save

    def slow_save(self, rows):
        writes.append(1)
        time.sleep(0.03)
        save(self, rows)

    monkeypatch.setattr(TokenStore, "_save", slow_save)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(lambda _: TokenStore(path).valid(token), range(4)))
    assert len(writes) == 1


def test_requests_are_counted_and_flushed_with_the_throttled_write(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    token = store.create("counted")["token"]
    for now in (1000, 1010, 1020, 1060):
        monkeypatch.setattr("tokens.time.time", lambda now=now: now)
        assert store.valid(token)
        assert store.list()[0]["requests"] == {1000: 1, 1010: 1, 1020: 1, 1060: 4}[now]


def test_project_limit_is_stored_listed_and_returned_by_lookup(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    scoped = store.create("a", ["b", "a", "b"])
    unscoped = store.create("open")
    assert scoped["projects"] == ["a", "b"] and "projects" not in unscoped and "hash" not in scoped
    assert store.lookup(scoped["token"])["projects"] == ["a", "b"]
    assert "projects" not in store.lookup(unscoped["token"])
    assert store.lookup("nope") is None
    assert [row.get("projects") for row in store.list()] == [["a", "b"], None]


def test_expired_token_is_invalid_but_still_listed(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    monkeypatch.setattr("tokens.time.time", lambda: 1000)
    made = store.create("short", expires_days=1)
    assert made["expires"] == 1000 + 86400 and store.valid(made["token"])
    monkeypatch.setattr("tokens.time.time", lambda: 1000 + 86400)
    assert not store.valid(made["token"])
    assert store.list()[0]["expires"] == 1000 + 86400


def test_update_replaces_projects_and_rate(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    made = store.create("p", ["a"], rate=5)
    assert store.update(made["id"], ["c", "b"], None)
    row = store.list()[0]
    assert row["projects"] == ["b", "c"] and "rate" not in row
    assert store.update(made["id"], None, 7)
    row = store.list()[0]
    assert "projects" not in row and row["rate"] == 7
    assert not store.update("nope", None, None)


def test_throttle_allows_the_rate_per_minute(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    clock = [100.0]
    monkeypatch.setattr("tokens.time.monotonic", lambda: clock[0])
    row = {"id": "x", "rate": 2}
    assert store.throttle(row) is None and store.throttle(row) is None
    clock[0] = 130.0
    assert store.throttle(row) == 31
    clock[0] = 161.0
    assert store.throttle(row) is None
    assert store.throttle({"id": "y"}) is None  # no rate = unlimited


def test_record_is_written_once_a_minute_and_exported(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    made = store.create('proj "x"')
    clock = [1000]
    monkeypatch.setattr("tokens.time.time", lambda: clock[0])
    store.record(made["id"], 100, False)  # the first call is written at once
    assert store.list()[0]["timed"] == 1
    clock[0] = 1030
    store.record(made["id"], 300, True)
    assert store.list()[0]["timed"] == 1  # held back until a minute passed
    clock[0] = 1061
    store.record(made["id"], 200, False)
    row = store.list()[0]
    assert (row["timed"], row["errors"], row["latency_ms"]) == (3, 1, 600)
    text = store.metrics("svc")
    assert 'svc_errors_total{token="proj \\"x\\""} 1' in text and "svc_latency_ms_sum" in text
