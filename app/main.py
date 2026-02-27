import asyncio
import os
import secrets
import subprocess
from urllib.parse import urlencode

import requests as http_requests
from fastapi import FastAPI, Request, Header, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import models
from app import log_buffer as _log_buffer
from app.auth import SESSION_SECRET, check_credentials, is_logged_in, require_login
from app.handlers import handle_payload
from app.models import clear_github_creds, load_github_creds, save_github_creds
from app.utils import verify_signature

app = FastAPI(docs_url=None, redoc_url=None)
_log_buffer.setup()   # capture app + uvicorn logs into the ring buffer
_initial_password = models.bootstrap_users()
if _initial_password:
    _banner = (
        "\n"
        "╔══════════════════════════════════════════════════╗\n"
        "║           DeployHook — FIRST RUN SETUP           ║\n"
        "╠══════════════════════════════════════════════════╣\n"
       f"║  Username : admin                                ║\n"
       f"║  Password : {_initial_password:<38}║\n"
        "╠══════════════════════════════════════════════════╣\n"
        "║  Change this in Settings → Users → Change Password  ║\n"
        "╚══════════════════════════════════════════════════╝\n"
    )
    print(_banner, flush=True)

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=86400)


# ────────────────────────────────────────────────────────────────────────────
# Dynamic webhook path middleware
# ────────────────────────────────────────────────────────────────────────────

class _DynamicWebhookMiddleware(BaseHTTPMiddleware):
    """Forward POST requests at the custom webhook path to the real /webhook handler."""
    async def dispatch(self, request: Request, call_next):
        ui = models.get_ui_context()
        custom = ui.get("webhook_path", "/webhook").rstrip("/") or "/webhook"
        if (
            request.method == "POST"
            and custom != "/webhook"
            and request.url.path.rstrip("/") == custom
        ):
            body = await request.body()
            sig  = request.headers.get("x-hub-signature-256")
            try:
                verify_signature(sig, body)
                await handle_payload(body)
                return JSONResponse({"status": "ok"})
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        return await call_next(request)


app.add_middleware(_DynamicWebhookMiddleware)

_base = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(_base, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_base, "templates"))

# Inject UI customisation as a Jinja2 global so every template gets it for free
def _refresh_ui_globals():
    templates.env.globals["ui"] = models.get_ui_context()

_refresh_ui_globals()

GITHUB_TOKEN         = os.getenv("GITHUB_TOKEN", os.getenv("GHCR_PAT", ""))


def _get_effective_token(request: Request) -> str:
    """Return the best available GitHub token: session > persisted > env."""
    session_token = request.session.get("github_token", "")
    if session_token:
        return session_token
    persisted = load_github_creds()
    if persisted.get("token"):
        return persisted["token"]
    return GITHUB_TOKEN


