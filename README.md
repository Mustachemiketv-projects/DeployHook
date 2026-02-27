# DeployHook

A self-hosted webhook server that automatically redeploys Docker containers when a GitHub Actions workflow run completes successfully. Includes a web UI for managing repositories, browsing GitHub, viewing logs, and managing users.

---

## Features

- **Auto-deploy on push** — listens for `workflow_run` webhook events and pulls + relaunches the matching container
- **Manual controls** — deploy, restart, or kill any container directly from the dashboard
- **GitHub OAuth** — connect your GitHub account to browse repos that have a GHCR container package
- **Multi-user login** — create and manage multiple web UI accounts from Settings → Users
- **GHCR support** — authenticates to GitHub Container Registry (`ghcr.io`) for private image pulls
- **Discord notifications** — optional deploy start / success / failure embeds
- **Webhook signature verification** — HMAC-SHA256 validation on all incoming payloads
- **Live container logs** — tail and auto-refresh container output from the browser
- **In-app customization** — change accent color, background color, and panel color from Settings → Customization
- **Dynamic webhook path** — the webhook listener path is configurable at runtime, no restart needed

---

## Prerequisites

- Docker and Docker Compose installed on the host
- A server reachable from the internet (for GitHub to send webhooks)
- A GitHub account with container packages on GHCR for private image pulls

---

## Quick Start

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd Github-Script
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` — at minimum set `SESSION_SECRET`. Everything else can be configured from the Settings page after first login.

### 3. Start the server

```bash
docker compose up -d
```

The UI will be available at `http://your-server:3002`.

### 4. Get the admin password

On first start, a random admin password is generated and printed to the container logs:

```bash
docker logs webhook_server
```

Look for the banner:

```
╔══════════════════════════════════════════════════╗
║           DeployHook — FIRST RUN SETUP           ║
╠══════════════════════════════════════════════════╣
║  Username : admin                                ║
║  Password : xK9mP2rQvL8nJdF3                    ║
╠══════════════════════════════════════════════════╣
║  Save this password — it won't be shown again.   ║
║  Add permanent users in Settings → Users.        ║
╚══════════════════════════════════════════════════╝
```

This password is shown **once only**. Save it before dismissing. The `admin` account cannot be deleted. Add additional permanent accounts from **Settings → Users**.

---

## Configuration

All configuration is done via the `.env` file or through the **Settings → Credentials** page in the UI. The Settings page shows which values are set and provides step-by-step setup instructions for each.

### Session Secret

| Variable | Description |
|---|---|
| `SESSION_SECRET` | Random string used to sign session cookies. **Required. Change this.** Generate with `openssl rand -hex 32` |

---

### GitHub Container Registry (GHCR)

Required to pull private container images during deployment.

| Variable | Description |
|---|---|
| `GHCR_USERNAME` | Your GitHub username or organisation name |
| `GHCR_PAT` | A classic Personal Access Token with the **`read:packages`** scope |

**How to create the PAT:**
1. Go to **github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Set an expiry and tick **`read:packages`**
4. Copy the token — paste it in `.env` or in **Settings → Credentials → GHCR PAT**

---

### GitHub Webhook

| Variable | Description |
|---|---|
| `GITHUB_WEBHOOK_SECRET` | A shared secret that GitHub signs payloads with. Generate with `openssl rand -hex 32` |

**How to add the webhook on GitHub:**
1. In your GitHub repository go to **Settings → Webhooks → Add webhook**
2. Set **Payload URL** to `https://your-host/webhook` (or your custom webhook path)
3. Set **Content type** to `application/json`
4. Paste the same value as `GITHUB_WEBHOOK_SECRET` into the **Secret** field
5. Under **Which events**, choose **Let me select individual events** and tick **Workflow runs**
6. Save

---

### GitHub OAuth App (for Browse GitHub)

Optional. Enables the **Browse GitHub** repo picker in the UI. Only repos you have access to that also have a GHCR container package are shown.

| Variable | Description |
|---|---|
| `GITHUB_CLIENT_ID` | OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | OAuth App client secret |
| `APP_BASE_URL` | Public URL of this server (e.g. `https://deploy.example.com`) |

**How to create an OAuth App:**
1. Go to **github.com → Settings → Developer settings → OAuth Apps → New OAuth App**
2. Set **Homepage URL** to your `APP_BASE_URL`
3. Set **Authorization callback URL** to `https://your-host/auth/github/callback`
4. Copy the **Client ID** and paste it into `GITHUB_CLIENT_ID`
5. Click **Generate a new client secret** and copy it into `GITHUB_CLIENT_SECRET`

---

### Discord Notifications (optional)

| Variable | Description |
|---|---|
| `DISCORD_WEBHOOK_URL` | Discord webhook URL for deploy start/success/failure notifications |

**How to create a Discord webhook:**
1. Open your Discord server, go to the channel you want
2. Click the gear icon → **Integrations → Webhooks → New Webhook**
3. Copy the **Webhook URL** and paste it into `DISCORD_WEBHOOK_URL`

---

## Using the Web UI

### Dashboard

The main dashboard lists all configured repositories. Each repo card shows:
- Container name, GitHub repo, branch, and Docker image
- Port mappings and last deploy time
- `.env` file status (green **env loaded** badge if present)

