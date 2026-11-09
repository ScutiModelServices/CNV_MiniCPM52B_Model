# Deploying CNV_MiniCPM52B_Model

CNV_MiniCPM52B_Model serves MiniCPM5-2B over HTTP on port 29200. FastAPI replicas (`cnv_minicpm52b_model`) sit behind nginx
(`cnv_minicpm52b_model_lb`), and all replicas call the Ollama on the host (`SERVICE_OLLAMA_URL`), which stores
`hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M`; the admin Updates Download action pulls it. Callers use `http://<host>:29200`.

## Ways to run it

Each path has its own guide. Pick one. First start needs an explicit admin Updates Download; see the web UI guide below.

| Path | Guide |
|---|---|
| Docker Desktop on this PC | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `run.bat` (Deploy) | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).

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
