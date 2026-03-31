"""Bridge: session management and event routing between ACP and adapters."""

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .client import ACPClient, PermissionRequest, PromptResult, StreamCallback

log = logging.getLogger(__name__)


class _SessionInfo:
    __slots__ = ("session_id", "last_active", "lock")

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.last_active = time.monotonic()
        self.lock = threading.Lock()  # serialize prompts per chat


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
        self._sessions: dict[str, _SessionInfo] = {}  # chat_id -> session
        self._client_lock = threading.Lock()
        self._client_started_at: float = 0
        self._permission_handler: Callable[[PermissionRequest], str | None] | None = None

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
        if self._client:
            self._client.stop()
            self._client = None
        log.info("[Bridge] Stopped")

    def on_permission_request(self, handler: Callable[[PermissionRequest], str | None]):
        self._permission_handler = handler

    # ── Public API ──

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
        info = self._sessions.get(chat_id)
        if info and self._client:
            self._client.session_cancel(info.session_id)

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

    def get_sessions(self) -> dict[str, dict]:
        """Return active sessions: {chat_id: {session_id, idle_seconds}}."""
        now = time.monotonic()
        return {
            cid: {"session_id": info.session_id, "idle_seconds": int(now - info.last_active)}
            for cid, info in self._sessions.items()
        }

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
            if self._permission_handler:
                self._client.on_permission_request(self._permission_handler)
            return self._client

    def _get_workspace(self, chat_id: str) -> str:
        if self._workspace_mode == "fixed":
            return str(self._working_dir)
        ws = self._working_dir / chat_id
        ws.mkdir(parents=True, exist_ok=True)
        return str(ws)

    def _ensure_session(self, chat_id: str) -> _SessionInfo:
        if chat_id in self._sessions:
            return self._sessions[chat_id]

        self._ensure_client()
        cwd = self._get_workspace(chat_id)
        session_id, _ = self._client.session_new(cwd)
        info = _SessionInfo(session_id)
        self._sessions[chat_id] = info
        return info

    def _reap_loop(self):
        while self._running:
            time.sleep(60)
            if not self._idle_timeout:
                continue
            now = time.monotonic()
            idle = [
                cid for cid, info in self._sessions.items()
                if now - info.last_active > self._idle_timeout
            ]
            for cid in idle:
                self._sessions.pop(cid, None)
                log.info("[Bridge] Reaped idle session for chat %s", cid)

            # Stop client if no sessions left and not recently started
            if (
                not self._sessions
                and self._client
                and now - self._client_started_at > self._idle_timeout
            ):
                log.info("[Bridge] No active sessions, stopping kiro-cli")
                self._client.stop()
                self._client = None
