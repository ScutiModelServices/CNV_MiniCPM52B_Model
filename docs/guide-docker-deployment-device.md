# CNV_MiniCPM52B_Model: Docker on a deployment device (ZimaOS server)

Run the service on a separate always-on box (default `lex@scutiserver1.local`, folder
`/media/ZimaOS-HD/Apps/CNV_MiniCPM52B_Model`). It is a compose stack (`cnv_minicpm52b_model`, `cnv_minicpm52b_model_lb`) that calls the Ollama on that box.

## Requirements on the box

- Docker + Compose v2, ssh access as a normal user (root over ssh is refused).
- Ollama on the box, listening on `0.0.0.0`, with the model pulled: `ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M`.

## 1. Publish the code (from your PC)

Run `CNV_MiniCPM52B_Model\run.bat` and choose **Deploy** (or `run.bat deploy`):

| Option | Does |
|---|---|
| 1 | Publish code to the `live` branch |
| 2 | Copy your `.env` to the server (one ssh/tar session) |
| 3 | Both |
| 4, 5 | Dry runs |
| 6 | chown helper if option 2 says `Permission denied` |

Keep your own target in the git-ignored `tools\deploy.local.ini` (copy `tools\deploy.local.example.ini`), or override it with `DEPLOY_HOST`, `DEPLOY_DIR`, `DEPLOY_WEB_PORT`. Commit `main` first: publish reads committed
`main`. The `live` branch holds only Dockerfile, compose, nginx.conf, pyproject, src, web and README.

## 2. First-time setup on the box

```bash
mkdir -p /media/ZimaOS-HD/Apps
git clone -b live <your-repo-url> /media/ZimaOS-HD/Apps/CNV_MiniCPM52B_Model
cd /media/ZimaOS-HD/Apps/CNV_MiniCPM52B_Model
```
`.env` (deploy option 2 copies yours, or `cp .env.example .env`):
- `SERVICE_ADMIN_PASSWORD`: set a real one.
- `SERVICE_NUM_THREAD`: the real core count of the box.
- `SERVICE_REPLICAS=1`, `OLLAMA_NUM_PARALLEL=2` are fine to start.

## 3. Start

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:29200/health
```

## 4. Let callers reach it

`SERVICE_PORT` (29200) is published on all interfaces, so other devices use `http://<box-ip>:29200`.
1. Open `http://<box-ip>:29200/admin/`, log in, create one token per calling project.
2. Callers send it as `X-Internal-Token`.
3. A stack on the same box uses `http://host.docker.internal:29200`, or joins the `cnv_minicpm52b_model` network from its own
   compose file (`external: true`) and calls `http://cnv_minicpm52b_model_lb:29200`. The service never references its callers.
4. Firewall the port if the LAN should not reach it, and never expose it to the internet.

## Registering a fine-tuned project model

Copy your fine-tuned GGUF to the deployment host as `file.gguf`, beside a `Modelfile` containing
`FROM ./file.gguf`. In the deployment folder on that host:

```bash
ollama create minicpm-my-project -f Modelfile
```

Call `/v1/chat` with `"project":"my-project"`. Names match `^[a-z0-9][a-z0-9_-]{0,47}$`; invalid names
return 422. `SERVICE_PROJECT_PREFIX` defaults to `minicpm-` when unset; change the create name if customizing it.
An absent project uses the base; an uninstalled project uses the base with `fallback: true`. Replies report
the selected `model`. `/health` adds `project_models`; `ready` still checks the base. Installed names are
cached for 30 seconds per API replica; a project not in the cache re-asks Ollama, so a new model is found at once.
If Ollama cannot list its models, the base is used with `fallback: true`.

CPU model switches can unload and reload weights. Set `OLLAMA_MAX_LOADED_MODELS=1` on the host Ollama;
raise it only if the deployment device has RAM for multiple resident models and contexts.

## Scaling

```powershell
$env:SERVICE_REPLICAS = "3"
docker compose up -d --build
docker compose restart cnv_minicpm52b_model_lb
```

nginx resolves replica addresses at start, so restart it after changing the count. `least_conn` balances API
requests; Ollama queues and schedules actual generations. Increasing API replicas does not create more model
copies or change Ollama's parallelism. Tune `OLLAMA_NUM_PARALLEL` on Ollama for that.

nginx allows 130-second responses and 2 MB bodies. It may retry connection errors, but never retries timeouts:
repeating a slow generation could run the same turn twice. Keep these settings distinct from Laya's.

## Updating

```bash
cd /media/ZimaOS-HD/Apps/CNV_MiniCPM52B_Model
git pull
docker compose up -d --build
docker compose restart cnv_minicpm52b_model_lb
```
The model and tokens volumes survive rebuilds.

To pull model weights manually, run this on the box (the host Ollama):

```bash
ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
```

All API replicas use that same Ollama. Model updates do not require per-replica API restarts.

## Releasing

A plain publish (`run.bat`, Deploy, option 1) updates only the `live` branch. For a release, set `VERSION` and
the version in `pyproject.toml` to the same number, commit, then run `run.bat`, Deploy, option R or
`uv run tools/publish_live.py --release`. This publishes `live` and tags the main commit `vX.Y.Z` and live commit
`live-vX.Y.Z`. Different live files under an existing release version are refused; bump `VERSION`. Before an
official release, `--replace` can move its tags.

`run.bat`, Build the release ZIP, builds `_zip_versions\CNV_MiniCPM52B_Model-<VERSION>.zip` and opens GitHub's new-release page.
The archive excludes development tools, ignored files and old ZIPs; its example configuration is included.

## Problems

| Symptom | Fix |
|---|---|
| Permission denied on push | run.bat Deploy option 6. |
| `.env` seems missing | Hidden dotfile: `ls -la`. |
| `/health` `ready: false` | Ollama down, not on `0.0.0.0`, or model not pulled: `ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M`. |
| Slow or 504 | Lower `OLLAMA_NUM_PARALLEL`, set `SERVICE_NUM_THREAD` to real cores. |
| nginx 502 after update or scaling | `docker compose restart cnv_minicpm52b_model_lb`. |
