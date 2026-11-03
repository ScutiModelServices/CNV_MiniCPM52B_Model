# MiniCPM52BModel: Docker on the same device

Run the service plus Ollama in Docker on your own machine. Ollama holds the model; the API replicas sit behind
nginx.

## Requirements
- Docker with Compose v2.
- Internet on the first start (Ollama pulls the MiniCPM5-2B Q4_K_M model, about 1.5 GB).
- CPU is enough.

## Steps
1. Configure. In `MiniCPM52BModel/`:
   ```bash
   cp .env.example .env
   ```
   Set `MINICPM_ADMIN_PASSWORD` (empty = no auth, local-only). Defaults are fine for the rest:
   `MINICPM_PORT=9130`, `MINICPM_REPLICAS=1`, `OLLAMA_NUM_PARALLEL=2`. Set `MINICPM_NUM_THREAD` to your real core
   count (compose default 4). For another quantisation set `MINICPM_MODEL=hf.co/openbmb/MiniCPM5-2B-GGUF:Q8_0`.
2. Start:
   ```bash
   docker compose up -d --build
   ```
   Order: `ollama` starts, `minicpm_pull` downloads the model and exits, `minicpm` starts, then `minicpm_lb`.
   `minicpm_pull` showing `Exited (0)` is normal.
3. Check:
   ```bash
   curl http://127.0.0.1:9130/health
   ```
   Expect `{"status": "ok", "model": ..., "ready": true}`.
4. Try a chat (add `-H "X-Internal-Token: <token>"` if a password is set; make tokens at
   `http://127.0.0.1:9130/admin/`):
   ```bash
   curl -X POST http://127.0.0.1:9130/v1/chat -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"Say hi"}],"thinking":false}'
   ```

## Callers
- From the host: `http://127.0.0.1:9130`.
- From another Docker stack on the same host: `http://host.docker.internal:9130`, or join this stack's `minicpm`
  network from the caller's compose file (`external: true`) and use `http://minicpm_lb:9130`. The service itself
  never references its callers.

## Registering a fine-tuned project model

Copy your GGUF to this host as `file.gguf`. Beside it, create `Modelfile` containing `FROM ./file.gguf`, then:

```bash
docker compose cp file.gguf ollama:/tmp/file.gguf
docker compose cp Modelfile ollama:/tmp/Modelfile
docker compose exec -w /tmp ollama ollama create minicpm-my-project -f Modelfile
```

Send `"project":"my-project"` with chat requests. Names match `^[a-z0-9][a-z0-9_-]{0,47}$`; invalid names
return 422. `MINICPM_PROJECT_PREFIX` defaults to `minicpm-` when unset; use that prefix in the create command.
An absent project uses the base model. A missing project model uses the base with `fallback: true`;
`model` reports the selection. `/health` adds `project_models`, while `ready` still checks the base model.
A model made with `ollama create` is found at once (a miss re-asks Ollama); if the lookup fails, the base is used
with `fallback: true`.

On CPU, model changes can reload weights and add latency. Compose sets `OLLAMA_MAX_LOADED_MODELS=1`;
increase this on Ollama only if RAM permits keeping more models loaded.

## Daily use

```bash
docker compose ps
docker compose logs -f minicpm ollama
docker compose down      # keeps the ollama volume (model) and tokens volume
MINICPM_REPLICAS=3 docker compose up -d --build && docker compose restart minicpm_lb   # scale
```

## Problems
| Symptom | Fix |
|---|---|
| `minicpm` never starts | `minicpm_pull` failed (no internet, wrong tag). `docker compose logs minicpm_pull`. |
| `/health` `ready: false` | Ollama up but model missing; run `docker compose up -d` to repeat the pull. |
| 502 from calls | Ollama down or model missing. |
| 504 | Ollama timed out; lower `OLLAMA_NUM_PARALLEL` or set `MINICPM_NUM_THREAD` to real cores. |
| nginx 502 after changing replicas | `docker compose restart minicpm_lb`. |
| `address already in use` on 9130 | `run.bat` or another copy is running. |

Note: the project README says this compose stack has not been run end to end yet, so expect to fix small things on
the first try.
