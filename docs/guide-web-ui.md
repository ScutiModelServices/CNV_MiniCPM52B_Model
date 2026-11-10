# CNV_MiniCPM52B_Model: the admin web UI

The admin UI at `/admin/` has separate sidebar pages for health, access and model configuration. It works with every deployment mode. Only the address differs:

| Run | Address |
|---|---|
| Docker or `run.bat` on this PC | `http://127.0.0.1:29200/admin/` |
| Docker on the deployment box | `http://<box-ip>:29200/admin/` |

If `SERVICE_PORT` is changed, use that port.

## Before you open it

The page needs the admin password: the value of `SERVICE_ADMIN_PASSWORD`.
- Docker Desktop on this device: set it in `.env.compose` next to `docker-compose.yml`, then `docker compose --env-file .env.compose up -d`.
- Deployment device: set it in `.env` next to `docker-compose.yml` (Deploy option 2 copies it), then `docker compose up -d`.
- No Docker: set it in `.env.local` (or `.env`) in this folder and start with `run.bat` (it passes `--dotenv`), or set it in the environment first; a real environment variable wins over both files and `.env.local` wins over `.env`. A plain `python src/app.py` reads neither.
- Empty password = no auth at all and the page answers `Admin password required (set SERVICE_ADMIN_PASSWORD)`. Set
  one to use the page.

## Sign in and service status

Type the admin password and press **Sign in**. A wrong password shows an inline `Wrong admin password` error.
The password stays in `sessionStorage` for this browser tab, so reloading restores the session. **Sign out** clears
the password and any freshly created token. If browser storage is blocked, sign in again after reloading.

The header shows the service address and a health badge with an icon and text: **Healthy**, **Starting**, or
**Unreachable**. Health is checked without authentication every 10 seconds while signed in. **Starting** means Ollama
has not made the model ready yet. Tiles show model readiness, replicas, Ollama parallel requests, CPU threads, and active tokens.
Use **Theme** to cycle between system, light, and dark appearance; this preference is saved in the browser.

## Tokens

Each calling project gets its own token, so one can be revoked without touching the others.
- **Create:** type a project name (up to 64 characters) and press **Create token**. The token appears once in
  an accent-tinted box with a **Copy** button and the reminder “Shown once. Copy it before you leave.”
  The box is cleared on the next create attempt or sign-out. Clipboard access has a fallback for plain HTTP;
  if both methods are blocked, select the token and copy it manually.
- **Use:** the caller sends it in the `X-Internal-Token` header on every call except `/health`.
- **List:** the table shows **Project**, **Created**, **Last used**, and **Requests** (authenticated calls so far). Last used is **never** until a token
  authenticates a request, then a relative time; hover a timestamp for its exact local time. The list refreshes
  every 10 seconds. Token values are not shown again.
- **Revoke:** press the red-outlined **Revoke** pill and confirm in the in-app dialog, which names the project.
  **Cancel**, Escape, or a click on the backdrop closes the dialog. The caller gets `401 Invalid token` from then on. Lost
  token = revoke it and create a new one.

Last-used timestamps are Unix seconds or null. They are persisted at most once per minute per token, so they
are approximate. Request counts are added to the file with that same write; uses between writes are
counted in memory, so a crash can lose up to a minute of them. Old token files without the field still load and show **never** until next use. A companion
`tokens.json.lock` file serializes updates across replicas without adding dependencies.

Tokens live in `tokens.json` (`SERVICE_TOKENS_FILE`). In Docker that is the `cnv_minicpm52b_model_data` volume at `/data`, so they
survive `docker compose down` and rebuilds. Without Docker it is `tokens.json` in the folder the service starts from.
Tokens are enforced only while `SERVICE_ADMIN_PASSWORD` is set.

## Ollama CPU threads

Use the synchronized slider or number field, then **Apply**. Both accept 1 to 256. Apply is disabled and
shows a busy indicator during saving; a short **Saved** confirmation follows. The page states:
“Saved to settings.json. Other replicas apply it within a few seconds.”
- Sets `num_thread` sent to Ollama with each request: how many CPU threads it uses per generation. Start near the
  number of physical cores the host can spare.
- Range 1 to 256. The field shows the value in use now (`num_thread` in `/health`; empty = Ollama decides).
- The change applies at once to the replica that answers the click and is saved to `settings.json` next to
  `tokens.json`. Other replicas pick it up within a few seconds; no restart is needed.
- The saved value beats `SERVICE_NUM_THREAD` from the env file (`.env.compose` or `.env`). Remove `settings.json` from the `/data` volume to go back
  to the env-file value.

## Replicas and parallel requests (read-only)

The tiles show replicas and Ollama parallel requests from `SERVICE_REPLICAS` and `SERVICE_OLLAMA_PARALLEL` (set the second to the `OLLAMA_NUM_PARALLEL` of your Ollama).
The muted line says **Replicas: N. Change SERVICE_REPLICAS in .env.compose (or .env on the deployment device), then rebuild.** The
page cannot change them. To change, set them in `.env.compose` (or `.env` on the deployment device), then:
```bash
docker compose --env-file .env.compose up -d --build
docker compose restart cnv_minicpm52b_model_lb
```
`unknown` means the variable is not set, which is normal without Docker. Without Docker, Ollama's parallelism is
set on the Ollama process itself (`OLLAMA_NUM_PARALLEL` in its environment).

