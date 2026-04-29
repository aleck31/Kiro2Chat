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

CLAIM_TTL = 15 * 60  # 15 minutes


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


def consume_claim(section: str, token: str, user_id) -> str:
    """Validate `token` for `section` and append `user_id` to that section's
    allowed_user_ids.

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
    # Preserve element type: ints for TG/Discord, strings for Lark.
    items = list(existing)
    coerced = user_id
    if all(isinstance(x, int) for x in items) and str(user_id).lstrip("-").isdigit():
        coerced = int(user_id)
    if coerced not in items:
        items.append(coerced)
    sec["allowed_user_ids"] = items
    save_config_file(cfg)
    p.unlink(missing_ok=True)
    cfg_mod.reload()
    return "ok"


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
