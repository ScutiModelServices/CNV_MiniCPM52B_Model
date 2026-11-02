# MiniCPM52BModel

The one place MiniCPM5-2B is served. It sits in front of Ollama; projects send it turns over HTTP instead of
talking to Ollama or loading the model themselves. It is built as a tool-calling model: one call is one model turn,
and the caller runs the tools and the loop.

## API

| Call | Purpose |
|---|---|
| `POST /v1/chat` | `{"messages", "tools", "schema", "thinking", "temperature", "max_tokens"}` → `{"content", "thinking", "tool_calls", "finish_reason", "model", "usage", "duration_ms"}`. |
| `GET /health` | `{"status": "ok", "model", "ready"}`; `ready` is true when Ollama answers and has the model. No token needed. |

**Tool calling.** Send `tools` (`[{"name", "description", "parameters": <JSON schema>}]`). When the model wants a tool
it answers `finish_reason: "tool_calls"` with `tool_calls: [{"id", "name", "arguments"}]`. Run them, append the
assistant turn (`{"role": "assistant", "tool_calls": [...]}`) and one `{"role": "tool", "tool_name", "content"}` per
result to `messages`, and call again; repeat until `finish_reason` is `stop`. `tools` and `schema` cannot be sent
together.

**Structured output.** A JSON `schema` constrains the reply and also returns it as `parsed`.

**Thinking** is on by default (`"thinking": false` turns it off). The reasoning comes back in `thinking`, and it
counts against `max_tokens` (default 2048): `finish_reason: "length"` means the budget ran out, often with empty
`content`.

When `MINICPM_ADMIN_PASSWORD` is set, send a project token (made on `/admin/`) as `X-Internal-Token`. Errors: 401 bad token, 422 bad request (or Ollama
refused the options), 502 Ollama down, model missing or invalid JSON under a `schema` (that body is
`{"detail": {"message", "content"}}`), 504 Ollama timed out, 500 anything else.

## Run

Local (Ollama installed): pull the model once, then start the service.

```bash
ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
run.bat
```

Docker, with a load balancer: `docker compose up -d --build`. `minicpm` runs `MINICPM_REPLICAS` API copies (default 1)
behind nginx (`minicpm_lb`, `least_conn`, one retry), all using one `ollama` container that holds the model;
`minicpm_pull` downloads it on first start. Ollama runs `OLLAMA_NUM_PARALLEL` requests at once (default 2). `MINICPM_NUM_THREAD` caps the CPU threads Ollama uses per request (`/health` reports it; `/admin/` can change and save it; other replicas apply it on restart). After
changing the replica count, run `docker compose restart minicpm_lb`.

### Guides

- [Docker on the same device](docs/guide-docker-same-device.md)
- [Docker on a deployment device](docs/guide-docker-deployment-device.md)
- [Without Docker on the same device](docs/guide-no-docker-same-device.md)
- [Admin web UI](docs/guide-web-ui.md): tokens and CPU threads at `/admin/`; the same for every run option

## Settings

`MINICPM_HOST` (127.0.0.1), `MINICPM_PORT` (9130), `MINICPM_ADMIN_PASSWORD` (unlocks `/admin/`; empty = no auth), `MINICPM_MODEL`
(`hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M`; `:Q8_0` and `:F16` also exist), `OLLAMA_URL` (http://127.0.0.1:11434).

## Test

`uv sync` then `uv run pytest`. The tests fake Ollama. A manual check against the real `Q4_K_M` model got correct tool calls for simple tools
(weather, add) and a plain refusal when no tool fit.

## License

The service code is MIT. It does not ship model weights: Ollama downloads MiniCPM5-2B from
`openbmb/MiniCPM5-2B-GGUF`, which has its own license; read it before redistributing the weights. See [LICENSE](LICENSE) and [CHANGELOG.md](CHANGELOG.md).

### Releasing

A plain publish (`tools\deploy.bat` option 1) only updates the `live` branch, so it is fine for test deploys. A release is a separate step: set `VERSION` (and `pyproject.toml` where it has a version, to the same number), commit, then run `tools\deploy.bat` option R or `py tools\publish_live.py --release`. That publishes to `live` and tags the `main` commit `vX.Y.Z` and the live commit `live-vX.Y.Z`. A version already released with different live files is refused, so bump `VERSION`. While a version is not officially released yet, add `--replace` to move its tags.

`tools\make_zip.bat` then builds `zip_versions\MiniCPM52BModel-<VERSION>.zip` (no `tools/`, no git-ignored files, no older ZIPs; `.env.example` is included) and opens GitHub's new-release page for the tag.
