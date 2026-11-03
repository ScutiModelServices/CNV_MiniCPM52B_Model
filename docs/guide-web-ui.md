# MiniCPM52BModel: the admin web UI

One page, `/admin/`, for tokens and Ollama CPU threads. It is the same for every way of running the service (Docker
on the same device, Docker on a deployment device, no Docker). Only the address differs:

| Run | Address |
|---|---|
| Docker or `run.bat` on this PC | `http://127.0.0.1:9130/admin/` |
| Docker on the deployment box | `http://<box-ip>:9130/admin/` |

If `MINICPM_PORT` is changed, use that port.

## Before you open it
The page needs the admin password: the value of `MINICPM_ADMIN_PASSWORD`.
- Docker: set it in `.env` next to `docker-compose.yml`, then `docker compose up -d`.
- No Docker: set it in the environment before starting the service (`run.bat` / `uvicorn`); the service reads the
  real environment, not `.env`.
- Empty password = no auth at all and the page answers `Admin password required (set MINICPM_ADMIN_PASSWORD)`. Set
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

Tokens live in `tokens.json` (`MINICPM_TOKENS_FILE`). In Docker that is the `tokens` volume at `/data`, so they
survive `docker compose down` and rebuilds. Without Docker it is `tokens.json` in the folder the service starts from.
Tokens are enforced only while `MINICPM_ADMIN_PASSWORD` is set.

## Ollama CPU threads
Use the synchronized slider or number field, then **Apply**. Both accept 1 to 256. Apply is disabled and
shows a busy indicator during saving; a short **Saved** confirmation follows. The page states:
“Saved to settings.json. Other replicas apply it on restart.”
- Sets `num_thread` sent to Ollama with each request: how many CPU threads it uses per generation. Start near the
  number of physical cores the host can spare.
- Range 1 to 256. The field shows the value in use now (`num_thread` in `/health`; empty = Ollama decides).
- The change applies at once to the replica that answers the click and is saved to `settings.json` next to
  `tokens.json`. Other replicas pick it up when they restart: `docker compose restart minicpm`.
- The saved value beats `MINICPM_NUM_THREAD` from `.env`. Remove `settings.json` from the `/data` volume to go back
  to the `.env` value.

## Replicas and parallel requests (read-only)
The tiles show replicas and Ollama parallel requests from `MINICPM_REPLICAS` and `OLLAMA_NUM_PARALLEL`.
The muted line says **Replicas: N. Change MINICPM_REPLICAS in .env, then rebuild.** The
page cannot change them. To change, set them in `.env`, then:
```bash
docker compose up -d --build
docker compose restart minicpm_lb
```
`unknown` means the variable is not set, which is normal without Docker. Without Docker, Ollama's parallelism is
set on the Ollama process itself (`OLLAMA_NUM_PARALLEL` in its environment).

## Problems
| Symptom | Fix |
|---|---|
| Page does not load | Service not running, wrong port, or blocked by a firewall. Test `/health`. |
| `Wrong admin password` | Password differs from `MINICPM_ADMIN_PASSWORD` in the running container. Recreate it after editing `.env`. |
| `Admin password required (set MINICPM_ADMIN_PASSWORD)` | The password is empty. Set it and restart. |
| Token lost | Revoke it and create a new one. |
| `/health` shows `ready: false` | Ollama is up but the model is not pulled yet. See the run guide for `ollama pull`. |
| Never expose port 9130 to the internet | It is a LAN tool; use a firewall or VPN for remote access. |

## Limiting a token to projects

When you create a token you can list the projects it may use (comma separated; empty = all). A request whose `project` is not on the list is refused with `403`. A request without `project` is always allowed. The list cannot be edited later: revoke the token and create a new one.

## Rate limit, expiry, edits, usage and metrics

- **Requests per minute** (optional, when creating or editing a token): over the limit a call gets `429` with `Retry-After`. The count is per replica, so with several replicas behind the balancer the real limit can be up to that many times higher.
- **Expires in (days)** (optional, only when creating): after that the token is refused like a revoked one, and the row shows "Expired". Create a new token to continue.
- **Edit** changes the allowed projects and the rate limit without revoking the token.
- **Usage:** each row shows errors (answers with status 500 or above) and the average time per call. The counters are written at most once a minute.
- **Metrics:** `GET /metrics` (send the admin password in `X-Internal-Token`) returns the same numbers as Prometheus text, one series per token. It reads the shared token file, so any replica answers with the same data.
- **Project models** card: the models loaded for a single project on the replica that answered.

## Settings

The **Settings** card changes options without editing `.env` or restarting: update-check interval, caches and limits, and similar. Each row shows where its value comes from (saved, environment or default). A value saved here beats the environment variable; **Reset** removes it. Every replica reads the shared settings file, so a change applies within a few seconds. Rows marked "applies after a restart" are read once at start. The admin password, ports, replica count and other Docker settings stay in `.env` or the compose file, because the service cannot change its own container.