# ────────────────────────────────────────────────────────────────────────────
# Auth
# ────────────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request,
                        username: str = Form(...),
                        password: str = Form(...)):
    if check_credentials(username, password):
        request.session["authenticated"] = True
        request.session["username"] = username
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html",
                                      {"request": request, "error": "Invalid credentials"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ────────────────────────────────────────────────────────────────────────────
# GitHub OAuth — lets the user connect their GitHub account to browse repos
# ────────────────────────────────────────────────────────────────────────────

@app.get("/auth/github")
@require_login
async def github_oauth_start(request: Request):
    """Begin GitHub OAuth flow. Redirects to GitHub login/authorize."""
    client_id  = models.get_setting("GITHUB_CLIENT_ID", "")
    app_base   = models.get_setting("APP_BASE_URL", "http://localhost:3002").rstrip("/")
    if not client_id:
        return HTMLResponse(
            "<p>GITHUB_CLIENT_ID is not set. Configure it in Settings.</p>",
            status_code=500,
        )
    state = secrets.token_urlsafe(16)
    request.session["gh_oauth_state"] = state
    request.session["gh_oauth_next"] = request.query_params.get("next", "/repos/browse")

    params = urlencode({
        "client_id":    client_id,
        "redirect_uri": f"{app_base}/auth/github/callback",
        "scope":        "repo read:packages",
        "state":        state,
    })
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")


@app.get("/auth/github/callback")
@require_login
async def github_oauth_callback(request: Request, code: str = "", state: str = ""):
    """GitHub redirects here after the user authorises the app."""
    if state != request.session.pop("gh_oauth_state", None):
        return HTMLResponse("<p>OAuth state mismatch. Please try again.</p>", status_code=400)

    # Exchange code for access token
    app_base = models.get_setting("APP_BASE_URL", "http://localhost:3002").rstrip("/")
    r = http_requests.post(
        "https://github.com/login/oauth/access_token",
        data={
            "client_id":     models.get_setting("GITHUB_CLIENT_ID", ""),
            "client_secret": models.get_setting("GITHUB_CLIENT_SECRET", ""),
            "code":          code,
            "redirect_uri":  f"{app_base}/auth/github/callback",
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    if not r.ok:
        return HTMLResponse(f"<p>Token exchange failed: {r.status_code}</p>", status_code=502)

    token_data = r.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return HTMLResponse(f"<p>No token returned: {token_data}</p>", status_code=502)

    request.session["github_token"] = access_token
    next_url = request.session.pop("gh_oauth_next", "/repos/browse")

    # Fetch the authenticated user's login so we can use it for GHCR
    user_r = http_requests.get(
        "https://api.github.com/user",
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    username = user_r.json().get("login", "") if user_r.ok else ""
    if username:
        request.session["github_username"] = username
        save_github_creds(username, access_token)

    return RedirectResponse(next_url, status_code=302)


@app.get("/auth/github/disconnect")
@require_login
async def github_oauth_disconnect(request: Request):
    request.session.pop("github_token", None)
    request.session.pop("github_username", None)
    clear_github_creds()
    return RedirectResponse("/", status_code=302)


# ────────────────────────────────────────────────────────────────────────────
# GitHub Repo Browser
# ────────────────────────────────────────────────────────────────────────────

@app.get("/repos/browse", response_class=HTMLResponse)
@require_login
async def browse_repos(request: Request):
    """Show a GitHub repo browser. Requires OAuth token in session."""
    gh_token     = request.session.get("github_token") or GITHUB_TOKEN
    gh_connected = bool(request.session.get("github_token"))
    gh_oauth_ok  = bool(models.get_setting("GITHUB_CLIENT_ID", ""))
    gh_username  = request.session.get("github_username", "")
    # Pass already-tracked repo names so the browser can mark them
    repos_set    = [r["github_repo"] for r in models.list_repos()]
    return templates.TemplateResponse("browse.html", {
        "request":      request,
        "gh_connected": gh_connected,
        "gh_oauth_ok":  gh_oauth_ok,
        "gh_token":     gh_token,
        "gh_username":  gh_username,
        "repos_set":    repos_set,
    })


@app.get("/api/github/repos")
@require_login
async def api_github_repos(
    request: Request,
    q:    str = "",
    page: int = 1,
):
    """
    Return the authenticated user's repos via their OAuth session token only.
    Requires the user to have explicitly connected GitHub in this session.
    """
    token = request.session.get("github_token", "")
    if not token:
        return JSONResponse({"error": "GitHub not connected. Click 'Connect GitHub' to continue."}, status_code=401)

    headers = {
        "Accept":        "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
    }

    # Fetch the authenticated user's container packages first so we can filter
    pkg_names: set = set()
    pkg_resp = http_requests.get(
        "https://api.github.com/user/packages",
        headers=headers,
        params={"package_type": "container", "per_page": 100},
        timeout=10,
    )
    if pkg_resp.ok:
        pkg_names = {p["name"].lower() for p in pkg_resp.json()}

    if q:
        # Search within repos the user is affiliated with that also have a package
        affiliated_q = f"{q} user:@me"
        r = http_requests.get(
            "https://api.github.com/search/repositories",
            headers=headers,
            params={"q": affiliated_q, "per_page": 50, "page": page, "sort": "updated"},
            timeout=10,
        )
        if not r.ok:
            return JSONResponse({"error": f"GitHub API error {r.status_code}"}, status_code=502)
        items = r.json().get("items", [])
    else:
        # List the authenticated user's own repos
        r = http_requests.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={"per_page": 100, "page": page, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
            timeout=10,
        )
        if not r.ok:
            return JSONResponse({"error": f"GitHub API error {r.status_code}"}, status_code=502)
        items = r.json()

    # Only show repos that have a matching container package on GHCR
    if pkg_names:
        items = [repo for repo in items if repo["name"].lower() in pkg_names]

    return [
        {
            "full_name":       repo["full_name"],
            "description":     repo.get("description") or "",
            "language":        repo.get("language") or "",
            "private":         repo.get("private", False),
            "updated_at":      repo.get("updated_at", "")[:10],
            "container_name":  repo["name"].replace("-", "_").lower(),
            "image":           f"ghcr.io/{repo['full_name'].split('/')[0].lower()}/{repo['name'].lower()}:latest",
        }
        for repo in items
    ]

@app.get("/api/github/lookup")
@require_login
async def github_lookup(request: Request, repo: str = ""):
    """
    Calls the GitHub API to fetch repo metadata and the first matching
    container package under the same owner.

    Returns:
      { owner, repo_name, image, container_name, description, default_branch, packages }
    """
    if "/" not in repo:
        return JSONResponse({"error": "Expected owner/repo format"}, status_code=400)

    owner, repo_name = repo.split("/", 1)
    token = _get_effective_token(request)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Fetch repo metadata
    r = http_requests.get(
        f"https://api.github.com/repos/{owner}/{repo_name}",
        headers=headers, timeout=10,
    )
    if r.status_code == 404:
        return JSONResponse({"error": f"Repository '{repo}' not found"}, status_code=404)
    if not r.ok:
        return JSONResponse({"error": f"GitHub API error: {r.status_code}"}, status_code=502)

    meta = r.json()

    # Fetch container packages — try user endpoint first, then org endpoint
    packages = []
    matched_image = f"ghcr.io/{owner.lower()}/{repo_name.lower()}:latest"  # sensible default

    for pkg_url in [
        f"https://api.github.com/users/{owner}/packages",
        f"https://api.github.com/orgs/{owner}/packages",
    ]:
        pkg_resp = http_requests.get(
            pkg_url,
            headers=headers,
            params={"package_type": "container"},
            timeout=10,
        )
        if pkg_resp.ok:
            for pkg in pkg_resp.json():
                pkg_name = pkg.get("name", "")
                entry = f"ghcr.io/{owner.lower()}/{pkg_name.lower()}:latest"
                if entry not in packages:
                    packages.append(entry)
                if pkg_name.lower() == repo_name.lower():
                    matched_image = entry
            break  # stop after first successful endpoint

    # Derive a safe container name: replace - with _
    container_name = repo_name.replace("-", "_").replace("/", "_").lower()

    return {
        "owner":          owner,
        "repo_name":      repo_name,
        "image":          matched_image,
        "container_name": container_name,
        "description":    meta.get("description") or "",
        "default_branch": meta.get("default_branch", "main"),
        "packages":       packages,
    }


# ────────────────────────────────────────────────────────────────────────────
# Dashboard
# ────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@require_login
async def dashboard(request: Request):
    repos        = models.list_repos()
    gh_connected = bool(request.session.get("github_token"))
    gh_username  = request.session.get("github_username", "")
    gh_oauth_ok  = bool(models.get_setting("GITHUB_CLIENT_ID", ""))
    ui           = models.get_ui_context()
    base_url     = models.get_setting("APP_BASE_URL", "").rstrip("/")
    if not base_url:
        base_url = str(request.base_url).rstrip("/")
    webhook_url  = base_url + ui["webhook_path"]
    return templates.TemplateResponse("dashboard.html", {
        "request":      request,
        "repos":        repos,
        "gh_connected": gh_connected,
        "gh_username":  gh_username,
        "gh_oauth_ok":  gh_oauth_ok,
        "webhook_url":  webhook_url,
    })


# ────────────────────────────────────────────────────────────────────────────
# Add repo
# ────────────────────────────────────────────────────────────────────────────

@app.get("/repos/new", response_class=HTMLResponse)
@require_login
async def new_repo_form(request: Request):
    # ?repo=org/name  — passed from the browser to trigger JS autofill
    prefill = request.query_params.get("repo", "")
    return templates.TemplateResponse("repo_form.html", {
        "request": request, "repo": None, "error": None, "prefill": prefill,
    })


@app.post("/repos/new", response_class=HTMLResponse)
@require_login
async def new_repo_submit(
    request:        Request,
    github_repo:    str = Form(...),
    container_name: str = Form(...),
    image:          str = Form(...),
    ports:          str = Form(""),
    volumes:        str = Form(""),
    extra_flags:    str = Form(""),
    env_content:    str = Form(""),
    branch:         str = Form(...),
):
    if not branch.strip():
        return templates.TemplateResponse("repo_form.html", {
            "request": request, "repo": None, "error": "Branch is required.",
        })
    try:
        models.save_repo(
            github_repo=github_repo,
            container_name=container_name,
            image=image,
            ports=ports,
            volumes=volumes,
            extra_flags=extra_flags,
            env_content=env_content,
            branch=branch,
        )
        return RedirectResponse("/", status_code=302)
    except Exception as e:
        return templates.TemplateResponse("repo_form.html", {
            "request": request, "repo": None, "error": str(e),
        })


# ────────────────────────────────────────────────────────────────────────────
# Edit repo
# ────────────────────────────────────────────────────────────────────────────

@app.get("/repos/{repo_id}/edit", response_class=HTMLResponse)
@require_login
async def edit_repo_form(request: Request, repo_id: str):
    repo = models.get_repo(repo_id)
    if repo is None:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("repo_form.html",
                                      {"request": request, "repo": repo, "error": None})


@app.post("/repos/{repo_id}/edit", response_class=HTMLResponse)
@require_login
async def edit_repo_submit(
    request:        Request,
    repo_id:        str,
    github_repo:    str = Form(...),
    container_name: str = Form(...),
    image:          str = Form(...),
    ports:          str = Form(""),
    volumes:        str = Form(""),
    extra_flags:    str = Form(""),
    env_content:    str = Form(""),
    branch:         str = Form(...),
):
    if not branch.strip():
        repo = models.get_repo(repo_id) or {}
        return templates.TemplateResponse("repo_form.html", {
            "request": request, "repo": repo, "error": "Branch is required.",
        })
    try:
        models.save_repo(
            repo_id=repo_id,
            github_repo=github_repo,
            container_name=container_name,
            image=image,
            ports=ports,
            volumes=volumes,
            extra_flags=extra_flags,
            # Empty string → keep existing .env file untouched
            env_content=env_content,
            branch=branch,
        )
        return RedirectResponse("/", status_code=302)
    except Exception as e:
        repo = models.get_repo(repo_id) or {}
        return templates.TemplateResponse("repo_form.html", {
            "request": request, "repo": repo, "error": str(e),
        })


# ────────────────────────────────────────────────────────────────────────────
# Delete repo
# ────────────────────────────────────────────────────────────────────────────

@app.post("/repos/{repo_id}/delete")
@require_login
async def delete_repo(request: Request, repo_id: str):
    models.delete_repo(repo_id)
    return RedirectResponse("/", status_code=302)


# ────────────────────────────────────────────────────────────────────────────
# Manual container controls
# ────────────────────────────────────────────────────────────────────────────

@app.post("/repos/{repo_id}/deploy")
@require_login
async def manual_deploy(request: Request, repo_id: str):
    """Pull the latest image and relaunch the container."""
    from app.handlers import deploy, notify_deploy_fail
    repo = models.get_repo(repo_id)
    if repo is None:
        return RedirectResponse("/", status_code=302)

    def _run():
        try:
            deploy(repo)
        except Exception as e:
            notify_deploy_fail(repo["github_repo"], repo.get("container_name", "?"), str(e))

    asyncio.create_task(asyncio.to_thread(_run))
    return RedirectResponse(f"/repos/{repo_id}/logs", status_code=302)


@app.post("/repos/{repo_id}/restart")
@require_login
async def manual_restart(request: Request, repo_id: str):
    """Restart the running container (no image pull)."""
    repo = models.get_repo(repo_id)
    if repo is None:
        return RedirectResponse("/", status_code=302)
    subprocess.run(["docker", "restart", repo["container_name"]], capture_output=True)
    return RedirectResponse("/", status_code=302)


@app.post("/repos/{repo_id}/stop")
@require_login
async def manual_stop(request: Request, repo_id: str):
    """Stop and remove the container."""
    repo = models.get_repo(repo_id)
    if repo is None:
        return RedirectResponse("/", status_code=302)
    container = repo["container_name"]
    subprocess.run(["docker", "stop", container], capture_output=True)
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    return RedirectResponse("/", status_code=302)


# ────────────────────────────────────────────────────────────────────────────
# Container Logs
# ────────────────────────────────────────────────────────────────────────────

@app.get("/repos/{repo_id}/logs", response_class=HTMLResponse)
@require_login
async def repo_logs_page(request: Request, repo_id: str):
    repo = models.get_repo(repo_id)
    if repo is None:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("logs.html", {"request": request, "repo": repo})


@app.get("/api/repos/{repo_id}/logs")
@require_login
async def api_repo_logs(request: Request, repo_id: str, tail: int = 300):
    repo = models.get_repo(repo_id)
    if repo is None:
        return JSONResponse({"error": "Repo not found"}, status_code=404)

    container = repo["container_name"]
    try:
        r = subprocess.run(
            ["docker", "logs", "--timestamps", "--tail", str(tail), container],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10,
        )
        # stderr merged into stdout at OS level so timestamps are in order
        lines = r.stdout.splitlines()
        # Detect if container exists at all
        if r.returncode != 0 and not lines:
            return JSONResponse({
                "container": container,
                "running":   False,
                "lines":     [f"[no output — container '{container}' may not exist yet]"],
            })
        # Check running state
        ps = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{container}$", "--format", "{{.Names}}"],
            capture_output=True, text=True,
        )
        running = container in ps.stdout
        return JSONResponse({"container": container, "running": running, "lines": lines})
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "docker logs timed out"}, status_code=504)
    except FileNotFoundError:
        return JSONResponse({"error": "docker not found on this host"}, status_code=500)


# ────────────────────────────────────────────────────────────────────────────
# System / Application Logs
# ────────────────────────────────────────────────────────────────────────────

@app.get("/system/logs", response_class=HTMLResponse)
@require_login
async def system_logs_page(request: Request):
    return templates.TemplateResponse("system_logs.html", {"request": request})


@app.get("/api/system/logs")
@require_login
async def api_system_logs(request: Request):
    return JSONResponse({"lines": _log_buffer.get_lines()})


# ────────────────────────────────────────────────────────────────────────────
# Settings
# ────────────────────────────────────────────────────────────────────────────

def _mask(value: str) -> str:
    """Return a redacted version: first 4 chars visible, rest replaced with bullets."""
    if not value:
        return ""
    visible = value[:4]
    return visible + "●" * min(len(value) - 4, 24)


@app.get("/settings", response_class=HTMLResponse)
@require_login
async def settings_page(request: Request, error: str = "", success: str = "", tab: str = "credentials"):
    creds = {
        "GHCR_USERNAME":          models.get_setting("GHCR_USERNAME", ""),
        "GHCR_PAT":               _mask(models.get_setting("GHCR_PAT", "")),
        "GITHUB_WEBHOOK_SECRET":  _mask(models.get_setting("GITHUB_WEBHOOK_SECRET", "")),
        "GITHUB_CLIENT_ID":       models.get_setting("GITHUB_CLIENT_ID", ""),
        "GITHUB_CLIENT_SECRET":   _mask(models.get_setting("GITHUB_CLIENT_SECRET", "")),
        "APP_BASE_URL":           models.get_setting("APP_BASE_URL", ""),
        "DISCORD_WEBHOOK_URL":    _mask(models.get_setting("DISCORD_WEBHOOK_URL", "")),
    }
    overridden = set(models.load_app_settings().keys())
    return templates.TemplateResponse("settings.html", {
        "request":      request,
        "creds":        creds,
        "overridden":   overridden,
        "users":        models.list_users(),
        "current_user": request.session.get("username", ""),
        "active_tab":   tab,
        "error":        error,
        "success":      success,
    })


@app.post("/settings/update", response_class=HTMLResponse)
@require_login
async def settings_update(
    request: Request,
    key:     str = Form(...),
    value:   str = Form(""),
):
    allowed = {
        "GHCR_USERNAME", "GHCR_PAT", "GITHUB_WEBHOOK_SECRET",
        "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
        "APP_BASE_URL", "DISCORD_WEBHOOK_URL",
    }
    if key not in allowed:
        return RedirectResponse(f"/settings?tab=credentials&error=Unknown+setting+key.", status_code=302)
    models.save_app_setting(key, value)
    msg = f"{key}+cleared." if not value.strip() else f"{key}+saved."
    return RedirectResponse(f"/settings?tab=credentials&success={msg}", status_code=302)


@app.post("/settings/users/add", response_class=HTMLResponse)
@require_login
async def settings_add_user(
    request:  Request,
    username: str = Form(...),
    password: str = Form(...),
):
    err = models.add_user(username, password)
    if err:
        return RedirectResponse(f"/settings?tab=users&error={err}", status_code=302)
    return RedirectResponse("/settings?tab=users&success=User+added.", status_code=302)


@app.post("/settings/users/{target}/delete", response_class=HTMLResponse)
@require_login
async def settings_delete_user(request: Request, target: str):
    current = request.session.get("username", "")
    if target == "admin":
        return RedirectResponse("/settings?tab=users&error=The+admin+account+cannot+be+deleted.", status_code=302)
    if target == current:
        return RedirectResponse("/settings?tab=users&error=You+cannot+delete+your+own+account.", status_code=302)
    if len(models.list_users()) <= 1:
        return RedirectResponse("/settings?tab=users&error=Cannot+delete+the+last+user.", status_code=302)
    models.delete_user(target)
    return RedirectResponse("/settings?tab=users&success=User+deleted.", status_code=302)


@app.post("/settings/users/{target}/password", response_class=HTMLResponse)
@require_login
async def settings_change_password(
    request:      Request,
    target:       str,
    new_password: str = Form(...),
    confirm:      str = Form(...),
):
    current = request.session.get("username", "")
    # Only allow changing your own password, unless you are admin
    if target != current and current != "admin":
        return RedirectResponse("/settings?tab=users&error=You+can+only+change+your+own+password.", status_code=302)
    if new_password != confirm:
        return RedirectResponse("/settings?tab=users&error=Passwords+do+not+match.", status_code=302)
    err = models.change_password(target, new_password)
    if err:
        return RedirectResponse(f"/settings?tab=users&error={err}", status_code=302)
    return RedirectResponse("/settings?tab=users&success=Password+changed.", status_code=302)


@app.post("/settings/customize", response_class=HTMLResponse)
@require_login
async def settings_customize(
    request:       Request,
    accent_color:  str = Form(""),
    surface_color: str = Form(""),
    panel_color:   str = Form(""),
):
    models.save_ui_config({
        "accent_color":  accent_color,
        "surface_color": surface_color,
        "panel_color":   panel_color,
    })
    _refresh_ui_globals()
    return RedirectResponse("/settings?tab=customization&success=Customization+saved.", status_code=302)


# ────────────────────────────────────────────────────────────────────────────
# GitHub Webhook
# ────────────────────────────────────────────────────────────────────────────

@app.post("/webhook")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(None),
):
    body = await request.body()
    verify_signature(x_hub_signature_256, body)
    await handle_payload(body)
    return {"status": "ok"}
