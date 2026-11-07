# Deploying MiniCPM52BModel

MiniCPM52BModel serves MiniCPM5-2B over HTTP on port 9130. FastAPI replicas (`minicpm`) sit behind nginx
(`minicpm_lb`), and all replicas use one `ollama` host. The one-shot `minicpm_pull` service pulls
`hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M` before the API starts. Callers use `http://<host>:9130`.

## Ways to run it

Each path has its own guide. Pick one.

| Path | Guide |
|---|---|
| Docker Desktop on this PC | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `tools\deploy.bat` | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).
