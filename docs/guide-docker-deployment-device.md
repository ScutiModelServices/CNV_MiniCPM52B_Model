# MiniCPM52BModel: Docker on a deployment device (ZimaOS server)

Run the service on a separate always-on box (default `user@your-server.local`, folder
`/media/ZimaOS-HD/Apps/MiniCPM52BModel`). It is a self-contained compose stack (`ollama`, `minicpm_pull`, `minicpm`,
`minicpm_lb`) with no Citadel dependency.

## Requirements on the box
- Docker + Compose v2, ssh access as a normal user (root over ssh is refused).
- Internet on the first start (model pull, about 1 GB). Disk for the `ollama` volume.

## 1. Publish the code (from your PC)
Run `MiniCPM52BModel\tools\deploy.bat`:

| Option | Does |
|---|---|
| 1 | Publish code to the `live` branch |
| 2 | Copy your `.env` to the server (one ssh/tar session) |
| 3 | Both |
| 4, 5 | Dry runs |
| 6 | chown helper if option 2 says `Permission denied` |

Keep your own target in the git-ignored `tools\deploy.local.bat` (copy `tools\deploy.local.example.bat`), or override it with `DEPLOY_HOST`, `DEPLOY_DIR`, `DEPLOY_WEB_PORT`. Commit `main` first: publish reads committed
`main`. The `live` branch holds only Dockerfile, compose, nginx.conf, pyproject, src, web and README.

## 2. First-time setup on the box
```bash
mkdir -p /media/ZimaOS-HD/Apps
git clone -b live <your-repo-url> /media/ZimaOS-HD/Apps/MiniCPM52BModel
cd /media/ZimaOS-HD/Apps/MiniCPM52BModel
```
`.env` (deploy option 2 copies yours, or `cp .env.example .env`):
- `MINICPM_ADMIN_PASSWORD`: set a real one.
- `MINICPM_NUM_THREAD`: the real core count of the box.
- `MINICPM_REPLICAS=1`, `OLLAMA_NUM_PARALLEL=2` are fine to start.

## 3. Start
```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:9130/health
```
`minicpm_pull` ends as `Exited (0)` after downloading the model.

## 4. Let callers reach it
`MINICPM_PORT` (9130) is published on all interfaces, so other devices use `http://<box-ip>:9130`.
1. Open `http://<box-ip>:9130/admin/`, log in, create one token per calling project.
2. Callers send it as `X-Internal-Token`.
3. A stack on the same box uses `http://host.docker.internal:9130`, or joins the `minicpm` network from its own
   compose file (`external: true`) and calls `http://minicpm_lb:9130`. The service never references its callers.
4. Firewall the port if the LAN should not reach it, and never expose it to the internet.

## Registering a fine-tuned project model

Copy your fine-tuned GGUF to the deployment host as `file.gguf`, beside a `Modelfile` containing
`FROM ./file.gguf`. In the deployment folder on that host:

```bash
docker compose cp file.gguf ollama:/tmp/file.gguf
docker compose cp Modelfile ollama:/tmp/Modelfile
docker compose exec -w /tmp ollama ollama create minicpm-my-project -f Modelfile
```

Call `/v1/chat` with `"project":"my-project"`. Names match `^[a-z0-9][a-z0-9_-]{0,47}$`; invalid names
return 422. `MINICPM_PROJECT_PREFIX` defaults to `minicpm-` when unset; change the create name if customizing it.
An absent project uses the base; an uninstalled project uses the base with `fallback: true`. Replies report
the selected `model`. `/health` adds `project_models`; `ready` still checks the base. Installed names are
cached for 30 seconds per API replica; a project not in the cache re-asks Ollama, so a new model is found at once.
If Ollama cannot list its models, the base is used with `fallback: true`.

CPU model switches can unload and reload weights. `OLLAMA_MAX_LOADED_MODELS=1` is the Compose default;
raise it on Ollama only if the deployment device has RAM for multiple resident models and contexts.

## Updating

```bash
cd /media/ZimaOS-HD/Apps/MiniCPM52BModel
git pull
docker compose up -d --build
docker compose restart minicpm_lb
```
The model and tokens volumes survive rebuilds.

## Problems
| Symptom | Fix |
|---|---|
| Permission denied on push | deploy.bat option 6. |
| `.env` seems missing | Hidden dotfile: `ls -la`. |
| `/health` `ready: false` | Model not pulled: `docker compose logs minicpm_pull`, then `docker compose up -d`. |
| Slow or 504 | Lower `OLLAMA_NUM_PARALLEL`, set `MINICPM_NUM_THREAD` to real cores. |
| nginx 502 after update or scaling | `docker compose restart minicpm_lb`. |
