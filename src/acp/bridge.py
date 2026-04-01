"""Bridge: session management and event routing between ACP and adapters."""

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .client import ACPClient, PromptResult, StreamCallback

log = logging.getLogger(__name__)


class _SessionInfo:
    __slots__ = ("session_id", "last_active", "lock", "workspace")

    def __init__(self, session_id: str, workspace: str = "default"):
        self.session_id = session_id
        self.last_active = time.monotonic()
        self.lock = threading.Lock()
        self.workspace = workspace


class Bridge:
    def __init__(
        self,
        cli_path: str = "kiro-cli",
        workspace_mode: str = "per_chat",
        working_dir: str = "/tmp/kiro2chat-workspaces",
        idle_timeout: int = 300,
    ):
        self._cli_path = cli_path
        self._workspace_mode = workspace_mode
        self._working_dir = Path(working_dir)
        self._idle_timeout = idle_timeout

        self._client: ACPClient | None = None
        # (chat_id, workspace_name) → session
        self._sessions: dict[tuple[str, str], _SessionInfo] = {}
        # chat_id → active workspace name
        self._active_workspace: dict[str, str] = {}
        self._client_lock = threading.Lock()
        self._client_started_at: float = 0
        # prefix → handler, e.g. "private." → telegram_handler
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
        self._sessions.clear()
        self._active_workspace.clear()
        if self._client:
            self._client.stop()
            self._client = None
        log.info("[Bridge] Stopped")

    def on_permission_request(self, prefix: str, handler: Callable):
        """Register permission handler for chat_ids starting with prefix."""
        self._permission_handlers[prefix] = handler

    # ── Workspace API ──

    def get_active_workspace(self, chat_id: str) -> str:
        return self._active_workspace.get(chat_id, "default")

    def switch_workspace(self, chat_id: str, workspace_name: str):
        from src.config import config  
        if workspace_name not in config.workspaces:
            raise ValueError(f"Unknown workspace: {workspace_name}")
        self._active_workspace[chat_id] = workspace_name

    def get_workspaces(self) -> dict[str, str]:
        """Return {name: path} for display."""
        from src.config import config
        return {name: ws["path"] for name, ws in config.workspaces.items()}

    # ── Public API ──

    def _session_key(self, chat_id: str) -> tuple[str, str]:
        ws = self.get_active_workspace(chat_id)
        return (chat_id, ws)

    def prompt(
        self,
        chat_id: str,
        text: str,
        images: list[tuple[str, str]] | None = None,
        timeout: float = 300,
        on_stream: StreamCallback | None = None,
    ) -> PromptResult:
        info = self._ensure_session(chat_id)
        info.last_active = time.monotonic()
        with info.lock:
            return self._client.session_prompt(
                info.session_id, text, images=images, timeout=timeout, on_stream=on_stream,
            )

    def cancel(self, chat_id: str):
        key = self._session_key(chat_id)
        info = self._sessions.get(key)
        if info and self._client:
            self._client.session_cancel(info.session_id)

    def clear(self, chat_id: str):
        """Reset current workspace session for chat_id."""
        key = self._session_key(chat_id)
        self._sessions.pop(key, None)
        # Clear session_id from config so next message creates fresh session
        self._save_workspace_session(key[1], "")

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
        key = self._session_key(chat_id)
        info = self._sessions.get(key)
        if info and self._client:
            return self._client._context_usage.get(info.session_id)
        return None

    def get_sessions(self) -> dict[str, dict]:
        """Return active sessions: {display_key: {session_id, idle_seconds, workspace}}."""
        now = time.monotonic()
        result = {}
        for (cid, ws), info in self._sessions.items():
            result[f"{cid}@{ws}"] = {
                "session_id": info.session_id,
                "idle_seconds": int(now - info.last_active),
                "workspace": ws,
            }
        return result

    # ── Internal ──

    def _ensure_client(self) -> ACPClient:
        with self._client_lock:
            if self._client and self._client.is_running():
                return self._client
            log.info("[Bridge] Starting kiro-cli acp...")
            self._client = ACPClient(cli_path=self._cli_path)
            cwd = str(self._working_dir) if self._workspace_mode == "fixed" else None
            self._client.start(cwd=cwd)
            self._client_started_at = time.monotonic()
            if self._permission_handlers:
                def _dispatch_permission(req):
                    # Find chat_id for this session
                    chat_id = None
                    for (cid, _ws), info in self._sessions.items():
                        if info.session_id == req.session_id:
                            chat_id = cid
                            break
                    if chat_id:
                        for prefix, handler in self._permission_handlers.items():
                            if chat_id.startswith(prefix):
                                return handler(chat_id, req)
                    return "allow_once"
                self._client.on_permission_request(_dispatch_permission)
            return self._client

    def _get_workspace_path(self, chat_id: str) -> str:
        ws_name = self.get_active_workspace(chat_id)
        from src.config import config
        ws = config.workspaces.get(ws_name, {})
        ws_path = ws.get("path") if isinstance(ws, dict) else ws
        if ws_path:
            p = Path(ws_path).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return str(p)
        ws = self._working_dir / chat_id
        ws.mkdir(parents=True, exist_ok=True)
        return str(ws)

    def _ensure_session(self, chat_id: str) -> _SessionInfo:
        key = self._session_key(chat_id)
        if key in self._sessions:
            return self._sessions[key]

        self._ensure_client()
        cwd = self._get_workspace_path(chat_id)
        ws_name = key[1]

        # Get session_id from workspace config
        from src.config import config
        ws_cfg = config.workspaces.get(ws_name, {})
        sid = ws_cfg.get("session_id") if isinstance(ws_cfg, dict) else None

        # Always try load first
        if sid and self._client.session_load(sid, cwd):
            info = _SessionInfo(sid, workspace=ws_name)
            self._sessions[key] = info
            return info

        # Fallback: create new and save to config
        session_id, _ = self._client.session_new(cwd)
        info = _SessionInfo(session_id, workspace=ws_name)
        self._sessions[key] = info
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
        if session_id:
            entry["session_id"] = session_id
        else:
            entry.pop("session_id", None)
        ws[ws_name] = entry
        data["_workspaces"] = ws
        save_config_file(data)
        from src.config import reload
        reload()

    def _reap_loop(self):
        while self._running:
            time.sleep(60)
            if not self._idle_timeout:
                continue
            now = time.monotonic()
            idle = [
                key for key, info in self._sessions.items()
                if now - info.last_active > self._idle_timeout
            ]
            for key in idle:
                self._sessions.pop(key, None)
                log.info("[Bridge] Reaped idle session for %s", key)

            if (
                not self._sessions
                and self._client
                and now - self._client_started_at > self._idle_timeout
            ):
                log.info("[Bridge] No active sessions, stopping kiro-cli")
                self._client.stop()
                self._client = None
