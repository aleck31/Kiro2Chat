"""Bridge: session management and event routing between ACP and adapters.

Sessions are shared per workspace: different chat_ids (even across platforms)
that point at the same workspace reuse the same kiro-cli session, giving
cross-platform context continuity. A [platform/user] tag is injected into
each prompt so kiro can tell messages apart.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .client import ACPClient, PromptResult, StreamCallback

log = logging.getLogger(__name__)


class _SessionInfo:
    __slots__ = ("session_id", "last_active", "created_at", "lock", "workspace",
                 "bound_chat_ids", "last_prompt_chat_id")

    def __init__(self, session_id: str, workspace: str = "default"):
        self.session_id = session_id
        self.last_active = time.monotonic()
        self.created_at = time.time()
        self.lock = threading.Lock()
        self.workspace = workspace
        # chat_ids that have used this session (for display / debugging)
        self.bound_chat_ids: set[str] = set()
        # chat_id of the most recent prompt — used to route permission requests
        self.last_prompt_chat_id: str = ""


class Bridge:
    def __init__(
        self,
        cli_path: str = "kiro-cli",
        workspace_mode: str = "per_chat",
        fixed_workspace: str = "default",
        idle_timeout: int = 300,
    ):
        self._cli_path = cli_path
        self._workspace_mode = workspace_mode
        self._fixed_workspace = fixed_workspace
        self._idle_timeout = idle_timeout

        self._client: ACPClient | None = None
        # workspace_name → session (shared across chat_ids / platforms)
        self._sessions: dict[str, _SessionInfo] = {}
        # chat_id → active workspace name
        self._active_workspace: dict[str, str] = {}
        self._client_lock = threading.Lock()
        self._client_started_at: float = 0
        # prefix → handler, e.g. "tg." → telegram_handler
        self._permission_handlers: dict[str, Callable] = {}

        self._reaper: threading.Thread | None = None
        self._running = False

    # ── Lifecycle ──

    def start(self):
        self._running = True
        if self._idle_timeout > 0:
            self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
            self._reaper.start()
        log.info("[Bridge] Started (mode=%s, idle=%ds)", self._workspace_mode, self._idle_timeout)

    def stop(self):
        self._running = False
        for info in self._sessions.values():
            self._release_session_lock(info.session_id)
        self._sessions.clear()
        self._active_workspace.clear()
        if self._client:
            self._client.stop()
            self._client = None
        log.info("[Bridge] Stopped")

    def on_permission_request(self, prefix: str, handler: Callable):
        """Register permission handler for chat_ids starting with prefix."""
        self._permission_handlers[prefix] = handler

    def off_permission_request(self, prefix: str):
        """Unregister permission handler. Safe to call if not registered."""
        self._permission_handlers.pop(prefix, None)

    # ── Workspace API ──

    def get_active_workspace(self, chat_id: str) -> str:
        return self._active_workspace.get(chat_id, "default")

    def switch_workspace(self, chat_id: str, workspace_name: str):
        from src.config import config
        if workspace_name not in config.workspaces:
            raise ValueError(f"Unknown workspace: {workspace_name}")
        # Only retarget this chat_id's pointer. The shared session for the
        # old workspace stays alive for any other chat_ids still using it.
        old_ws = self._active_workspace.get(chat_id)
        self._active_workspace[chat_id] = workspace_name
        if old_ws and old_ws != workspace_name:
            # Unbind chat_id from old workspace session
            info = self._sessions.get(old_ws)
            if info:
                info.bound_chat_ids.discard(chat_id)
            log.info("[Bridge] chat_id=%s switched workspace %s → %s", chat_id, old_ws, workspace_name)

    def get_workspaces(self) -> dict[str, str]:
        """Return {name: path} for display."""
        from src.config import config
        return {name: ws["path"] for name, ws in config.workspaces.items()}

    # ── Public API ──

    def prompt(
        self,
        chat_id: str,
        text: str,
        images: list[tuple[str, str]] | None = None,
        timeout: float = 0,
        on_stream: StreamCallback | None = None,
        author: str = "",
    ) -> PromptResult:
        if not timeout:
            from src.config import config
            timeout = config.response_timeout
        info = self._ensure_session(chat_id)
        info.last_active = time.monotonic()
        info.bound_chat_ids.add(chat_id)
        info.last_prompt_chat_id = chat_id

        tagged = _inject_tag(chat_id, author, text)
        with info.lock:
            return self._client.session_prompt(
                info.session_id, tagged, images=images, timeout=timeout, on_stream=on_stream,
            )

    def cancel(self, chat_id: str):
        ws = self.get_active_workspace(chat_id)
        info = self._sessions.get(ws)
        if info and self._client:
            self._client.session_cancel(info.session_id)

    def clear(self, chat_id: str):
        """Reset the session for chat_id's active workspace.

        Note: because sessions are shared per workspace, this affects every
        chat_id/platform currently bound to this workspace.
        """
        ws = self.get_active_workspace(chat_id)
        info = self._sessions.pop(ws, None)
        if info:
            self._release_session_lock(info.session_id)
        self._save_workspace_session(ws, "")

    def set_mode(self, chat_id: str, mode_id: str) -> dict:
        info = self._ensure_session(chat_id)
        return self._client.session_set_mode(info.session_id, mode_id)

    def get_available_modes(self, chat_id: str) -> list:
        info = self._ensure_session(chat_id)
        modes = self._client._session_modes.get(info.session_id, {})
        return modes.get("availableModes", [])

    def get_current_mode(self, chat_id: str) -> str:
        info = self._ensure_session(chat_id)
        modes = self._client._session_modes.get(info.session_id, {})
        return modes.get("currentModeId", "")

    def set_model(self, chat_id: str, model_id: str) -> dict:
        info = self._ensure_session(chat_id)
        return self._client.session_set_model(info.session_id, model_id)

    def get_available_models(self, chat_id: str) -> list:
        info = self._ensure_session(chat_id)
        return self._client.get_available_models(info.session_id)

    def get_current_model(self, chat_id: str) -> str:
        info = self._ensure_session(chat_id)
        return self._client.get_current_model(info.session_id)

    def get_context_usage(self, chat_id: str) -> float | None:
        """Return last known context usage percentage, or None."""
        ws = self.get_active_workspace(chat_id)
        info = self._sessions.get(ws)
        if info and self._client:
            return self._client._context_usage.get(info.session_id)
        return None

    def get_sessions(self) -> list[dict]:
        """Return active sessions (one per workspace)."""
        now = time.monotonic()
        return [
            {
                "chat_id": ", ".join(sorted(info.bound_chat_ids)) or "(none)",
                "session_id": info.session_id,
                "idle_seconds": int(now - info.last_active),
                "started_at": info.created_at,
                "workspace": ws,
            }
            for ws, info in self._sessions.items()
        ]

    # ── Internal ──

    def _ensure_client(self) -> ACPClient:
        with self._client_lock:
            if self._client and self._client.is_running():
                return self._client
            log.info("[Bridge] Starting kiro-cli acp...")
            self._client = ACPClient(cli_path=self._cli_path)
            cwd = self._resolve_fixed_path() if self._workspace_mode == "fixed" else None
            self._client.start(cwd=cwd)
            self._client_started_at = time.monotonic()
            if self._permission_handlers:
                def _dispatch_permission(req):
                    # Find the session owning this session_id, then route to the
                    # chat_id that most recently triggered a prompt on it.
                    target_chat_id = None
                    for info in self._sessions.values():
                        if info.session_id == req.session_id:
                            target_chat_id = info.last_prompt_chat_id
                            break
                    if target_chat_id:
                        for prefix, handler in self._permission_handlers.items():
                            if target_chat_id.startswith(prefix):
                                return handler(target_chat_id, req)
                    return "allow_once"
                self._client.on_permission_request(_dispatch_permission)
            return self._client

    def _resolve_fixed_path(self) -> str:
        """Resolve the fixed-mode workspace path from its configured name."""
        from src.config import config
        ws = config.workspaces.get(self._fixed_workspace, {})
        ws_path = ws.get("path") if isinstance(ws, dict) else ws
        if not ws_path:
            raise ValueError(
                f"Fixed workspace '{self._fixed_workspace}' not found in [workspaces]. "
                f"Go to Settings → Workspaces to define it."
            )
        p = Path(ws_path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def _get_workspace_path(self, chat_id: str) -> str:
        # Fixed mode: every chat shares the fixed workspace (ignores per-chat selection).
        if self._workspace_mode == "fixed":
            return self._resolve_fixed_path()

        # per_chat mode: resolve the active workspace's configured path.
        ws_name = self.get_active_workspace(chat_id)
        from src.config import config
        ws = config.workspaces.get(ws_name, {})
        ws_path = ws.get("path") if isinstance(ws, dict) else ws
        if not ws_path:
            raise ValueError(
                f"Workspace '{ws_name}' has no path configured. "
                f"Go to Settings → Workspaces to set one."
            )
        p = Path(ws_path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def _ensure_session(self, chat_id: str) -> _SessionInfo:
        ws_name = self.get_active_workspace(chat_id)
        if ws_name in self._sessions:
            return self._sessions[ws_name]

        self._ensure_client()
        cwd = self._get_workspace_path(chat_id)

        # Get session_id from workspace config
        from src.config import config
        ws_cfg = config.workspaces.get(ws_name, {})
        sid_mem = ws_cfg.get("session_id") if isinstance(ws_cfg, dict) else None
        # Read fresh from file to detect mismatch
        from src.config_manager import load_config_file
        file_ws = load_config_file().get("_workspaces", {}).get(ws_name, {})
        sid_file = file_ws.get("session_id") if isinstance(file_ws, dict) else None
        if sid_mem != sid_file:
            log.warning("[Bridge] _ensure_session: sid MISMATCH ws=%s mem=%s file=%s", ws_name, sid_mem, sid_file)
        sid = sid_file or sid_mem
        log.debug("[Bridge] _ensure_session: chat_id=%s ws=%s sid=%s", chat_id, ws_name, sid)

        if sid and self._client.session_load(sid, cwd):
            log.info("[Bridge] Resumed session %s for %s", sid, ws_name)
            info = _SessionInfo(sid, workspace=ws_name)
            self._sessions[ws_name] = info
            return info

        if sid:
            log.warning("[Bridge] session/load failed for %s (sid=%s), creating new", ws_name, sid)
        session_id, _ = self._client.session_new(cwd)
        info = _SessionInfo(session_id, workspace=ws_name)
        self._sessions[ws_name] = info
        self._save_workspace_session(ws_name, session_id)
        return info

    def _save_workspace_session(self, ws_name: str, session_id: str):
        """Write session_id back to workspace config in config.toml."""
        from src.config_manager import load_config_file, save_config_file
        data = load_config_file()
        ws = data.get("_workspaces", {})
        entry = ws.get(ws_name, {})
        if isinstance(entry, str):
            entry = {"path": entry}
        old_sid = entry.get("session_id")
        if session_id:
            entry["session_id"] = session_id
        else:
            entry.pop("session_id", None)
        ws[ws_name] = entry
        data["_workspaces"] = ws
        log.debug("[Bridge] save_workspace_session(%s): %s → %s", ws_name, old_sid, session_id or "(cleared)")
        save_config_file(data)
        from src.config import reload
        reload()

    def _release_session_lock(self, session_id: str):
        """Remove the .lock file so kiro-cli chat can resume this session."""
        lock_path = Path.home() / ".kiro" / "sessions" / "cli" / f"{session_id}.lock"
        try:
            lock_path.unlink(missing_ok=True)
            log.debug("[Bridge] Removed lock file for %s", session_id)
        except Exception as e:
            log.warning("[Bridge] Failed to remove lock for %s: %s", session_id, e)

    def _reap_loop(self):
        while self._running:
            time.sleep(60)
            if not self._idle_timeout:
                continue
            now = time.monotonic()
            idle = [
                (ws, info) for ws, info in self._sessions.items()
                if now - info.last_active > self._idle_timeout
            ]
            for ws, info in idle:
                self._sessions.pop(ws, None)
                self._release_session_lock(info.session_id)
                log.info("[Bridge] Reaped idle session for ws=%s", ws)

            if (
                not self._sessions
                and self._client
                and now - self._client_started_at > self._idle_timeout
            ):
                log.info("[Bridge] No active sessions, stopping kiro-cli")
                self._client.stop()
                self._client = None


# ── Tag injection ──

def _inject_tag(chat_id: str, author: str, text: str) -> str:
    """Prepend [platform/user] tag so kiro can distinguish cross-platform messages.

    chat_id format: {channel}.{scope}.{raw_id}  (e.g. tg.group.123, lark.direct.oc_abc)
    """
    parts = chat_id.split(".", 2)
    platform = parts[0] if parts else "chat"
    scope = parts[1] if len(parts) > 1 else ""
    tag_platform = f"{platform}-group" if scope == "group" else platform
    user = author.strip() if author else (parts[2] if len(parts) > 2 else "?")
    return f"[{tag_platform}/{user}] {text}"
