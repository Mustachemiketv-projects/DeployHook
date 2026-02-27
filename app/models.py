"""
Repo data model and persistence layer.
All data is stored in a SQLite database at /etc/deployhook/deployhook.db.
Plain .env content is stored in /etc/deployhook/.secrets/{repo_id}.env
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DB_DIR       = "/app/data"
DB_PATH      = "/app/data/deployhook.db"
SECRETS_PATH = "/app/data/.secrets"

# Legacy paths — used only for one-time migration
_OLD_DATA    = os.path.join(os.path.dirname(__file__), "..", "data")
_OLD_SECRETS = os.path.join(os.path.dirname(__file__), "..", "secrets")


# ---------------------------------------------------------------------------
# Database bootstrap + helpers
# ---------------------------------------------------------------------------

def _ensure_dirs():
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs(SECRETS_PATH, exist_ok=True)


@contextmanager
def _db():
    _ensure_dirs()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _init_db():
    """Create tables if they don't exist, then migrate legacy JSON data once."""
    with _db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS repos (
                id             TEXT PRIMARY KEY,
                github_repo    TEXT,
                container_name TEXT,
                image          TEXT,
                ports          TEXT DEFAULT '',
                volumes        TEXT DEFAULT '[]',
                extra_flags    TEXT DEFAULT '[]',
                branch         TEXT DEFAULT '',
                created_at     TEXT,
                last_deployed  TEXT
            );
            CREATE TABLE IF NOT EXISTS github_creds (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                hash     TEXT,
                salt     TEXT
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS ui_config (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
    _migrate_json()


def _migrate_json():
    """One-time import of legacy JSON files into the SQLite DB. No-ops if data exists."""
    with _db() as con:
        # --- repos.json ---
        repos_json = os.path.join(_OLD_DATA, "repos.json")
        if os.path.exists(repos_json):
            try:
                with open(repos_json) as f:
                    repos = json.load(f)
                for rid, r in repos.items():
                    exists = con.execute("SELECT 1 FROM repos WHERE id=?", (rid,)).fetchone()
                    if not exists:
                        con.execute(
                            "INSERT INTO repos VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                rid,
                                r.get("github_repo", ""),
                                r.get("container_name", ""),
                                r.get("image", ""),
                                r.get("ports", ""),
                                json.dumps(r.get("volumes", [])),
                                json.dumps(r.get("extra_flags", [])),
                                r.get("branch", ""),
                                r.get("created_at", ""),
                                r.get("last_deployed"),
                            ),
                        )
            except Exception:
                pass

        # --- github_creds.json ---
        creds_json = os.path.join(_OLD_DATA, "github_creds.json")
        if os.path.exists(creds_json):
            try:
                with open(creds_json) as f:
                    creds = json.load(f)
                for k, v in creds.items():
                    exists = con.execute("SELECT 1 FROM github_creds WHERE key=?", (k,)).fetchone()
                    if not exists:
                        con.execute("INSERT INTO github_creds VALUES (?,?)", (k, v))
            except Exception:
                pass

        # --- users.json ---
        users_json = os.path.join(_OLD_DATA, "users.json")
        if os.path.exists(users_json):
            try:
                with open(users_json) as f:
                    users = json.load(f)
                for uname, u in users.items():
                    exists = con.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone()
                    if not exists:
                        con.execute(
                            "INSERT INTO users VALUES (?,?,?)",
                            (uname, u.get("hash", ""), u.get("salt", "")),
                        )
            except Exception:
                pass

        # --- app_settings.json ---
        settings_json = os.path.join(_OLD_DATA, "app_settings.json")
        if os.path.exists(settings_json):
            try:
                with open(settings_json) as f:
                    settings = json.load(f)
                for k, v in settings.items():
                    exists = con.execute("SELECT 1 FROM app_settings WHERE key=?", (k,)).fetchone()
                    if not exists:
                        con.execute("INSERT INTO app_settings VALUES (?,?)", (k, str(v)))
            except Exception:
                pass

        # --- ui_config.json ---
        ui_json = os.path.join(_OLD_DATA, "ui_config.json")
        if os.path.exists(ui_json):
            try:
                with open(ui_json) as f:
                    ui = json.load(f)
                for k, v in ui.items():
                    exists = con.execute("SELECT 1 FROM ui_config WHERE key=?", (k,)).fetchone()
                    if not exists:
                        con.execute("INSERT INTO ui_config VALUES (?,?)", (k, str(v)))
            except Exception:
                pass

    # --- secrets/*.env ---
    old_sec = _OLD_SECRETS
    if os.path.isdir(old_sec):
        for fname in os.listdir(old_sec):
            if fname.endswith(".env"):
                src = os.path.join(old_sec, fname)
                dst = os.path.join(SECRETS_PATH, fname)
                if not os.path.exists(dst):
                    try:
                        import shutil
                        shutil.copy2(src, dst)
                    except Exception:
                        pass


def _env_path(repo_id: str) -> str:
    _ensure_dirs()
    return os.path.join(SECRETS_PATH, f"{repo_id}.env")


# ---------------------------------------------------------------------------
# Public API — Repos
# ---------------------------------------------------------------------------

def _row_to_repo(row) -> dict:
    d = dict(row)
    d["volumes"]     = json.loads(d.get("volumes", "[]"))
    d["extra_flags"] = json.loads(d.get("extra_flags", "[]"))
    d["has_env"]     = os.path.exists(_env_path(d["id"]))
    return d


def list_repos() -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM repos").fetchall()
    return [_row_to_repo(r) for r in rows]


def get_repo(repo_id: str) -> Optional[dict]:
    with _db() as con:
        row = con.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
    return _row_to_repo(row) if row else None


def get_repo_by_full_name(full_name: str) -> Optional[dict]:
    with _db() as con:
        row = con.execute("SELECT * FROM repos WHERE github_repo=?", (full_name,)).fetchone()
    return _row_to_repo(row) if row else None


def save_repo(
    *,
    repo_id:        Optional[str] = None,
    github_repo:    str,
    container_name: str,
    image:          str,
    ports:          str = "",
    volumes:        str = "",
    extra_flags:    str = "",
    env_content:    str = "",
    branch:         str = "",
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    vols  = json.dumps([v.strip() for v in volumes.splitlines()    if v.strip()])
    flags = json.dumps([f.strip() for f in extra_flags.splitlines() if f.strip()])

    if repo_id is None:
        repo_id    = str(uuid.uuid4())
        created_at = now
        last_dep   = None
    else:
        with _db() as con:
            existing = con.execute("SELECT created_at, last_deployed FROM repos WHERE id=?", (repo_id,)).fetchone()
        created_at = existing["created_at"] if existing else now
        last_dep   = existing["last_deployed"] if existing else None

    with _db() as con:
        con.execute(
            """INSERT INTO repos VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   github_repo=excluded.github_repo,
                   container_name=excluded.container_name,
                   image=excluded.image,
                   ports=excluded.ports,
                   volumes=excluded.volumes,
                   extra_flags=excluded.extra_flags,
                   branch=excluded.branch,
                   created_at=excluded.created_at,
                   last_deployed=excluded.last_deployed""",
            (repo_id, github_repo, container_name, image, ports, vols, flags, branch.strip(), created_at, last_dep),
        )

    if env_content.strip():
        with open(_env_path(repo_id), "w") as f:
            f.write(env_content.strip())

    return repo_id


def delete_repo(repo_id: str):
    with _db() as con:
        con.execute("DELETE FROM repos WHERE id=?", (repo_id,))
    ep = _env_path(repo_id)
    if os.path.exists(ep):
        os.remove(ep)


def read_env_content(repo_id: str) -> Optional[str]:
    ep = _env_path(repo_id)
    if not os.path.exists(ep):
        return None
    with open(ep) as f:
        return f.read()


def touch_last_deployed(repo_id: str):
    with _db() as con:
        con.execute(
            "UPDATE repos SET last_deployed=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), repo_id),
        )


# ---------------------------------------------------------------------------
# GitHub OAuth credentials
# ---------------------------------------------------------------------------

def save_github_creds(username: str, token: str):
    with _db() as con:
        con.execute("INSERT INTO github_creds VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("username", username))
        con.execute("INSERT INTO github_creds VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("token", token))


def load_github_creds() -> dict:
    with _db() as con:
        rows = con.execute("SELECT key, value FROM github_creds").fetchall()
    return {r["key"]: r["value"] for r in rows}


def clear_github_creds():
    with _db() as con:
        con.execute("DELETE FROM github_creds")


# ---------------------------------------------------------------------------
# App settings (UI-editable overrides for env vars)
# ---------------------------------------------------------------------------

def load_app_settings() -> dict:
    with _db() as con:
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def save_app_setting(key: str, value: str):
    """Save or clear a single setting. Empty value removes the override."""
    if value.strip():
        with _db() as con:
            con.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value.strip()))
    else:
        with _db() as con:
            con.execute("DELETE FROM app_settings WHERE key=?", (key,))


def get_setting(key: str, default: str = "") -> str:
    """Return the setting value: UI override first, then env var, then default."""
    data = load_app_settings()
    return data.get(key) or os.getenv(key, default)


# ---------------------------------------------------------------------------
# UI customisation (branding, colors, webhook path)
# ---------------------------------------------------------------------------

_UI_DEFAULTS: dict = {
    "app_title":     "DeployHook",
    "webhook_path":  "/webhook",
    "accent_color":  "#00a0f0",
    "surface_color": "#0d1117",
    "panel_color":   "#161b22",
}


def _hex_lighten(hex_color: str, factor: float = 0.13) -> str:
    """Return a lightened version of a hex color (mix toward white)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = min(255, int(r + (255 - r) * factor))
        g = min(255, int(g + (255 - g) * factor))
        b = min(255, int(b + (255 - b) * factor))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


def _hex_tint_bg(hex_color: str, surface: str = "#0d1117", t: float = 0.12) -> str:
    """Mix accent into dark surface to produce a subtle tinted background."""
    try:
        h1, h2 = hex_color.lstrip("#"), surface.lstrip("#")
        r1, g1, b1 = int(h1[0:2], 16), int(h1[2:4], 16), int(h1[4:6], 16)
        r2, g2, b2 = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        r = int(r1 * t + r2 * (1 - t))
        g = int(g1 * t + g2 * (1 - t))
        b = int(b1 * t + b2 * (1 - t))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#0d1e2d"


def load_ui_config() -> dict:
    with _db() as con:
        rows = con.execute("SELECT key, value FROM ui_config").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    return {**_UI_DEFAULTS, **stored}


def save_ui_config(updates: dict):
    current = load_ui_config()
    for k, v in updates.items():
        stripped = v.strip()
        current[k] = stripped if stripped else _UI_DEFAULTS.get(k, "")
    with _db() as con:
        for k, v in current.items():
            con.execute("INSERT INTO ui_config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))


def get_ui_context() -> dict:
    """Return the full UI context dict (config + derived colors) for templates."""
    cfg = load_ui_config()
    accent  = cfg["accent_color"]
    surface = cfg["surface_color"]
    cfg["hover_color"]  = _hex_lighten(accent, 0.13)
    cfg["accent_bg"]    = _hex_tint_bg(accent, surface, 0.12)
    # Normalize webhook_path
    wp = cfg["webhook_path"].strip()
    if not wp.startswith("/"):
        wp = "/" + wp
    cfg["webhook_path"] = wp
    # Include base URL so templates can show the full webhook URL
    base = get_setting("APP_BASE_URL", "").rstrip("/")
    cfg["base_url"] = base
    return cfg


# ---------------------------------------------------------------------------
# User accounts (multi-user login)
# ---------------------------------------------------------------------------


def _hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def bootstrap_users() -> Optional[str]:
    """Init DB, migrate legacy JSON data, create admin user on first run.

    Returns the generated password (printed once to logs), or None if
    at least one user already exists.
    """
    _init_db()
    with _db() as con:
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count > 0:
        return None
    password = secrets.token_urlsafe(16)
    salt = uuid.uuid4().hex
    with _db() as con:
        con.execute("INSERT INTO users VALUES (?,?,?)", ("admin", _hash_pw(password, salt), salt))
    return password


def change_password(username: str, new_password: str) -> Optional[str]:
    """Change password for an existing user. Returns an error string or None on success."""
    if not new_password or len(new_password) < 6:
        return "Password must be at least 6 characters."
    with _db() as con:
        row = con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return f"User '{username}' not found."
    salt = uuid.uuid4().hex
    with _db() as con:
        con.execute("UPDATE users SET hash=?, salt=? WHERE username=?", (_hash_pw(new_password, salt), salt, username))
    return None


def verify_user(username: str, password: str) -> bool:
    with _db() as con:
        row = con.execute("SELECT hash, salt FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return False
    return _hmac.compare_digest(_hash_pw(password, row["salt"]), row["hash"])


def list_users() -> list[str]:
    with _db() as con:
        rows = con.execute("SELECT username FROM users").fetchall()
    return [r["username"] for r in rows]


def add_user(username: str, password: str) -> Optional[str]:
    """Add a new user. Returns an error string or None on success."""
    username = username.strip()
    if not username or not password:
        return "Username and password are required."
    with _db() as con:
        existing = con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        return f"User '{username}' already exists."
    salt = uuid.uuid4().hex
    with _db() as con:
        con.execute("INSERT INTO users VALUES (?,?,?)", (username, _hash_pw(password, salt), salt))
    return None


def delete_user(username: str) -> bool:
    with _db() as con:
        cur = con.execute("DELETE FROM users WHERE username=?", (username,))
    return cur.rowcount > 0
