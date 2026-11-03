# Deploying MiniCPM52BModel

MiniCPM52BModel serves MiniCPM5-2B over HTTP on port 9130. FastAPI replicas (`minicpm`) sit behind nginx
(`minicpm_lb`), and all replicas use one `ollama` host. The one-shot `minicpm_pull` service pulls
`hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M` before the API starts. Callers use `http://<host>:9130`.

## Requirements

- Docker with Compose v2, or Python 3.11+, uv and a running Ollama for a local run.
- RAM for the model and Ollama's concurrent contexts. API replicas share one model in Ollama; they do not load
  independent torch models. Higher `OLLAMA_NUM_PARALLEL` can increase memory use.
- Network access for the first model pull. Later starts reuse the persistent `ollama` volume.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `MINICPM_REPLICAS` | `1` | Number of API containers, read by Compose and shown read-only in `/admin/`. |
| `MINICPM_PORT` | `9130` | Published nginx port; containers always listen on 9130. Also the local service port. |
| `MINICPM_HOST` | `127.0.0.1` | Local bind address; the Dockerfile sets `0.0.0.0`. |
| `MINICPM_ADMIN_PASSWORD` | empty | Unlocks `/admin/` and enables project-token auth. Empty means no auth; use only locally. |
| `MINICPM_MODEL` | `hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M` | Model pulled and served by Ollama. |
| `MINICPM_PROJECT_PREFIX` | `minicpm-` | Prefix for installed project models selected by optional chat `project`. Works when unset. |
| `OLLAMA_MAX_LOADED_MODELS` | `1` in Compose | Maximum resident models on Ollama; more models need more RAM. For local runs set this on the Ollama process. |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint; Compose sets `http://ollama:11434`. |
| `MINICPM_UPDATE_CHECK_HOURS` | `6` | Check remote model manifests at startup and every N hours; `0` disables automatic checks. Manual checks remain available. |
| `MINICPM_MAX_INFLIGHT` | 4 x `OLLAMA_NUM_PARALLEL` | Calls one replica lets wait on Ollama at once. More get `429` with `Retry-After: 5` instead of timing out. An Ollama `503` (its own queue is full) also becomes `429`. |
| `MINICPM_WARM` | `1` | Load the model at startup with an empty chat and a 30-minute keep-alive. `0` leaves loading to the first request. Failure is logged and does not stop the API. |
| `MINICPM_NUM_THREAD` | Ollama decides | CPU threads per request; Compose sets `4`. Admin changes are saved and override this at next start. Other replicas apply them on restart. |
| `OLLAMA_NUM_PARALLEL` | `2` in Compose | Parallel generations on the shared Ollama host; shown read-only in `/admin/`. |
| `MINICPM_TOKENS_FILE` | `tokens.json` | Token storage; Docker uses `/data/tokens.json` in the `tokens` volume. Saved settings live beside it. |
| `MINICPM_WEB_DIR` | repository `web/` | Admin static files; Docker uses `/app/web`. |

Set Compose variables in the shell or a local `.env` beside `docker-compose.yml`. Do not expose the service
without setting an admin password. Create a separate project token for each caller in `/admin/` and send it
as `X-Internal-Token`. The admin password is only for admin routes.

## Standalone

```bash
docker compose up -d --build
```

The stack owns its `minicpm` network. Other stacks can call `http://host.docker.internal:9130` on the same host,
or join that network and call `http://minicpm_lb:9130`. Ollama's port is not published by Compose.

Without Docker, start Ollama, then:

```bash
ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
run.bat
```

`run.bat` syncs dependencies with uv and starts FastAPI at `http://127.0.0.1:9130`. If uv reports a cross-drive
cache error, set PowerShell `$env:UV_CACHE_DIR = "D:/User/.uv-cache"` and retry.

## First start

1. Compose starts `ollama` and waits for its health check.
2. `minicpm_pull` downloads the model into the shared `ollama` volume and exits successfully.
3. `minicpm` replicas start after that pull completes and warm the model unless warm-up is turned off on the Settings card. Their HTTP health checks let `minicpm_lb` start.

The model volume survives rebuilds. The pull can take time on a slow connection; inspect
`docker compose logs minicpm_pull` and `docker compose ps` before retrying.

## Check it works

```bash
curl http://127.0.0.1:9130/health
```

Expect `{"status":"ok","model":"hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M","ready":true,"num_thread":4,"project_models":[]}`
with Compose defaults. `ready` means Ollama answers and has the model pulled, not that a generation has run.
The endpoint needs no token. HTTP 200 by itself only proves the API is up; inspect `ready` too.

```bash
curl -X POST http://127.0.0.1:9130/v1/chat -H "Content-Type: application/json" -H "X-Internal-Token: <token>" -d '{"messages":[{"role":"user","content":"Say hello"}],"thinking":false,"max_tokens":32}'
```

The example uses shell single-quote syntax (PowerShell or bash). Omit the token header when auth is disabled.

## Registering a fine-tuned project model

Copy `file.gguf` to the deployment host and create a `Modelfile` beside it containing `FROM ./file.gguf`.
For local Ollama, run `ollama create minicpm-my-project -f Modelfile` in that folder. For Compose:

