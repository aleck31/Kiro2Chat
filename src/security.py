"""Security helpers — claim-token based self-service authorization.

Claim tokens let a new user bind their platform user id (e.g. Telegram user id)
to the config.toml allowlist without the operator having to look up obscure
numeric ids. Operator clicks "Generate claim token" in the dashboard; the token
is shown once and persisted to `data_dir/<platform>_claim.json`. The user DMs
the bot `/claim <token>` and their id is appended to the allowlist.

Tokens are single-use and expire after CLAIM_TTL seconds.
"""

import json
import secrets
import time
from pathlib import Path

CLAIM_TTL = 15 * 60  # 15 minutes


def _claim_path(platform: str) -> Path:
    from .config import config
    return config.data_dir / f"{platform}_claim.json"


def create_claim(platform: str) -> tuple[str, int]:
    """Generate a fresh claim token for `platform` and persist it.

    Returns (token, expires_at_epoch).
    """
    token = secrets.token_urlsafe(6)  # ~8 url-safe chars
    expires_at = int(time.time()) + CLAIM_TTL
    p = _claim_path(platform)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": token, "expires_at": expires_at}))
    return token, expires_at


def consume_claim(platform: str, token: str, user_id: int) -> str:
    """Validate `token` for `platform` and append `user_id` to the allowlist.

    Returns a short status string for user-facing reply:
      - "ok"       : authorized, id appended
      - "expired"  : token existed but expired (file removed)
      - "mismatch" : token doesn't match (file kept)
      - "missing"  : no active claim token
    """
    p = _claim_path(platform)
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

    # Consume — append id to allowlist, delete token file.
    from .config_manager import load_config_file, save_config_file
    from . import config as cfg_mod

    cfg = load_config_file()
    key = f"{platform}_allowed_user_ids"
    existing = cfg.get(key) or []
    if isinstance(existing, str):
        existing = [int(x.strip()) for x in existing.split(",") if x.strip().isdigit()]
    ids = {int(x) for x in existing}
    ids.add(int(user_id))
    cfg[key] = sorted(ids)
    save_config_file(cfg)
    p.unlink(missing_ok=True)
    cfg_mod.reload()
    return "ok"


def active_claim(platform: str) -> dict | None:
    """Return {'token': ..., 'expires_at': ...} if a live (unexpired) token exists."""
    p = _claim_path(platform)
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
