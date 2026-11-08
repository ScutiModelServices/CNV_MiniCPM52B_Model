# CNV_MiniCPM52B_Model: Docker on the same device

Run the service in Docker on your own machine. It calls the Ollama already running on the host, which holds the model;
the API replicas sit behind nginx.

## Requirements

- Docker with Compose v2.
- Ollama on the host, listening on `0.0.0.0`, with the model pulled once: `ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M` (about 1.5 GB).
- CPU is enough.
- RAM for the model and Ollama's concurrent contexts. API replicas share one model in Ollama; they do not load
  independent torch models. Higher `OLLAMA_NUM_PARALLEL` can increase memory use.

## Steps

1. Configure. In `CNV_MiniCPM52B_Model/`:
   ```bash
   cp .env.example .env
   ```
   Set `SERVICE_ADMIN_PASSWORD` (empty = no auth, local-only). Defaults are fine for the rest:
   `SERVICE_PORT=29200`, `SERVICE_REPLICAS=1`, `OLLAMA_NUM_PARALLEL=2`. Set `SERVICE_NUM_THREAD` to your real core
   count (compose default 4). For another quantisation set `SERVICE_OLLAMA_MODEL=hf.co/openbmb/MiniCPM5-2B-GGUF:Q8_0`.
2. Start:
   ```bash
   docker compose up -d --build
   ```
   Order: `cnv_minicpm52b_model` starts, then `cnv_minicpm52b_model_lb`.
3. Check:
   ```bash
   curl http://127.0.0.1:29200/health
   ```
   Expect `{"status": "ok", "model": ..., "ready": true}`.
4. Try a chat (add `-H "X-Internal-Token: <token>"` if a password is set; make tokens at
   `http://127.0.0.1:29200/admin/`):
   ```bash
   curl -X POST http://127.0.0.1:29200/v1/chat -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"Say hi"}],"thinking":false}'
   ```

## Callers

- From the host: `http://127.0.0.1:29200`.
- From another Docker stack on the same host: `http://host.docker.internal:29200`, or join this stack's `cnv_minicpm52b_model`
  network from the caller's compose file (`external: true`) and use `http://cnv_minicpm52b_model_lb:29200`. The service itself
  never references its callers.

## Registering a fine-tuned project model

Copy your GGUF to this host as `file.gguf`. Beside it, create `Modelfile` containing `FROM ./file.gguf`, then:

```bash
ollama create minicpm-my-project -f Modelfile
```

Send `"project":"my-project"` with chat requests. Names match `^[a-z0-9][a-z0-9_-]{0,47}$`; invalid names
return 422. `SERVICE_PROJECT_PREFIX` defaults to `minicpm-` when unset; use that prefix in the create command.
An absent project uses the base model. A missing project model uses the base with `fallback: true`;
`model` reports the selection. `/health` adds `project_models`, while `ready` still checks the base model.
A model made with `ollama create` is found at once (a miss re-asks Ollama); if the lookup fails, the base is used
with `fallback: true`.

On CPU, model changes can reload weights and add latency. set `OLLAMA_MAX_LOADED_MODELS=1` on the host Ollama;
increase it only if RAM permits keeping more models loaded.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `SERVICE_REPLICAS` | `1` | Number of API containers, read by Compose and shown read-only in `/admin/`. |
| `SERVICE_PORT` | `29200` | Published nginx port; containers always listen on 29200. Also the local service port. |
| `SERVICE_HOST` | `127.0.0.1` | Local bind address; the Dockerfile sets `0.0.0.0`. |
| `SERVICE_ADMIN_PASSWORD` | empty | Unlocks `/admin/` and enables project-token auth. Empty means no auth; use only locally. |
| `SERVICE_OLLAMA_MODEL` | `hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M` | Model name in the host Ollama; pull it there first. |
| `SERVICE_PROJECT_PREFIX` | `minicpm-` | Prefix for installed project models selected by optional chat `project`. Works when unset. |
| `SERVICE_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint. Compose defaults to `http://host.docker.internal:11434` (the Ollama on the host; pull the model there once). |
| `SERVICE_UPDATE_CHECK_HOURS` | `6` | Check remote model manifests at startup and every N hours; `0` disables automatic checks. Manual checks remain available. |
| `SERVICE_MAX_INFLIGHT` | 4 x `OLLAMA_NUM_PARALLEL` | Calls one replica lets wait on Ollama at once. More get `429` with `Retry-After: 5` instead of timing out. An Ollama `503` (its own queue is full) also becomes `429`. |
| `SERVICE_WARM` | `1` | Load the model at startup with an empty chat and a 30-minute keep-alive. `0` leaves loading to the first request. Failure is logged and does not stop the API. |
| `SERVICE_NUM_THREAD` | Ollama decides | CPU threads per request; Compose sets `4`. Admin changes are saved and override this at next start. Other replicas apply them on restart. Use the number of physical cores (`lscpu`: cores x sockets), not logical CPUs; more threads than cores slows every call. Ollama defaults to the logical count. |
| `OLLAMA_NUM_PARALLEL` | `2` in Compose | Parallel generations on the shared Ollama host; shown read-only in `/admin/`. |
| `SERVICE_TOKENS_FILE` | `tokens.json` | Token storage; Docker uses `/data/tokens.json` in the `cnv_minicpm52b_model_data` volume. Saved settings live beside it. |
| `SERVICE_WEB_DIR` | repository `web/` | Admin static files; Docker uses `/app/web`. |