```bash
docker compose cp file.gguf ollama:/tmp/file.gguf
docker compose cp Modelfile ollama:/tmp/Modelfile
docker compose exec -w /tmp ollama ollama create minicpm-my-project -f Modelfile
```

The registered model persists in the Ollama volume. Send `"project":"my-project"` in `/v1/chat`;
project names match `^[a-z0-9][a-z0-9_-]{0,47}$`. Replace `minicpm-` if using `MINICPM_PROJECT_PREFIX`.
The prefix defaults to `minicpm-` even when unset. No project uses the base model; an uninstalled project
falls back to the base with `fallback: true`. Replies report the selected `model`; health `project_models`
lists installed canonical project names, while `ready` still checks the base model. Inventory is cached
30 seconds per API replica; a project missing from the cache re-asks Ollama (at most every 2 seconds), and `/health`
always asks. If the lookup fails the request uses the base with `fallback: true`; a real outage still shows as the
base call's 502/504.

On CPU, switching models can unload and reload weights, adding latency. Compose sets
`OLLAMA_MAX_LOADED_MODELS=1` to bound resident model count; raise it only with sufficient RAM.
For local runs set it in Ollama's environment and restart Ollama. Parallel contexts also consume RAM.

## Scaling

```powershell
$env:MINICPM_REPLICAS = "3"
docker compose up -d --build
docker compose restart minicpm_lb
```

nginx resolves replica addresses at start, so restart it after changing the count. `least_conn` balances API
requests; Ollama queues and schedules actual generations. Increasing API replicas does not create more model
copies or change Ollama's parallelism. Tune `OLLAMA_NUM_PARALLEL` on Ollama for that.

nginx allows 130-second responses and 2 MB bodies. It may retry connection errors, but never retries timeouts:
repeating a slow generation could run the same turn twice. Keep these settings distinct from Laya's.

## Update

```bash
docker compose up -d --build
docker compose restart minicpm_lb
```

Rebuilding replaces API replicas; restart nginx afterwards. Persistent model and token volumes survive.
To pull model weights manually through the shared Ollama host:

```bash
docker compose exec ollama ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
```

All API replicas use that same Ollama. Model updates do not require per-replica API restarts.

### Updating the model from `/admin/`

The **Updates** card lists the configured model's local and remote manifest digests. **Check now** compares
Ollama `/api/tags` with a read-only remote manifest request. The manifest's raw bytes are hashed with SHA-256,
matching Ollama's manifest digest; the registry does not have to return a `Docker-Content-Digest` header.
Checks fetch no model blobs and run every `MINICPM_UPDATE_CHECK_HOURS` (default 6); `0` disables the timer.
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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `minicpm_lb` never starts | API health checks have not passed; inspect `docker compose logs minicpm`. |
| `minicpm` never starts | Initial pull has not completed; inspect `docker compose logs minicpm_pull`. |
| `/health` has `ready: false` | Ollama is unreachable or the configured model is missing. Check `OLLAMA_URL`, `MINICPM_MODEL` and `ollama list`. |
| 401 from chat | Missing or revoked project token. Make one in `/admin/`. |
| 422 from chat | Invalid input or Ollama rejected options; inspect the response detail. |
| 504 from chat | Ollama generation exceeded the API timeout. Reduce token budget, concurrency or context size. |
| 502 from chat | Ollama is down, model missing, or output did not match requested JSON. Inspect detail and Ollama logs. |
| Ollama restarts or requests stall | Insufficient RAM for model and concurrent contexts; lower `OLLAMA_NUM_PARALLEL`. |
| nginx 502 after scaling | Restart `minicpm_lb` to refresh replica addresses. |
| Port 9130 already in use | Another local or Compose instance is running. Stop one or change the published port. |

### Releasing

A plain publish (`tools\deploy.bat` option 1) updates only the `live` branch. For a release, set `VERSION` and
the version in `pyproject.toml` to the same number, commit, then run `tools\deploy.bat` option R or
`uv run tools/publish_live.py --release`. This publishes `live` and tags the main commit `vX.Y.Z` and live commit
`live-vX.Y.Z`. Different live files under an existing release version are refused; bump `VERSION`. Before an
official release, `--replace` can move its tags.

`tools\make_zip.bat` builds `zip_versions\MiniCPM52BModel-<VERSION>.zip` and opens GitHub's new-release page.
The archive excludes development tools, ignored files and old ZIPs; its example configuration is included.

## Settings on the admin page

These are edited on the **Settings** card of `/admin/` and saved in `settings.json` beside the tokens, so all replicas share them: `MINICPM_UPDATE_CHECK_HOURS`, `MINICPM_MAX_INFLIGHT`, `MINICPM_CACHE_SIZE`, `MINICPM_PROJECT_PREFIX`, `MINICPM_MODEL` and `MINICPM_WARM` (the last two are read at start). A saved value beats the environment variable. The compose file no longer passes them, except `MINICPM_MODEL`, which the one-time `minicpm_pull` container also uses. After you change the base model and restart, pull it from the Updates card. Still in `.env`: `MINICPM_ADMIN_PASSWORD`, `MINICPM_PORT`, `MINICPM_REPLICAS`, `MINICPM_NUM_THREAD` (a starting value for the CPU threads card) and `OLLAMA_NUM_PARALLEL`.
