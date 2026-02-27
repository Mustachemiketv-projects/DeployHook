# DeployHook

A self-hosted webhook server that automatically redeploys your Docker containers whenever a GitHub Actions workflow run completes. Manage everything from a clean web UI — no CLI required after setup.

![License](https://img.shields.io/badge/license-Proprietary-red)
![Docker](https://img.shields.io/badge/docker-ready-blue)

---

## Bugs & Feature Requests

Found a bug or have a feature request? [Open an issue](https://github.com/Mustachemiketv-projects/DeployHook/issues) on GitHub.

---

## Features

- **Auto-deploy on push** — listens for `workflow_run` GitHub webhook events, pulls the latest image, and relaunches the matching container
- **Dashboard** — view all repos, trigger deploys, restart or kill containers, tail live logs
- **GitHub OAuth** — Browse GitHub to pick repos directly from your connected account
- **GHCR support** — authenticates to GitHub Container Registry for private image pulls
- **Discord notifications** — optional deploy start / success / failure embeds
- **Webhook signature verification** — HMAC-SHA256 validation on all incoming payloads
- **Multi-user accounts** — add and manage web UI users from Settings → Users
- **Live customization** — change accent color, background, and panel color at runtime with no restart
- **Configurable webhook path** — change the listener path from Settings without restarting
- **SQLite storage** — all data stored in a single file at `/app/data/deployhook.db`

---

## Quick Start

### Docker CLI

```bash
docker run -d \
  --name deployhook \
  --restart unless-stopped \
  -p 3002:8000 \
  -e SESSION_SECRET=$(openssl rand -hex 32) \
  -e DOCKER_GID=$(stat -c '%g' /var/run/docker.sock) \
  --group-add $(stat -c '%g' /var/run/docker.sock) \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v deployhook_data:/app/data \
  mustachemiketv/deployhook:latest
```

### Docker Compose

Create a `docker-compose.yml`:

```yaml
services:
  deployhook:
    image: mustachemiketv/deployhook:latest
    container_name: deployhook
    restart: unless-stopped
    ports:
      - "3002:8000"
    environment:
      - SESSION_SECRET=change-this-to-a-random-string
      - DOCKER_GID=988  # run: stat -c '%g' /var/run/docker.sock
    group_add:
      - "${DOCKER_GID:-988}"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - deployhook_data:/app/data

volumes:
  deployhook_data:
```

Then run:

```bash
docker compose up -d
```

### Portainer (Stack)

1. In Portainer go to **Stacks → Add stack**
2. Paste the compose above into the web editor
3. Under **Environment variables** set `SESSION_SECRET` to the output of `openssl rand -hex 32`
4. Click **Deploy the stack**

> Generate a strong session secret with: `openssl rand -hex 32`
> Find your docker socket GID with: `stat -c '%g' /var/run/docker.sock`

---

## First Login

On first start a random admin password is generated and printed to the container logs:

```bash
docker logs deployhook
```

Look for:

```
╔══════════════════════════════════════════════════╗
║           DeployHook — FIRST RUN SETUP           ║
╠══════════════════════════════════════════════════╣
║  Username : admin                                ║
║  Password : xK9mP2rQvL8nJdF3                    ║
╠══════════════════════════════════════════════════╣
║  Save this password — it won't be shown again.   ║
║  Change it in Settings → Users → Change Password ║
╚══════════════════════════════════════════════════╝
```

The UI is available at `http://your-server:3002`.

The `admin` account cannot be deleted. Add additional accounts from **Settings → Users**.

---

## Environment Variables

All variables are optional except `SESSION_SECRET`. Everything else can be set from the **Settings → Credentials** page in the UI after first login.

| Variable | Description |
|---|---|
| `SESSION_SECRET` | **Required.** Random string to sign session cookies. Generate with `openssl rand -hex 32` |
| `GITHUB_WEBHOOK_SECRET` | Shared secret for HMAC-SHA256 webhook signature verification |
| `GHCR_USERNAME` | GitHub username or org for GHCR image pulls |
| `GHCR_PAT` | Classic PAT with `read:packages` scope |
| `GITHUB_CLIENT_ID` | OAuth App client ID (for Browse GitHub) |
| `GITHUB_CLIENT_SECRET` | OAuth App client secret (for Browse GitHub) |
| `APP_BASE_URL` | Public URL of this server, e.g. `https://deploy.example.com` |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL for deploy notifications |

---

## Setting Up GitHub Webhooks

1. In your GitHub repo go to **Settings → Webhooks → Add webhook**
2. Set **Payload URL** to `https://your-host/webhook`
3. Set **Content type** to `application/json`
4. Paste your `GITHUB_WEBHOOK_SECRET` value into the **Secret** field
5. Under **Which events** choose **Let me select individual events** and tick **Workflow runs**
6. Click **Add webhook**

DeployHook will only trigger a deploy when the workflow run `conclusion` is `success` and the branch matches the one you configured for that repo.

---

## Adding Repositories

1. Click **Add Repo** on the dashboard (or **Browse GitHub** to pick from your account)
2. Fill in the fields:
   - **GitHub Repo** — `owner/repo` format
   - **Branch** — only webhooks for this branch will trigger a deploy
   - **Container Name** — the Docker container name to manage
   - **Image** — full image reference, e.g. `ghcr.io/org/myapp:latest`
   - **Ports** — e.g. `8080:80`
   - **Volumes** — one mount per line, e.g. `/host/path:/container/path`
   - **Extra Docker flags** — additional `docker run` flags, one per line
   - **Environment file** — paste your container's `.env` contents here (stored encrypted at rest)
3. Save — the repo now auto-deploys on matching `workflow_run` events

---

## GHCR Setup

To pull private images from GitHub Container Registry:

1. Go to **github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Generate a new token with the **`read:packages`** scope
3. Enter the token and your GitHub username in **Settings → Credentials → GHCR**

---

## GitHub OAuth (Browse GitHub)

Optional — enables the repo browser in the UI.

1. Go to **github.com → Settings → Developer settings → OAuth Apps → New OAuth App**
2. Set **Homepage URL** to your `APP_BASE_URL`
3. Set **Authorization callback URL** to `https://your-host/auth/github/callback`
4. Copy the **Client ID** and **Client Secret** into **Settings → Credentials**

---

## Discord Notifications

1. In Discord open the channel settings → **Integrations → Webhooks → New Webhook**
2. Copy the webhook URL and paste it into **Settings → Credentials → Discord Webhook URL**

Notifications fire on deploy start, success, and failure.

---

## Auto-Deploy Flow

```
GitHub → POST /webhook
  → verify HMAC-SHA256 signature
  → filter: action=completed, conclusion=success, branch=configured branch
  → match github_repo to a configured repo
  → docker login ghcr.io
  → docker pull <image>
  → docker stop + docker rm <container>
  → docker run -d --restart unless-stopped <flags> <image>
  → update last_deployed timestamp in DB
  → prune dangling images
  → send Discord notification (if configured)
```

---

## Data Storage

All persistent data is stored in a Docker named volume (`deployhook_data`) mounted at `/app/data` inside the container. The container runs as a non-root user (`deployhook`) and owns this directory.

| Path | Contents |
|---|---|
| `/app/data/deployhook.db` | SQLite database (repos, users, settings, credentials) |
| `/app/data/.secrets/<repo-id>.env` | Per-repo env files injected at deploy time |
| `/app/data/app.log` | Rotating application log (2 MB, 2 backups) |

To back up:

```bash
docker cp deployhook:/app/data /your/backup/location
```

Or access the volume directly:

```bash
docker run --rm -v deployhook_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/deployhook-backup.tar.gz -C /data .
```

---

## Architecture

```
deployhook (Python 3.13-alpine, FastAPI + uvicorn, port 8000 → host 3002)
├── /                  Dashboard — repo list + manual controls
├── /repos/*           Add / edit / delete repos
├── /webhook           GitHub webhook receiver (path configurable)
├── /auth/*            GitHub OAuth flow
├── /settings          Credentials / Customization / Users
├── /system/logs       Application log viewer
└── /repos/*/logs      Live container log viewer
```

The container mounts `/var/run/docker.sock` so it can manage sibling containers on the host.

---

## Reverse Proxy (Recommended)

Run DeployHook behind a TLS-terminating reverse proxy. Example Caddy config:

```
deploy.example.com {
    reverse_proxy localhost:3002
}
```

Or nginx:

```nginx
server {
    listen 443 ssl;
    server_name deploy.example.com;

    location / {
        proxy_pass http://localhost:3002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Security Notes

- **Always set a strong `SESSION_SECRET`** — sessions are signed with this value
- **Run behind HTTPS** — use a reverse proxy (Caddy, nginx, Traefik)
- `GITHUB_WEBHOOK_SECRET` prevents arbitrary POST requests from triggering deploys
- The GHCR PAT only needs `read:packages` — never grant write access
- The Docker socket mount gives full Docker control on the host — only give trusted users access to the UI
- The `admin` account cannot be deleted to prevent accidental lockout

---

## License

Copyright (c) 2026. All rights reserved. See [LICENSE](LICENSE).

