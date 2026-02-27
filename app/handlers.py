"""Container deploy logic triggered by GitHub webhook events."""
import logging
import os
import shlex
import subprocess
from datetime import datetime, timezone

import requests as http_requests

from app import models
from app.utils import parse_env_file

log = logging.getLogger("deployhook")

_COLOUR_OK   = 0x00a0f0   # electric blue
_COLOUR_FAIL = 0xf85149   # red
_COLOUR_INFO = 0x58a6ff   # blue


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def _discord_embed(embeds: list):
    """Post one or more embed objects to Discord."""
    url = models.get_setting("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    try:
        http_requests.post(
            url,
            json={"embeds": embeds},
            timeout=5,
        )
    except Exception as exc:
        log.warning("Discord send failed: %s", exc)


def notify_deploy_start(github_repo: str, container_name: str, image: str):
    log.info("Deploy started: %s → %s  [%s]", github_repo, container_name, image)
    _discord_embed([{
        "title": f"⏳ Deploying `{container_name}`",
        "color": _COLOUR_INFO,
        "fields": [
            {"name": "Repository",  "value": github_repo,    "inline": True},
            {"name": "Container",   "value": container_name, "inline": True},
            {"name": "Image",       "value": f"`{image}`",   "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }])


def notify_deploy_ok(github_repo: str, container_name: str, image: str):
    log.info("Deploy succeeded: %s → %s", github_repo, container_name)
    _discord_embed([{
        "title": f"✅ Deployed `{container_name}`",
        "color": _COLOUR_OK,
        "fields": [
            {"name": "Repository",  "value": github_repo,    "inline": True},
            {"name": "Container",   "value": container_name, "inline": True},
            {"name": "Image",       "value": f"`{image}`",   "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }])


def notify_deploy_fail(github_repo: str, container_name: str, error: str):
    log.error("Deploy failed: %s  error: %s", github_repo, error)
    _discord_embed([{
        "title": f"❌ Deploy Failed: `{container_name}`",
        "color": _COLOUR_FAIL,
        "fields": [
            {"name": "Repository", "value": github_repo,    "inline": True},
            {"name": "Container",  "value": container_name, "inline": True},
            {"name": "Error",      "value": f"```{error[:900]}```", "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }])


# Keep a plain helper for one-off messages
def notify(msg: str):
    log.info("%s", msg)
    if DISCORD_WEBHOOK_URL:
        try:
            http_requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def docker_login():
    creds    = models.load_github_creds()
    username = creds.get("username") or models.get_setting("GHCR_USERNAME", "")
    token    = creds.get("token")    or models.get_setting("GHCR_PAT", "")
    if not username or not token:
        raise RuntimeError(
            "No GHCR credentials available. Connect GitHub in the UI "
            "(Browse GitHub → connect), or set GHCR_USERNAME/GHCR_PAT in .env."
        )
    r = subprocess.run(
        ["docker", "login", "ghcr.io", "-u", username, "--password-stdin"],
        input=token, text=True, capture_output=True,
    )
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "no output").strip()
        raise RuntimeError(f"docker login failed: {detail}")


def container_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    return name in r.stdout


def prune_old_images(image_repo: str):
    try:
        r = subprocess.run(
            ["docker", "images", "--format", "{{.ID}} {{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, check=True,
        )
        ids = [
            line.split()[0]
            for line in r.stdout.strip().splitlines()
            if image_repo in line and not line.endswith(":latest")
        ]
        if ids:
            subprocess.run(["docker", "rmi", "-f"] + ids, check=True)
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True)
    except Exception as e:
        notify(f"Warning: prune failed — {e}")


# ---------------------------------------------------------------------------
# Core deploy
# ---------------------------------------------------------------------------

def deploy(repo: dict, head_branch: str = ""):
    """Pull + re-launch a single container described by a repo dict."""
    repo_id        = repo["id"]
    github_repo    = repo["github_repo"]
    container_name = repo["container_name"]
    image          = repo["image"]
    # Substitute {branch} placeholder with the triggering branch name
    if head_branch and "{branch}" in image:
        image = image.replace("{branch}", head_branch)
    ports          = repo.get("ports", "")
    volumes        = repo.get("volumes", [])
    extra_flags    = repo.get("extra_flags", [])

    notify_deploy_start(github_repo, container_name, image)

    docker_login()

    pull = subprocess.run(
        ["docker", "pull", image],
        capture_output=True, text=True,
    )
    if pull.returncode != 0:
        detail = (pull.stderr or pull.stdout or "no output").strip()
        raise RuntimeError(f"docker pull failed: {detail}")

    if container_running(container_name):
        subprocess.run(["docker", "stop", container_name], capture_output=True)
        subprocess.run(["docker", "rm",   "-f", container_name], capture_output=True)

    cmd = ["docker", "run", "-d", "--name", container_name, "--restart", "unless-stopped"]

    if ports:
        cmd += ["-p", ports]

    for vol in volumes:
        cmd += ["-v", vol]

    # Inject any extra docker run flags (one flag expression per line)
    for flag_line in extra_flags:
        cmd += shlex.split(flag_line)

    # Inject plain env vars from the stored .env file for this repo
    env_content = models.read_env_content(repo_id)
    if env_content:
        for k, v in parse_env_file(env_content).items():
            cmd += ["-e", f"{k}={v}"]

    cmd.append(image)
    run_result = subprocess.run(cmd, capture_output=True, text=True)
    if run_result.returncode != 0:
        detail = (run_result.stderr or run_result.stdout or "no output").strip()
        raise RuntimeError(f"docker run failed: {detail}")

    models.touch_last_deployed(repo_id)
    prune_old_images(image.rsplit(":", 1)[0])
    notify_deploy_ok(github_repo, container_name, image)


# ---------------------------------------------------------------------------
# Webhook entry point
# ---------------------------------------------------------------------------

async def handle_payload(raw_body: bytes):
    import json
    if not raw_body.strip():
        return

    data       = json.loads(raw_body)
    action     = data.get("action", "")
    conclusion = data.get("workflow_run", {}).get("conclusion", "")
    full_name  = data.get("repository", {}).get("full_name", "")
    head_branch = data.get("workflow_run", {}).get("head_branch", "")

    if action != "completed" or conclusion != "success":
        return

    repo = models.get_repo_by_full_name(full_name)
    if repo is None:
        return

    # If the repo has a branch filter configured, skip other branches
    configured_branch = repo.get("branch", "").strip()
    if configured_branch and head_branch != configured_branch:
        log.debug(
            "Skipping deploy for %s: webhook branch '%s' != configured '%s'",
            full_name, head_branch, configured_branch,
        )
        return

    try:
        deploy(repo, head_branch=head_branch)
    except subprocess.CalledProcessError as e:
        notify_deploy_fail(full_name, repo.get("container_name", "?"), str(e))
    except Exception as e:
        notify_deploy_fail(full_name, repo.get("container_name", "?"), str(e))
        raise