## Problems

| Symptom | Fix |
|---|---|
| Page does not load | Service not running, wrong port, or blocked by a firewall. Test `/health`. |
| `Wrong admin password` | Password differs from `SERVICE_ADMIN_PASSWORD` in the running container. Recreate it after editing `.env.compose` or `.env`. |
| `Admin password required (set SERVICE_ADMIN_PASSWORD)` | The password is empty. Set it and restart. |
| Token lost | Revoke it and create a new one. |
| `/health` shows `ready: false` | Ollama is up but the model is not pulled yet. Use **Updates** → **Download**. |
| Never expose port 29200 to the internet | It is a LAN tool; use a firewall or VPN for remote access. |

## Limiting a token to projects

When you create a token you can list the projects it may use (comma separated; empty = all). A request whose `project` is not on the list is refused with `403`. A request without `project` is always allowed. The list cannot be edited later: revoke the token and create a new one.

## Rate limit, expiry, edits, usage and metrics

- **Requests per minute** (optional, when creating or editing a token): over the limit a call gets `429` with `Retry-After`. The count is per replica, so with several replicas behind the balancer the real limit can be up to that many times higher.
- **Expires in (days)** (optional, only when creating): after that the token is refused like a revoked one, and the row shows "Expired". Create a new token to continue.
- **Edit** changes the allowed projects and the rate limit without revoking the token.
- **Usage:** each row shows errors (answers with status 500 or above) and the average time per call. The counters are written at most once a minute.
- **Metrics:** `GET /metrics` (send the admin password in `X-Internal-Token`) returns the same numbers as Prometheus text, one series per token. It reads the shared token file, so any replica answers with the same data. `minicpm_request_seconds` is a histogram of how long each `/v1` call took on the replica that answers (to the first byte for a streamed reply); it starts from 0 when the replica restarts.
- **Project models** card: the models loaded for a single project on the replica that answered.

## Model variant

The **Model variant** card lists the MiniCPM5-2B quantizations (`Q4_K_M`, `Q8_0`, `F16`). Each row shows the estimated memory it takes (the file size plus a margin; not measured), the download size, whether Ollama already has it, and whether it would run on the GPU or the CPU (slower). The top line shows what this machine has. A quantization that fits neither the free GPU memory nor the free RAM is greyed with the reason and cannot be chosen. When Ollama runs on another machine its memory cannot be seen, so nothing is blocked and the card says so. **Use** saves the base model (the `model` setting); restart the service to apply it, then press Download on the Updates card if Ollama does not have it yet. Project models are not listed and are not changed. The model that is already loaded counts as fitting.

## Settings

The **Settings** card changes options without editing `.env.compose`, `.env` or restarting: update-check interval, caches and limits, and similar. Each row shows where its value comes from (saved, environment or default). A value saved here beats the environment variable; **Reset** removes it. Every replica reads the shared settings file, so a change applies within a few seconds. Rows marked "applies after a restart" are read once at start. The admin password, ports, replica count and other Docker settings stay in `.env` or the compose file, because the service cannot change its own container.

## Settings on the admin page

These are edited on the **Settings** card of `/admin/` and saved in `settings.json` beside the tokens, so all replicas share them: `SERVICE_UPDATE_CHECK_HOURS`, `SERVICE_MAX_INFLIGHT`, `SERVICE_CACHE_SIZE`, `SERVICE_PROJECT_PREFIX`, `SERVICE_OLLAMA_MODEL` and `SERVICE_WARM` (the last two are read at start). A saved value beats the environment variable. The compose file no longer passes them, except `SERVICE_OLLAMA_MODEL`, which the one-time `cnv_minicpm52b_model_pull` container also uses. After you change the base model and restart, pull it from the Updates card. Still in `.env`: `SERVICE_ADMIN_PASSWORD`, `SERVICE_PORT`, `SERVICE_REPLICAS`, `SERVICE_NUM_THREAD` (a starting value for the CPU threads card) and `OLLAMA_NUM_PARALLEL`.

## Updating the models

The **Updates** card lists the configured model's local and remote manifest digests. **Check now** compares
Ollama `/api/tags` with a read-only remote manifest request. The manifest's raw bytes are hashed with SHA-256,
matching Ollama's manifest digest; the registry does not have to return a `Docker-Content-Digest` header.
Checks fetch no model blobs and run every `SERVICE_UPDATE_CHECK_HOURS` (default 6); `0` disables the timer.
It is a digest comparison, not chronological version ordering: any changed manifest counts as an update.

**Download new weights** calls Ollama `POST /api/pull` and waits for success, then sends an empty chat with
`keep_alive: 0` to unload the old resident copy. The next request loads the new weights. Every API replica
shares that Ollama, so no per-replica restart is needed. Failed downloads or unloads are shown as errors;
retry the action if unloading failed. An explicit download action always unloads after a successful check,
even if another replica already pulled the weights; retries therefore work through the load balancer.
Unchanged weights are never downloaded again. The card reports model weights only, not Python package updates.

