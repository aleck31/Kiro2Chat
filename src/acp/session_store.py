"""Read-only access to kiro-cli's on-disk session store.

kiro-cli (v2+) stores all sessions as JSON files under ~/.kiro/sessions/cli/:
    {sid}.json   — metadata (cwd, title, created_at, updated_at, session_state)
    {sid}.jsonl  — conversation event stream
    {sid}.lock   — process lock (present while a process holds the session)
    {sid}.history— command history (interactive `kiro-cli chat` only)

There is NO separate index file — the `.json` `cwd` field is the sole cwd↔session mapping. 
`kiro-cli chat -l` scans these files filtered by cwd.

This module only READS the JSON files (fast, ~32ms for 150 files). 
Deletion is delegated to `kiro-cli chat --delete-session` so kiro-cli stays the single writer of its own store. 
SQLite (data.sqlite3) is never touched here — its session tables are classic/legacy and unused by v2.
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".kiro" / "sessions" / "cli"


@dataclass
class StoredSession:
    session_id: str
    cwd: str
    title: str
    updated_at: str          # ISO string from .json (may be "")
    mtime: float             # .json file mtime (reliable sort key)
    size_bytes: int          # .jsonl size (proxy for conversation length)
    locked: bool             # .lock present → held by a running process


@dataclass
class WorkspaceGroup:
    cwd: str
    sessions: list[StoredSession] = field(default_factory=list)
    in_config: bool = False          # cwd already a kiro2chat workspace
    config_name: str = ""            # the workspace name if in_config

    @property
    def latest_session_id(self) -> str:
        """Most recently updated session id in this cwd (for 'add as workspace')."""
        if not self.sessions:
            return ""
        return max(self.sessions, key=lambda s: s.mtime).session_id


def _is_locked(lock_path: Path) -> bool:
    """True only if the lock exists AND its PID is a live process.

    The .lock file records {"pid": ..., "started_at": ...}. A crashed/killed
    process leaves a stale lock behind, so we verify the PID is alive rather
    than trusting mere file existence (avoids false "in use" on dead sessions).
    """
    if not lock_path.exists():
        return False
    try:
        pid = json.loads(lock_path.read_text()).get("pid")
    except Exception:
        return False
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)   # signal 0 = existence check, doesn't actually signal
        return True
    except OSError:
        return False      # ProcessLookupError → stale lock


def _read_one(json_path: Path) -> StoredSession | None:
    sid = json_path.stem
    try:
        d = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug("[session_store] skip %s: %s", sid, e)
        return None

    jsonl = json_path.with_suffix(".jsonl")
    lock = json_path.with_suffix(".lock")
    try:
        size = jsonl.stat().st_size if jsonl.exists() else 0
    except OSError:
        size = 0

    return StoredSession(
        session_id=sid,
        cwd=d.get("cwd", "") or "(unknown)",
        title=(d.get("title") or "").strip(),
        updated_at=(d.get("updated_at") or "")[:19],
        mtime=json_path.stat().st_mtime,
        size_bytes=size,
        locked=_is_locked(lock),
    )


def list_sessions() -> list[StoredSession]:
    """Scan all session .json files. ~32ms for 150 files (metadata only)."""
    if not SESSIONS_DIR.is_dir():
        return []
    out: list[StoredSession] = []
    for jf in SESSIONS_DIR.glob("*.json"):
        s = _read_one(jf)
        if s:
            out.append(s)
    return out


def _configured_cwds() -> dict[str, str]:
    """Return {cwd_path: workspace_name} for kiro2chat-configured workspaces."""
    from ..config_manager import load_config_file
    result: dict[str, str] = {}
    for name, v in (load_config_file().get("_workspaces", {}) or {}).items():
        path = v.get("path") if isinstance(v, dict) else str(v)
        if path:
            result[str(Path(path).expanduser())] = name
    return result


def list_by_workspace() -> list[WorkspaceGroup]:
    """Group all stored sessions by cwd, sorted by most-recent activity.

    Each group is flagged if its cwd is already a configured kiro2chat
    workspace, so the UI can show a ✓ and skip the 'Add' action.
    """
    configured = _configured_cwds()
    groups: dict[str, WorkspaceGroup] = {}

    for s in list_sessions():
        cwd_norm = str(Path(s.cwd).expanduser()) if s.cwd != "(unknown)" else s.cwd
        g = groups.get(cwd_norm)
        if g is None:
            name = configured.get(cwd_norm, "")
            g = WorkspaceGroup(cwd=s.cwd, in_config=bool(name), config_name=name)
            groups[cwd_norm] = g
        g.sessions.append(s)

    # Sort sessions within group (newest first), and groups by their newest session
    for g in groups.values():
        g.sessions.sort(key=lambda x: x.mtime, reverse=True)

    return sorted(
        groups.values(),
        key=lambda g: g.sessions[0].mtime if g.sessions else 0,
        reverse=True,
    )


def delete_session(session_id: str, cli_path: str = "kiro-cli") -> tuple[bool, str]:
    """Delete a session via the official kiro-cli command.

    Runs `kiro-cli chat --delete-session <id>` (works from any cwd, removes
    both the JSON files and any legacy SQLite rows). Returns (ok, message).

    Note: each call spawns a kiro-cli process (~2s startup overhead). For
    many sessions use `delete_sessions()` which parallelizes.
    """
    if not session_id:
        return False, "empty session id"
    try:
        r = subprocess.run(
            [cli_path, "chat", "--delete-session", session_id],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return False, f"{cli_path} not found"
    except subprocess.TimeoutExpired:
        return False, "delete timed out"

    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return True, out or f"Deleted {session_id}"
    return False, out or f"delete failed (exit {r.returncode})"


def _max_workers() -> int:
    """Concurrency = 4× CPU cores, capped at 16.

    Each `kiro-cli --delete-session` is ~2s of mostly process-startup/IO wait
    (not CPU-bound), so oversubscribing cores helps. ~138MB RSS per process,
    so 16 × 138MB ≈ 2.2GB peak — safe on typical hosts.
    """
    cores = os.cpu_count() or 4
    return min(cores * 4, 16)


def delete_sessions(
    session_ids: list[str],
    cli_path: str = "kiro-cli",
    on_progress=None,
) -> tuple[int, int]:
    """Delete many sessions concurrently. Returns (ok_count, fail_count).

    `on_progress(done, total)` is called after each completion (thread-safe
    caller responsibility — the UI passes a callback that just updates a
    notification message string).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ids = [s for s in session_ids if s]
    if not ids:
        return 0, 0

    ok = fail = done = 0
    total = len(ids)
    workers = min(_max_workers(), total)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(delete_session, sid, cli_path): sid for sid in ids}
        for fut in as_completed(futures):
            success, _ = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
            done += 1
            if on_progress:
                on_progress(done, total)
    return ok, fail


def stat_summary() -> tuple[int, int]:
    """Return (session_count, workspace_count) for the overview line."""
    sessions = list_sessions()
    cwds = {str(Path(s.cwd).expanduser()) for s in sessions if s.cwd != "(unknown)"}
    return len(sessions), len(cwds)
