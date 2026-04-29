"""Security helpers — claim-token based self-service authorization.

Claim tokens let a new user bind their platform user id to the config.toml
allowlist without the operator having to look up obscure numeric ids.
Operator clicks "Generate claim token" in the dashboard; the token is shown
once and persisted to `data_dir/<section>_claim.json`. The user DMs the bot
`/claim <token>` and their id is appended to `[<section>].allowed_user_ids`.

`section` is the config.toml section name: "telegram" | "lark" | "discord".
TG/Discord ids are ints; Lark open_ids are strings — the union type is
preserved by `append_allowed_id`.

Tokens are single-use and expire after CLAIM_TTL seconds.
"""

import json
import secrets
import time
from pathlib import Path

# Shown once to unauthorized users, then silence.
# Deliberately vague — does not advertise the /claim flow to random scanners.
UNAUTHORIZED_HINT = "This bot is private. Contact the operator if you need access."
CLAIM_TTL = 15 * 60  # 15 minutes


def authorized_message(username: str = "", user_id=None) -> str:
    """Uniform /claim success reply. Prefers username, falls back to id."""
    who = (username or "").strip()
    if not who and user_id is not None:
        who = str(user_id)
    head = f"✅ Authorized as {who}." if who else "✅ Authorized."
    return f"{head}\nType /help to see available commands, or just send me a message."


def _claim_path(section: str) -> Path:
    from .config import config
    return config.data_dir / f"{section}_claim.json"


def create_claim(section: str) -> tuple[str, int]:
    """Generate a fresh claim token for `section` and persist it.

    Returns (token, expires_at_epoch).
    """
    token = secrets.token_urlsafe(6)
    expires_at = int(time.time()) + CLAIM_TTL
    p = _claim_path(section)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": token, "expires_at": expires_at}))
    return token, expires_at


def consume_claim(section: str, token: str, user_id, username: str = "") -> str:
    """Validate `token` for `section` and append `user_id` to that section's
    allowed_user_ids. Optional `username` is stashed under
    `allowed_users_meta.<id> = username` for UI display (not used for auth).

    Returns a short status string for user-facing reply:
      - "ok"       : authorized, id appended
      - "expired"  : token existed but expired (file removed)
      - "mismatch" : token doesn't match (file kept)
      - "missing"  : no active claim token
    """
    p = _claim_path(section)
    if not p.is_file():
        return "missing"
    try:
        data = json.loads(p.read_text())
    except Exception:
        p.unlink(missing_ok=True)
        return "missing"

    if int(data.get("expires_at", 0)) < time.time():
        p.unlink(missing_ok=True)
        return "expired"
    if data.get("token") != token:
        return "mismatch"

    from .config_manager import load_config_file, save_config_file
    from . import config as cfg_mod

    cfg = load_config_file()
    sec = cfg.setdefault(section, {})
    existing = sec.get("allowed_user_ids") or []
    if isinstance(existing, str):
        existing = [x.strip() for x in existing.split(",") if x.strip()]
    items = list(existing)
    coerced = user_id
    if all(isinstance(x, int) for x in items) and str(user_id).lstrip("-").isdigit():
        coerced = int(user_id)
    if coerced not in items:
        items.append(coerced)
    sec["allowed_user_ids"] = items
    if username:
        meta = sec.setdefault("allowed_users_meta", {})
        meta[str(coerced)] = username
    save_config_file(cfg)
    p.unlink(missing_ok=True)
    cfg_mod.reload()
    return "ok"


def revoke_user(section: str, user_id) -> bool:
    """Remove `user_id` from the section's allowlist + meta. Returns True if
    something was actually removed."""
    from .config_manager import load_config_file, save_config_file
    from . import config as cfg_mod

    cfg = load_config_file()
    sec = cfg.get(section) or {}
    ids = list(sec.get("allowed_user_ids") or [])
    # Match by string form so the caller doesn't have to worry about int/str.
    target = str(user_id)
    new_ids = [x for x in ids if str(x) != target]
    if len(new_ids) == len(ids):
        return False
    sec["allowed_user_ids"] = new_ids
    meta = sec.get("allowed_users_meta") or {}
    meta.pop(target, None)
    if meta:
        sec["allowed_users_meta"] = meta
    else:
        sec.pop("allowed_users_meta", None)
    cfg[section] = sec
    save_config_file(cfg)
    cfg_mod.reload()
    return True


def active_claim(section: str) -> dict | None:
    """Return {'token': ..., 'expires_at': ...} if a live (unexpired) token exists."""
    p = _claim_path(section)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    if int(data.get("expires_at", 0)) < time.time():
        p.unlink(missing_ok=True)
        return None
    return data
