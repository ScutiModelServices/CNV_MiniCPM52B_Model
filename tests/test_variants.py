import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
import settings
import variants
from app import create_app
from ollama import OllamaClient
from options import SETTINGS
from tokens import TokenStore

ADMIN = {"X-Internal-Token": "admin"}
Q4 = f"{variants.REPO}:Q4_K_M"
Q8 = f"{variants.REPO}:Q8_0"
F16 = f"{variants.REPO}:F16"
GPU = {"name": "Test GPU", "vram_total_mb": 6000, "vram_free_mb": 5000, "compute_capability": "8.9"}


def hw(ram=8000, gpu=GPU):
    return {"cpu_cores": 8, "ram_total_mb": 16000, "ram_available_mb": ram, "gpu": gpu}


def rows(hardware=None, current=Q4, downloaded=frozenset()):
    return variants.listing(hardware, current, set(downloaded))


def get(rows_, quant):
    return variants.find(rows_, quant)


def test_every_quantization_is_listed_smallest_first_with_an_estimate():
    result = rows(hw())
    assert [r["id"] for r in result] == ["Q4_K_M", "Q8_0", "F16"]
    assert get(result, "Q4_K_M")["estimated_mb"] == round(1488 * 1.1 + 400)


def test_a_variant_runs_on_the_gpu_when_it_fits_there_else_on_the_cpu():
    assert get(rows(hw()), "Q8_0")["runs_on"] == "gpu"
    assert get(rows(hw()), "F16")["runs_on"] == "cpu"
    small_gpu = hw(gpu=dict(GPU, vram_free_mb=1500))
    assert get(rows(small_gpu), "Q8_0")["runs_on"] == "cpu"
    assert "runs on the CPU (slower)" in get(rows(small_gpu), "Q8_0")["detail"]


def test_a_variant_that_fits_nowhere_is_refused_with_the_numbers():
    tight = rows(hw(ram=2000, gpu=None))
    assert get(tight, "Q8_0")["fits"] is False and "2000 MB" in get(tight, "Q8_0")["reason"]
    assert get(tight, "Q4_K_M")["fits"] is True  # the model in use is loaded already
    assert get(rows(hw(ram=4000, gpu=None)), "Q8_0")["fits"] is True


def test_unreadable_memory_and_a_remote_ollama_claim_nothing():
    assert get(rows(hw(ram=None, gpu=None)), "Q8_0")["fits"] is None
    remote = get(rows(None), "F16")
    assert remote["fits"] is None and "another machine" in remote["reason"]


def test_the_model_in_use_always_fits_and_downloaded_follows_ollama():
    result = rows(hw(ram=10, gpu=None), current=F16, downloaded={Q8})
    assert get(result, "F16")["fits"] is True and get(result, "F16")["current"] is True
    assert get(result, "Q8_0")["downloaded"] is True and get(result, "Q4_K_M")["downloaded"] is False


def build(tmp_path, monkeypatch, model, url="http://127.0.0.1:11434"):
    """A service whose Ollama holds the base model and one project model; 3500 MB of memory is free, so Q4_K_M
    (about 2037 MB) fits and Q8_0 (about 3210 MB) does not."""
    monkeypatch.setattr(app_module.hardware, "probe", lambda: hw(ram=3500, gpu=None))

    def handler(request):
        names = [Q4, "minicpm-alpha:latest"]
        return httpx.Response(200, json={"models": [{"name": n, "digest": "d"} for n in names]})

    store = TokenStore(tmp_path / "t.json")
    config = settings.Config(store.settings_path, SETTINGS)
    config.set({"update_check_hours": 0, "warm": False})
    client = OllamaClient(url, model, transport=httpx.MockTransport(handler))
    return TestClient(create_app(client, "admin", tokens=store, config=config)), config


@pytest.fixture
def served(tmp_path, monkeypatch):
    return build(tmp_path, monkeypatch, F16)


def test_variants_endpoint_is_admin_only_and_describes_the_machine(served):
    client, _ = served
    assert client.get("/admin/variants").status_code == 401
    body = client.get("/admin/variants", headers=ADMIN).json()
    assert "3500 MB" in body["summary"] and "not measured" in body["estimate"]
    assert [r["id"] for r in body["variants"] if r["current"]] == ["F16"]
    assert [r["id"] for r in body["variants"] if r["downloaded"]] == ["Q4_K_M"]


def test_choosing_a_quantization_saves_the_base_model_and_leaves_project_settings(served):
    client, config = served
    prefix = config.get("project_prefix")
    reply = client.put("/admin/variant", json={"id": "Q4_K_M"}, headers=ADMIN)
    assert reply.status_code == 200 and reply.json()["restart_required"] is True
    assert config.get("model") == Q4 and config.get("project_prefix") == prefix


def test_project_models_are_not_listed_as_variants(served):
    client, _ = served
    body = client.get("/admin/variants", headers=ADMIN).json()
    assert all(r["model"].startswith(variants.REPO) for r in body["variants"])
    assert client.put("/admin/variant", json={"id": "minicpm-alpha"}, headers=ADMIN).status_code == 404


def test_choosing_one_that_does_not_fit_is_refused_and_saves_nothing(tmp_path, monkeypatch):
    client, config = build(tmp_path, monkeypatch, Q4)
    before = config.get("model")
    reply = client.put("/admin/variant", json={"id": "Q8_0"}, headers=ADMIN)
    assert reply.status_code == 409 and "3500 MB" in reply.json()["detail"]
    assert config.get("model") == before
    assert client.put("/admin/variant", json={"id": "nope"}, headers=ADMIN).status_code == 404


def test_a_remote_ollama_allows_any_choice(tmp_path, monkeypatch):
    client, config = build(tmp_path, monkeypatch, Q4, url="http://ollama.example:11434")
    assert client.put("/admin/variant", json={"id": "F16"}, headers=ADMIN).status_code == 200
    assert config.get("model") == F16
