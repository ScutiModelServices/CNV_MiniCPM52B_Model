# CNV_MiniCPM52B_Model

The one place MiniCPM5-2B is served. It sits in front of Ollama; projects send it turns over HTTP instead of
talking to Ollama or loading the model themselves. It is built as a tool-calling model: one call is one model turn,
and the caller runs the tools and the loop.

## API

| Call | Purpose |
|---|---|
| `POST /v1/chat` | `{"messages", "project", "tools", "schema", "thinking", "temperature", "max_tokens", "stream"}` → `{"content", "thinking", "tool_calls", "finish_reason", "model", "fallback", "usage", "duration_ms"}`. |
| `GET /health` | `{"status": "ok", "model", "ready", "num_thread", "project_models"}`; `ready` checks the configured base model. `project_models` lists installed project model names. No token needed. |

**Streaming.** With `"stream": true` the reply is `application/x-ndjson`: one JSON object per line while the answer is
made, so the first words arrive at once instead of after the whole answer. Lines are `{"type": "content", "text"}`
(and `{"type": "thinking", "text"}` while the model thinks) and finally `{"type": "done", ...}` holding the same body a call without `stream` returns. A refusal before
the first piece (a conversation that is too long, a full queue) is still a normal HTTP error; a failure after it is
sent as `{"type": "error", "status", ...}` and ends the stream. A kept answer (see below) is streamed back in one piece.

**Cache.** The *Answers kept per replica* setting (`SERVICE_CACHE_SIZE`, default 256, 0 = off) keeps the last answers
for identical requests and sends them again without generating (`X-Cache: hit`). Only requests with `temperature` 0
(the default) are kept, because a higher temperature asks for a different answer each time. The key holds the model Ollama has for the project (its digest, read from the inventory at most every 30 seconds), so a replaced project model or newly pulled weights are not answered from the old cache. The cache is per
replica, so with N replicas the hit rate is about 1/N. `stream` does not change the key: a streamed answer fills the
cache for plain calls and the other way round.

**Tool calling.** Send `tools` (`[{"name", "description", "parameters": <JSON schema>}]`). When the model wants a tool
it answers `finish_reason: "tool_calls"` with `tool_calls: [{"id", "name", "arguments"}]`. Run them, append the
assistant turn (`{"role": "assistant", "tool_calls": [...]}`) and one `{"role": "tool", "tool_name", "content"}` per
result to `messages`, and call again; repeat until `finish_reason` is `stop`. `tools` and `schema` cannot be sent
together.

**Structured output.** A JSON `schema` constrains the reply and also returns it as `parsed`.

**Project models.** Optional `"project": "my-project"` selects `minicpm-my-project` when installed in Ollama,
including its `:latest` alias. Project names must match `^[a-z0-9][a-z0-9_-]{0,47}$`; invalid names return 422.
An absent or null project uses `SERVICE_OLLAMA_MODEL`. An unavailable project also uses that base model and returns
`"fallback": true`; all other successful replies return false. `model` reports the model actually selected.
Inventory is cached for 30 seconds per API replica. A project that is not in the cache re-asks Ollama (at most every
2 seconds), so a model just made with `ollama create` is found at once; `/health` always asks Ollama. If Ollama cannot
list its models the request uses the base model with `"fallback": true`, and the base call reports a real outage.

### Registering a fine-tuned project model

Copy your fine-tuned GGUF to the Ollama host, alongside a file named `Modelfile` containing:

```dockerfile
FROM ./file.gguf
```

From that folder run `ollama create minicpm-my-project -f Modelfile`, then send `"project": "my-project"`.
Use `minicpm-<project>` for other projects, or your configured prefix. See the deployment guides for container
copy commands. The prefix defaults to `minicpm-`, so routing works when `SERVICE_PROJECT_PREFIX` is unset.
Project selection does not train or download weights, and project tokens do not restrict model selection.

On CPU, changing models can unload and reload weights, increasing latency. Set `OLLAMA_MAX_LOADED_MODELS`
on the Ollama process to control how many models may remain loaded; Compose defaults to `1` to limit RAM.
Increase it only when memory permits; API replica count does not create independent model copies.

**Thinking** is on by default (`"thinking": false` turns it off). The reasoning comes back in `thinking`, and it
counts against `max_tokens` (default 2048): `finish_reason: "length"` means the budget ran out, often with empty
`content`.

When `SERVICE_ADMIN_PASSWORD` is set, send a project token (made on `/admin/`) as `X-Internal-Token`. Errors: 401 bad token, 422 bad request (or Ollama
refused the options), 502 Ollama down, model missing or invalid JSON under a `schema` (that body is
`{"detail": {"message", "content"}}`), 504 Ollama timed out, 500 anything else.

