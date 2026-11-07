# Changelog

## Unreleased

- A local run now loads `.env` from the service folder at start (python-dotenv, same as the tool services); a real environment variable still wins, and Docker is unchanged because `.env` is not in the image.
- Untracked folders now start with `.` or `_`: `zip_versions/` is `_zip_versions/`. A local `voices`, `tem_code` or `zip_versions` folder from an older checkout needs renaming by hand.
- `run.bat` is now a menu (run the service, deploy, build the release ZIP) and `run.bat run|deploy|zip` skips it. `tools/deploy.bat`, `tools/make_zip.bat` and `tools/deploy.local.example.bat` are gone; the local deploy target is `tools/deploy.local.ini` (copy `tools/deploy.local.example.ini`).
- Admin page: **Connect to this service** card with the addresses other services can use (this page's address, same PC, Docker network, `host.docker.internal`) and every endpoint, each with a Copy button.
- Compose uses the Ollama already running on the host by default (`MINICPM_OLLAMA_URL`, `http://host.docker.internal:11434`), shared with other services. The bundled `ollama` and `minicpm_pull` services are now behind the `bundled-ollama` profile (`COMPOSE_PROFILES=bundled-ollama`, `MINICPM_OLLAMA_URL=http://ollama:11434`).
- Variable names now match LFM3Model and SparkX26Model: `MINICPM_OLLAMA_URL` (was `OLLAMA_URL`) and `MINICPM_OLLAMA_MODEL` (was `MINICPM_MODEL`). The old names still work. Ollama's own variables (`OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_MEM_LIMIT`) keep their names.

- Admin **Try it** panel: descriptor-driven public API requests, live text streams, JSON/media output, status and latency, cancellation and redacted curl. Auth choice (b): pasted public token stays in memory; normal scope, limits and caches apply. Shared `web/playground.js` is copied across all model and tool services.

- `run.bat`: runs `uv sync` directly; `python -m uv` failed when uv was installed only as a program (winget).
- Docker: `.dockerignore` keeps `.git`, `.venv`, tests and local state out of the build context (the same file in every model service).
- Compose: `stop_grace_period: 90s`, so a restart lets a running answer finish instead of cutting it off after Docker's default 10 s.
- Docs: set CPU threads to the number of physical cores, not logical CPUs.
- `/metrics` also returns `minicpm_request_seconds`, a histogram of how long `/v1` calls took on that replica.
- Compose: optional `OLLAMA_MEM_LIMIT` caps the Ollama container's RAM and `OLLAMA_KEEP_ALIVE` sets how long an idle model keeps its RAM (default no cap, 5m).

- Answer cache (`MINICPM_CACHE_SIZE`, default 256, 0 = off; `X-Cache` header): the last answers to identical `temperature` 0 requests are sent again without asking Ollama.
- `POST /v1/chat` accepts `"stream": true`: the answer arrives as JSON lines (`content`, `thinking`, then `done`).

- **Settings** card on `/admin/`: the update interval, cache and limits, project options and similar are saved in `settings.json`, beat the environment variable and apply on every replica within seconds (`GET` and `PUT /admin/settings`). The compose file no longer passes these; the admin password, ports, replica count and Docker settings stay in `.env`.

- Tokens: edit the project list and a calls-per-minute limit without revoking (`PUT /admin/tokens/{id}`), optional expiry, per-token errors and average time on the admin page, and `GET /metrics` (Prometheus text). Over the rate limit a call gets `429` with `Retry-After`.
- Admin page: **Project models** card.
- `MINICPM_MAX_INFLIGHT` (default 4 x `OLLAMA_NUM_PARALLEL`) caps calls waiting on Ollama per replica; more get `429`, as does an Ollama `503`.

- Tokens can be limited to a list of projects (set on the admin page when creating one). Another project is `403`; no `project` is always allowed.

## 1.1.0

- Optional validated `project` on `/v1/chat` selects an installed `MINICPM_PROJECT_PREFIX` model (default `minicpm-`), otherwise falls back to the configured base model.
- Chat reports the selected `model` and `fallback`; `/health` adds installed `project_models` while readiness still checks the base model.
- Cache successful Ollama inventory lookups for 30 seconds with concurrent refresh protection. A project missing from the
  cache re-asks Ollama (at most every 2 seconds); `/health` always asks. A failed lookup serves the base with `fallback: true`.
- Document GGUF registration, CPU model reloads and `OLLAMA_MAX_LOADED_MODELS`; Compose forwards the prefix and defaults to one loaded model.

- Load saved CPU thread settings in `main()`, matching LayaModel and preserving injected clients in tests.
- Shared admin health rendering uses backend readiness and model fields while retaining each service's display.
- Deployment reference covers the shared Ollama stack, settings, scaling, updates and releasing.
- `MINICPM_WARM` (default `1`) loads the model with an empty chat at startup; connection failures are logged.
- Updates card and admin routes check remote manifest digests without downloading, then pull changed weights
  and unload the old Ollama copy on demand. All replicas use the new weights on their next request.
- `MINICPM_UPDATE_CHECK_HOURS` (default `6`, `0` disables the timer) controls background checks; manual checks remain available.

## 1.0.0

First public release.

- Admin page at `/admin/`: health badge with polling, token list with created, last used and request count,
  copy button, in-app revoke dialog, CPU thread control, light and dark themes, phone layout.
- Per-project tokens stored as hashes; `last_used` and `requests` are written at most once a minute per token,
  so the counters are approximate and a crash can lose up to a minute of them.
- Docker Compose deployment behind nginx with configurable replicas; guides for Docker on the same device,
  Docker on a deployment device, and no Docker.
- Browser smoke test (Playwright) and a GitHub Actions workflow that runs the tests.
