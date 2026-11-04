# MiniCPM52BModel

MiniCPM5-2B served over HTTP in front of Ollama, behind the shared model-service admin page, tokens and settings. Port 9130, env prefix `MINICPM_`, token prefix `minicpm_`.

## Agent instructions
This file is the one instruction file for every coding agent; `CLAUDE.md` only imports it (`@AGENTS.md`). Edit this file, never `CLAUDE.md`.

## Rules
- Follow the `model-service-parity` skill and `AI/ModelServices/SHARED-FEATURES.md` (what must match in every model service) and `AI/ModelServices/SERVICE-SPECIFIC.md` (what differs on purpose). A change to a shared feature goes to every service; a new difference goes into `SERVICE-SPECIFIC.md` in the same change.
- `web/index.html`, `src/tokens.py` and `src/settings.py`, `src/updates.py` are copies shared with the other model services. Change one, copy it to the others, run `python tools/check_sync.py` in each repo.
- The model runs in Ollama, not in this process: `src/ollama.py` is the client (its tests are `tests/test_engine.py`). Ollama has its own container and volume.
- The only service with `tools`, `schema` and `thinking` on `POST /v1/chat`. Project models are Ollama models named with the `project_prefix` setting.
- Behaviour changes update `README.md`, `deployment.md`, `docs/guide-web-ui.md`, `.env.example` and `CHANGELOG.md` in the same commit.
- Tests: `uv run pytest -q`. `tests/test_admin_ui.py` needs Playwright and its browser and is skipped without it. On Windows, if uv fails with "cannot move the file to a different disk drive", set `UV_CACHE_DIR` to a folder on the same drive and `UV_LINK_MODE=copy`.
- Never read or print `.env`, `tokens.json` or `settings.json`.
- Commit with the noreply author email `214214011+scutiii2@users.noreply.github.com` (another address is rejected on push). Push `main`, then run `python tools/publish_live.py`. Build Docker only when the Dockerfile or the dependencies changed.
