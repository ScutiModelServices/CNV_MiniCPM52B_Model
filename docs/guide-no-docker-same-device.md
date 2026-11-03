# MiniCPM52BModel: without Docker, same device

Two processes: Ollama (holds the model) and the MiniCPM service (`src/app.py`, port 9130).

## Requirements
- Python 3.11 or newer.
- Ollama installed from https://ollama.com (Windows, macOS, Linux).
- Internet once, to pull the model (about 1 GB).

## 1. Ollama and the model
Make sure Ollama is running (the Windows/macOS app starts it; on Linux run `ollama serve`). It listens on
`127.0.0.1:11434`. Then:
```bash
ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
ollama list
```
(`:Q8_0` and `:F16` also exist; if you use one, set `MINICPM_MODEL` to the same tag.)

## 2. The service
Windows: double-click `run.bat` (runs `uv sync` to create or update `.venv`, then starts the service). Needs uv: `python -m pip install uv`.

Linux / macOS:
```bash
cd MiniCPM52BModel
uv sync
uv run python src/app.py
```

## Settings
The service reads the real environment, **not** `.env`. Set before starting:

| Variable | Default | Effect |
|---|---|---|
| `MINICPM_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` only if other devices need it). |
| `MINICPM_PORT` | `9130` | Port. |
| `MINICPM_ADMIN_PASSWORD` | empty | Unlocks `/admin/` and requires tokens on all calls except `/health`. Empty = no auth. |
| `MINICPM_MODEL` | `hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M` | Must match the tag you pulled. |
| `MINICPM_PROJECT_PREFIX` | `minicpm-` | Prefix for project models; selection works when unset. |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Where Ollama is. |
| `MINICPM_NUM_THREAD` | Ollama decides | CPU threads per request. |

PowerShell: `$env:MINICPM_ADMIN_PASSWORD = "secret"; .\run.bat`
Bash: `MINICPM_ADMIN_PASSWORD=secret python src/app.py`

## Check
```bash
curl http://127.0.0.1:9130/health
```
Expect `"ready": true`. Then:
```bash
curl -X POST http://127.0.0.1:9130/v1/chat -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"Say hi"}],"thinking":false}'
```
(Add `-H "X-Internal-Token: <token>"` if a password is set; make tokens at `http://127.0.0.1:9130/admin/`.)

## Registering a fine-tuned project model

Copy the fine-tuned GGUF to the Ollama host as `file.gguf`. Beside it create `Modelfile` containing:

```dockerfile
FROM ./file.gguf
```

In that folder run `ollama create minicpm-my-project -f Modelfile`. Send `"project":"my-project"`
in `/v1/chat`. Project names match `^[a-z0-9][a-z0-9_-]{0,47}$`; invalid values return 422. Use your
`MINICPM_PROJECT_PREFIX` if changed; its unset default is `minicpm-`. An absent project uses the base.
An uninstalled project also uses the base with `fallback: true`; replies report the selected `model`.
`/health` adds installed `project_models`, while `ready` still checks the base model. A model made with
`ollama create` is found at once (a miss re-asks Ollama); if the lookup fails, the base is used with `fallback: true`.

CPU model changes may unload and reload weights, adding latency. Set `OLLAMA_MAX_LOADED_MODELS=1` in
the Ollama process environment and restart Ollama to limit resident models; increase it if RAM permits.
Setting this only on the Python service has no effect on Ollama.

## Stop

Ctrl+C in the service terminal. Quit Ollama separately if you want the RAM back. Do not run this and the Docker stack
together: both use port 9130.

## Problems
| Symptom | Fix |
|---|---|
| `ready: false` | Ollama not running, or the model tag differs from `MINICPM_MODEL`. Check `ollama list`. |
| 502 on calls | Ollama down or model missing. |
| 504 | Ollama timed out; set `MINICPM_NUM_THREAD` to real cores. |
| Docker callers cannot reach it | Start with `MINICPM_HOST=0.0.0.0` and use `http://host.docker.internal:9130`. |

## Tests
`uv run pytest` (they fake Ollama).
