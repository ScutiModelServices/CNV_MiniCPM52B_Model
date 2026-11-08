# Deploying CNV_MiniCPM52B_Model

CNV_MiniCPM52B_Model serves MiniCPM5-2B over HTTP on port 29200. FastAPI replicas (`cnv_minicpm52b_model`) sit behind nginx
(`cnv_minicpm52b_model_lb`), and all replicas call the Ollama on the host (`SERVICE_OLLAMA_URL`), which must have
`hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M` pulled. Callers use `http://<host>:29200`.

## Ways to run it

Each path has its own guide. Pick one.

| Path | Guide |
|---|---|
| Docker Desktop on this PC | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `run.bat` (Deploy) | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).