The initial read-only probe of `https://hf.co/v2/openbmb/MiniCPM5-2B-GGUF/manifests/Q4_K_M` returned HTTP 200
and manifest digest `e680c6c9f9033490cb68df7c1145f733a3a1b377b387af0937251a70d3c71a14`.
No digest header was present; hashing the response body works. Ollama's
[manifest persistence](https://github.com/ollama/ollama/blob/main/server/images.go) and
[manifest hashing](https://github.com/ollama/ollama/blob/main/manifest/manifest.go) establish the comparison format.
Live local digest equality could not be checked during implementation because localhost Ollama and Docker
were stopped. Tests cover equal and changed digests using a fake Ollama; no real weights were downloaded.

The unchanged nginx 130-second read timeout can expire before a large pull finishes. Ollama may continue that
pull; check status again before retrying, or use the manual Ollama command above. API-side pulls allow up to
30 minutes. Multiple replicas keep their own last-check status; **Check now** refreshes the one serving it.
There is no MiniCPM offline flag: set update hours to `0` to stop automatic network checks.

## Connect to this service

The **Connect to this service** card lists the addresses other services can use: the address you opened the page with, the same PC (`127.0.0.1`), another Docker container on the same network (the `_lb` name from the compose file), and a Docker container calling this PC (`host.docker.internal`), then every endpoint with a **Copy** button. Callers send a token from the tokens card in `X-Internal-Token`.

## Try it: send a real request

1. Sign in to `/admin/` and find **Try it**.
2. The token field is hidden. Signing in to the admin page makes a temporary public token named `playground (temporary)` (valid 1 day) and fills it in, and choices such as voices load by themselves. It is the public client token, not the admin password.
3. Choose an operation, complete its labeled fields and send it. Requests go to this service's public route, with the same token scope, rate/admission limits and cache as a separate client.
4. Read the HTTP status, elapsed time and response. NDJSON/SSE text appears while it arrives; JSON is formatted. HTTP errors, including 401, 422, 429 and 503, keep their response body visible. Use **Cancel** to abort an active request.
5. Copy the curl equivalent to repeat the request elsewhere. The token is always a placeholder; fill it locally. For an upload, provide the indicated input file. Multi-step operations show their follow-up request too.

Authentication is **a temporary public token made from the admin session and held only in memory**. The playground does not persist it in browser storage, put it in a URL, log it or include it in curl. Sign out or close the tab revokes it; opening the admin page in a second tab replaces it. It appears in the tokens list and counts as an active token while it lives. The existing admin sign-in is separate. A playground run consumes normal service quotas and can generate retained files where the public API normally does so; cancelling the browser fetch does not promise to undo work already accepted by a backend.

The browser caps uploads at **10 MiB**, displayed text at **256 KiB**, and binary audio/image responses at **32 MiB**. Server limits still apply and may be lower. Media uses bounded Blob URLs, revoked when replaced or cleared. Text is rendered as text, never executed as HTML. Audio players become available after the bounded audio download finishes. No new environment setting is needed.

### This service's request

Chat sends messages to `POST /v1/chat`, with optional project and generation controls. Streaming appends NDJSON events as they arrive. MiniCPM also accepts thinking, tools or a JSON schema; tools and schema are mutually exclusive.

## Updating the model files

The service loads only downloaded weights and tokenizer files. Startup, warm-up and requests never download. With no downloaded model, `/health` and `/admin/` answer, `ready` is false and inference returns 503 with an admin Download hint. First start: open `/admin/` → **Updates** → **Download**. The Updates card compares the downloaded Ollama manifest digest with the online registry manifest at startup, every `update_check_hours` and on **Check now**. Checks fetch metadata only; only **Download** runs `ollama pull`. A successful download invalidates every replica through shared storage; each replica reloads on its next request.

Admin curl alternative:

```bash
curl -X POST http://127.0.0.1:29200/admin/updates/weights -H "X-Internal-Token: <ADMIN_PASSWORD>"
```

## Admin pages and navigation

Open `/admin/` and sign in. Overview opens first; each sidebar destination has its own URL fragment and supports browser Back/Forward and direct links:

- `#overview`: health, model readiness, replicas, active tokens and request counts.
- `#playground`: service-specific request forms and streamed or media responses.
- `#tokens`: create, edit, expire and revoke scoped access tokens.
- `#runtime`: CPU threads where supported, replica information and live/restart settings.
- `#updates`: check versions and explicitly download weights.
- `#connections`: caller addresses and public endpoints.
- `#models` and `#voices`: project models and cloned voices, shown only when supported.

Collapse/Expand switches between labeled sidebar and icon rail on desktop and mobile. The non-secret sidebar preference is saved per service in this browser. Navigation preserves form drafts and active Playground requests; sign-out still clears credentials and cancels work. Page names appear once in the main header. No preview data or cross-service selector ships in the real admin UI. Existing authentication, quotas, settings precedence and explicit-download policy remain in effect.
