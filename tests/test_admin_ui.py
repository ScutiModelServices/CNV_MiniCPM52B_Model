"""Browser smoke test for /admin/: sign in, create a token, revoke it.

Needs `pip install playwright && playwright install chromium`; skipped when Playwright is missing.
"""
import hashlib
import socket
import threading
import time

import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")

import httpx

from app import create_app
from ollama import OllamaClient

PASSWORD = "secret"


@pytest.fixture
def base_url():
    manifest = b'{"schemaVersion":2,"config":{},"layers":[]}'
    digest = hashlib.sha256(manifest).hexdigest()
    state = {"pulled": False}

    def handler(request):
        if request.url.host == "registry.ollama.ai":
            return httpx.Response(200, content=manifest)
        if request.url.path == "/api/tags":
            models = [{"name": "m:latest", "digest": digest}] if state["pulled"] else []
            return httpx.Response(200, json={"models": models})
        if request.url.path == "/api/pull":
            state["pulled"] = True
        return httpx.Response(200, json={"status": "success"})

    ollama = OllamaClient("http://ollama", "m", transport=httpx.MockTransport(handler))
    app = create_app(ollama, PASSWORD)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/admin/"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def page(base_url):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base_url)
        yield page
        browser.close()


def test_wrong_password_is_rejected(page):
    page.fill("#admin", "nope")
    page.click("#signin-button")
    sync_api.expect(page.locator("#login-error")).not_to_be_empty()
    assert page.locator("#dashboard").is_hidden()


def test_create_and_revoke_a_token(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    sync_api.expect(page.locator("#empty")).to_be_visible()
    page.fill("#name", "Garrison")
    page.click("#create-button")
    sync_api.expect(page.locator("#fresh-token")).to_contain_text("minicpm_")
    row = page.locator("#rows tr")
    sync_api.expect(row).to_contain_text("Garrison")
    sync_api.expect(row.locator('td[data-label="Requests"]')).to_have_text("0")
    row.get_by_role("button", name="Revoke").click()
    page.click("#confirm-revoke")
    sync_api.expect(page.locator("#empty")).to_be_visible()


def test_health_keeps_backend_specific_model_display(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#model-value")).to_have_text("Starting")
    sync_api.expect(page.locator("#health-text")).to_have_text("Starting")


def test_updates_card_checks_and_downloads_without_replica_restart(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#updates-card")).to_be_visible()
    sync_api.expect(page.locator("#updates-description")).to_contain_text("without downloading")
    sync_api.expect(page.locator("#update-note")).to_have_text("Not checked yet.")
    page.click("#check-updates")
    sync_api.expect(page.locator("#update-weights")).to_be_visible()
    sync_api.expect(page.locator("#update-list")).to_contain_text("m")
    page.click("#update-weights")
    sync_api.expect(page.locator("#update-note")).to_contain_text("All replicas use the new weights on their next request.")
    sync_api.expect(page.locator("#update-weights")).to_be_hidden()
    assert "Restart" not in page.locator("#update-note").inner_text()