## Admin page

`/admin/` (password `SERVICE_ADMIN_PASSWORD`): tokens with project scope, rate limit and expiry, usage numbers, CPU threads, the Settings card (update check hours, in-flight cap, answers kept, project prefix, model, load at start), and the Updates card (**Check now** and **Download new weights**). New weights are downloaded once into the shared models volume; the other replicas notice within a few seconds and load them on their next request. `/metrics` (admin password) is Prometheus text per token. See [the web UI guide](docs/guide-web-ui.md).

## Run

Local (Ollama installed): pull the model once, then start the service.

```bash
ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
run.bat
```

Docker, with a load balancer: `docker compose up -d --build`. `cnv_minicpm52b_model` runs `SERVICE_REPLICAS` API copies (default 1)
behind nginx (`cnv_minicpm52b_model_lb`, `least_conn`, one retry), all using one Ollama that holds the model: by default the Ollama already running on the host (`SERVICE_OLLAMA_URL=http://host.docker.internal:11434`; pull the model there once, and it must listen on `0.0.0.0`), or with `COMPOSE_PROFILES=bundled-ollama` an `cnv_minicpm52b_model_ollama` container in the stack, where
`cnv_minicpm52b_model_pull` downloads it on first start. Ollama runs `OLLAMA_NUM_PARALLEL` requests at once (default 2). `SERVICE_NUM_THREAD` caps the CPU threads Ollama uses per request (`/health` reports it; `/admin/` can change and save it; other replicas apply it on restart). After
changing the replica count, run `docker compose restart cnv_minicpm52b_model_lb`.

### Guides

- [Deployment reference](deployment.md): settings, first start, scaling, updates and troubleshooting

- [Docker on the same device](docs/guide-docker-same-device.md)
- [Docker on a deployment device](docs/guide-docker-deployment-device.md)
- [Without Docker on the same device](docs/guide-no-docker-same-device.md)
- [Admin web UI](docs/guide-web-ui.md): tokens and CPU threads at `/admin/`; the same for every run option

## Settings

`SERVICE_HOST` (127.0.0.1), `SERVICE_PORT` (29200), `SERVICE_ADMIN_PASSWORD` (unlocks `/admin/`; empty = no auth), `SERVICE_OLLAMA_MODEL`
(`hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M`; `:Q8_0` and `:F16` also exist), `SERVICE_OLLAMA_URL` (http://127.0.0.1:11434).
`SERVICE_PROJECT_PREFIX` defaults to `minicpm-` and prefixes project model names.

## Tests

`uv sync` then `uv run pytest`. The tests fake Ollama. A manual check against the real `Q4_K_M` model got correct tool calls for simple tools
(weather, add) and a plain refusal when no tool fit.

## Limits worth knowing

- One Ollama request per call; more than the in-flight cap (`max_inflight`) waiting calls get 429 with `Retry-After: 5`.
- The rate limit and the answer cache are per replica.
- Only `temperature` 0 answers are cached.
- `tools`, `schema` and `thinking` are supported here and refused by the other chat services.

## License

The service code is MIT. It does not ship model weights: Ollama downloads MiniCPM5-2B from
`openbmb/MiniCPM5-2B-GGUF`, which has its own license; read it before redistributing the weights. See [LICENSE](LICENSE) and [CHANGELOG.md](CHANGELOG.md).

### Releasing

A plain publish (`run.bat`, Deploy, option 1) only updates the `live` branch, so it is fine for test deploys. A release is a separate step: set `VERSION` (and `pyproject.toml` where it has a version, to the same number), commit, then run `run.bat`, Deploy, option R or `py tools\publish_live.py --release`. That publishes to `live` and tags the `main` commit `vX.Y.Z` and the live commit `live-vX.Y.Z`. A version already released with different live files is refused, so bump `VERSION`. While a version is not officially released yet, add `--replace` to move its tags.

`run.bat`, Build the release ZIP, then builds `_zip_versions\CNV_MiniCPM52B_Model-<VERSION>.zip` (no `tools/`, no git-ignored files, no older ZIPs; `.env.example` is included) and opens GitHub's new-release page for the tag.

## Try it from the admin page

Open `/admin/` and use **Try it** to call the public API with a service token held only in memory. Runs use normal token scopes, limits and caches; the panel shows HTTP status, latency, responses, cancellation and a curl equivalent with a token placeholder. See [the web UI guide](docs/guide-web-ui.md#try-it-send-a-real-request).

Chat sends messages to `POST /v1/chat`, with optional project and generation controls. Streaming appends NDJSON events as they arrive. MiniCPM also accepts thinking, tools or a JSON schema; tools and schema are mutually exclusive.

## Documentation

- [Purpose: input, output and what it is for](docs/purpose.md)