Set Compose variables in the shell or a local `.env` beside `docker-compose.yml`. Do not expose the service
without setting an admin password. Create a separate project token for each caller in `/admin/` and send it
as `X-Internal-Token`. The admin password is only for admin routes.

## First start

1. `cnv_minicpm52b_model` replicas start and warm the model unless warm-up is turned off on the Settings card. Their HTTP health checks let `cnv_minicpm52b_model_lb` start.

If `ready` stays false, check that the host Ollama is running, listens on `0.0.0.0` and has the model (`ollama list`).

## Check it works

```bash
curl http://127.0.0.1:29200/health
```

Expect `{"status":"ok","model":"hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M","ready":true,"num_thread":4,"project_models":[]}`
with Compose defaults. `ready` means Ollama answers and has the model pulled, not that a generation has run.
The endpoint needs no token. HTTP 200 by itself only proves the API is up; inspect `ready` too.

```bash
curl -X POST http://127.0.0.1:29200/v1/chat -H "Content-Type: application/json" -H "X-Internal-Token: <token>" -d '{"messages":[{"role":"user","content":"Say hello"}],"thinking":false,"max_tokens":32}'
```

The example uses shell single-quote syntax (PowerShell or bash). Omit the token header when auth is disabled.

## Network

The stack owns its `cnv_minicpm52b_model` network. Other stacks can call `http://host.docker.internal:29200` on the same host,
or join that network and call `http://cnv_minicpm52b_model_lb:29200`. Ollama's port is not published by Compose.

## Daily use

```bash
docker compose ps
docker compose logs -f cnv_minicpm52b_model ollama
docker compose down      # keeps the ollama volume (model) and tokens volume
SERVICE_REPLICAS=3 docker compose up -d --build && docker compose restart cnv_minicpm52b_model_lb   # scale
```

## Problems

| Symptom | Fix |
|---|---|
| `/health` `ready: false` | Ollama on the host is down, not on `0.0.0.0`, or the model is missing: `ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M`. |
| 504 | Ollama timed out; lower `OLLAMA_NUM_PARALLEL` or set `SERVICE_NUM_THREAD` to real cores. |
| `address already in use` on 29200 | `run.bat` or another copy is running. |
| `cnv_minicpm52b_model_lb` never starts | API health checks have not passed; inspect `docker compose logs cnv_minicpm52b_model`. |
| `/health` has `ready: false` | Ollama is unreachable or the configured model is missing. Check `SERVICE_OLLAMA_URL`, `SERVICE_OLLAMA_MODEL` and `ollama list`. |
| 401 from chat | Missing or revoked project token. Make one in `/admin/`. |
| 422 from chat | Invalid input or Ollama rejected options; inspect the response detail. |
| 504 from chat | Ollama generation exceeded the API timeout. Reduce token budget, concurrency or context size. |
| 502 from chat | Ollama is down, model missing, or output did not match requested JSON. Inspect detail and Ollama logs. |
| Ollama restarts or requests stall | Insufficient RAM for model and concurrent contexts; lower `OLLAMA_NUM_PARALLEL`. |
| nginx 502 after scaling | Restart `cnv_minicpm52b_model_lb` to refresh replica addresses. |
| Port 29200 already in use | Another local or Compose instance is running. Stop one or change the published port. |

Note: the project README says this compose stack has not been run end to end yet, so expect to fix small things on
the first try.

The load balancer also joins external `citadel_core` as `cnv_minicpm52b_model_lb`. Start the Citadel stack first so that network exists; tool services on it use `http://cnv_minicpm52b_model_lb:29200`. Docker project and API image are `cnv_minicpm52b_model` and `cnv_minicpm52b_model:latest`; persistent volumes have explicit `cnv_minicpm52b_model_*` names. Volume migration and rollback instructions are in [CHANGELOG.md](../CHANGELOG.md).
