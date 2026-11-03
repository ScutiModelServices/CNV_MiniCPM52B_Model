# Changelog

## Unreleased

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