**Action buttons per repo:**

| Button | What it does |
|---|---|
| **Logs** | Opens live container log viewer |
| **Edit** | Edit the repo configuration |
| **Deploy** | Pull latest image and relaunch the container |
| **Restart** | `docker restart` — restart without pulling a new image |
| **Kill** | `docker stop` + `docker rm` — stop and remove the container |
| **Delete** | Remove the repo from DeployHook (does not stop the container) |

---

### Adding a Repository

1. Click **Add Repo** (or **Browse GitHub** to pick from your connected account)
2. Fill in:
   - **GitHub Repo** — `owner/repo` format
   - **Branch** — required; only webhooks for this branch trigger a deploy
   - **Container Name** — the Docker container name to create
   - **Image** — full image reference, e.g. `ghcr.io/org/myapp:latest`
   - **Ports** — port mapping, e.g. `8080:80`
   - **Volumes** — one volume mount per line, e.g. `/host/path:/container/path`
   - **Extra Docker flags** — any additional `docker run` flags, one per line
   - **Environment file** — paste the contents of your `.env` file for this container
3. Save — the repo will now auto-deploy when a matching `workflow_run` webhook fires

---

### Browse GitHub

Click **Browse GitHub** to open a searchable list of your repositories. Requirements:
- You must click **Connect GitHub** to authorise via OAuth in the current session
- Only repos where you have access **and** that have a matching GHCR container package are shown

---

### Settings

Navigate to **Settings** (gear icon) to access three tabs:

#### Credentials
View and update all service credentials (GHCR, Webhook Secret, OAuth, Discord). Each entry shows whether the value is set and includes step-by-step instructions.

#### Customization
Change the appearance of the UI at runtime — no restart needed:
- **Accent Color** — buttons, tags, links, borders (default `#00a0f0`)
- **Background Color** — main page background (default `#0d1117`)
- **Panel / Card Color** — card and panel backgrounds (default `#161b22`)

Use the color pickers or type hex values directly. A live preview shows the accent color applied before saving. **Reset to defaults** reverts all three colors.

#### Users
- View all user accounts
- Add new users with a username and password
- Delete users (cannot delete `admin` or your own account)

---

## Branding

Logo and favicon files live in `app/static/`:

| File | Use |
|---|---|
| `app/static/Logo.png` | Nav bar + login page logo |
| `app/static/favicon.png` | Browser tab icon |

To swap branding, replace these files and rebuild the container:

```bash
docker compose up -d --build
```

The default color scheme is derived from the DeployHook logo:
- **Accent** `#00a0f0` — electric blue (primary logo color)
- **Background** `#0d1117` — near-black
- **Panel** `#161b22` — dark grey

All three can be changed live from **Settings → Customization** without rebuilding.

---

## Auto-Deploy Flow

```
GitHub → POST /webhook → verify HMAC-SHA256 signature
       → filter: action=completed, conclusion=success, branch=configured branch
       → match repository full_name to configured repos
       → docker login ghcr.io
       → docker pull <image>
       → docker stop + docker rm <container>
       → docker run -d --restart unless-stopped <flags> <image>
       → update last_deployed timestamp
       → prune dangling images
       → send Discord notification (if configured)
```

---

## Data Storage

All persistent data is stored under `data/` and `secrets/`, both mounted as Docker volumes.

| Path | Contents |
|---|---|
| `data/repos.json` | Repository configuration |
| `data/users.json` | Hashed user account credentials |
| `data/ui_config.json` | UI customization (colors) |
| `data/github_creds.json` | Cached GitHub OAuth token |
| `data/app_settings.json` | Credential overrides set via the UI |
| `data/app.log` | Rotating application log (2 MB max) |
| `secrets/<repo-id>.env` | Per-repo `.env` file (injected at deploy time) |

Passwords are hashed with **PBKDF2-SHA256** (100,000 iterations) with a unique random salt per user.

---

## Architecture

```
docker compose up
└── webhook_server (Python 3.11, FastAPI + uvicorn, port 8000)
    ├── /                → Dashboard (repo list + manual controls)
    ├── /repos/*         → Add / edit / delete repos
    ├── /webhook         → GitHub webhook receiver (path configurable)
    ├── /auth/*          → GitHub OAuth flow
    ├── /settings        → Credentials / Customization / Users
    ├── /system/logs     → Application log viewer
    └── /repos/*/logs    → Container log viewer
```

The container has access to the host Docker socket (`/var/run/docker.sock`) so it can manage sibling containers.

---

## Rebuilding After Code or Asset Changes

```bash
docker compose up -d --build
```

Changes made through the Settings UI (credentials, colors, users) take effect immediately — no restart needed.

---

## Security Notes

- Set a strong, random `SESSION_SECRET` — sessions are signed with this value
- Use HTTPS in front of this server (nginx / Caddy reverse proxy recommended)
- The `GITHUB_WEBHOOK_SECRET` prevents arbitrary POST requests from triggering deploys
- The GHCR PAT only needs `read:packages` — do not grant write access
- The Docker socket mount gives the app full Docker control on the host — only expose the UI to trusted users
- The `admin` account cannot be deleted as a safeguard against accidental lockout
