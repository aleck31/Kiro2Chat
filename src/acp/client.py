"""ACP client for communicating with kiro-cli via JSON-RPC 2.0 over stdio."""

import json
import logging
import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

_BUF_SIZE = 4 * 1024 * 1024


@dataclass
class ToolCallInfo:
    tool_call_id: str = ""
    title: str = ""
    kind: str = ""
    status: str = "pending"
    content: str = ""
    image_paths: list[str] = field(default_factory=list)


@dataclass
class PromptResult:
    text: str = ""
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    stop_reason: str = ""


@dataclass
class PermissionRequest:
    session_id: str
    tool_call_id: str
    title: str
    options: list  # [{"optionId": "allow_once", "name": "Yes"}, ...]


# Handler returns "allow_once" | "allow_always" | "deny" | None (timeout)
PermissionHandler = Callable[[PermissionRequest], str | None]
StreamCallback = Callable[[str, str], None]  # (chunk, accumulated)


class ACPClient:
    def __init__(self, cli_path: str = "kiro-cli"):
        self._cli_path = cli_path
        self._proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._session_updates: dict[str, list[dict]] = {}
        self._context_usage: dict[str, float] = {}  # session_id -> percentage
        self._active_prompts: dict[str, int] = {}
        self._permission_handler: PermissionHandler | None = None
        self._stream_callbacks: dict[str, StreamCallback] = {}
        self._stream_accum: dict[str, list[str]] = {}
        self._session_modes: dict[str, dict] = {}
        self._session_models: dict[str, dict] = {}
        self._running = False

    def on_permission_request(self, handler: PermissionHandler):
        self._permission_handler = handler

    # ── Lifecycle ──

    def start(self, cwd: str | None = None) -> dict:
        import ctypes

        def _set_pdeathsig():
            """Ask kernel to send SIGTERM to child when parent dies."""
            try:
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG = 1
            except Exception:
                pass

        self._proc = subprocess.Popen(
            [self._cli_path, "acp"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            preexec_fn=_set_pdeathsig,
        )
        self._running = True
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        result = self._send_request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": True, "writeTextFile": True},
                "terminal": True,
            },
            "clientInfo": {"name": "kiro2chat", "version": "1.0.0"},
        })
        log.info("[ACP] Initialized: %s", json.dumps(result, ensure_ascii=False)[:200])
        return result

    def stop(self):
        self._running = False
        if self._proc and self._proc.poll() is None:
            self._kill_children(self._proc.pid)
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        log.info("[ACP] Stopped")

    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    # ── Session API ──

    def session_new(self, cwd: str) -> tuple[str, dict]:
        result = self._send_request("session/new", {
            "cwd": cwd,
            "mcpServers": [],
        })
        session_id = result.get("sessionId", "")
        if not session_id:
            raise RuntimeError(f"session/new returned no sessionId: {result}")
        self._session_modes[session_id] = result.get("modes", {})
        self._session_models[session_id] = result.get("models", {})
        log.info("[ACP] New session: %s", session_id)
        return session_id, result.get("modes", {})

    def session_load(self, session_id: str, cwd: str) -> bool:
        """Try to load/resume an existing session. Returns True on success."""
        try:
            # Collect replay updates during load (and discard them)
            self._session_updates[session_id] = []
            result = self._send_request("session/load", {
                "sessionId": session_id,
                "cwd": cwd,
                "mcpServers": [],
            }, timeout=120)
            # Discard replayed history
            self._session_updates.pop(session_id, None)
            self._session_modes[session_id] = result.get("modes", {})
            self._session_models[session_id] = result.get("models", {})
            log.info("[ACP] Loaded session: %s", session_id)
            return True
        except Exception as e:
            self._session_updates.pop(session_id, None)
            log.debug("[ACP] Failed to load session %s: %s", session_id, e)
            return False

    def session_prompt(
        self,
        session_id: str,
        text: str,
        images: list[tuple[str, str]] | None = None,
        timeout: float = 300,
        on_stream: StreamCallback | None = None,
    ) -> PromptResult:
        self._session_updates[session_id] = []
        if on_stream:
            self._stream_callbacks[session_id] = on_stream
            self._stream_accum[session_id] = []

        req_id = self._next_id()
        self._active_prompts[session_id] = req_id

        try:
            prompt_content = []
            if images:
                for b64_data, mime_type in images:
                    detected = self._detect_image_mime(b64_data)
                    if detected:
                        mime_type = detected
                    prompt_content.append({
                        "type": "image", "data": b64_data, "mimeType": mime_type,
                    })
            if text:
                prompt_content.append({"type": "text", "text": text})
            elif images:
                prompt_content.append({"type": "text", "text": "?"})
            if not prompt_content:
                prompt_content.append({"type": "text", "text": ""})

            result = self._send_request_with_id("session/prompt", {
                "sessionId": session_id,
                "prompt": prompt_content,
            }, req_id, timeout=timeout)
            return self._build_prompt_result(session_id, result)
        finally:
            self._active_prompts.pop(session_id, None)
            self._stream_callbacks.pop(session_id, None)
            self._stream_accum.pop(session_id, None)

    def session_cancel(self, session_id: str):
        if session_id not in self._active_prompts:
            return
        self._send_notification("session/cancel", {"sessionId": session_id})

    def session_set_mode(self, session_id: str, mode_id: str) -> dict:
        result = self._send_request("session/set_mode", {
            "sessionId": session_id, "modeId": mode_id,
        })
        if session_id in self._session_modes:
            self._session_modes[session_id]["currentModeId"] = mode_id
        return result

    def session_set_model(self, session_id: str, model_id: str) -> dict:
        result = self._send_request("session/set_model", {
            "sessionId": session_id, "modelId": model_id,
        }, timeout=15)
        if session_id in self._session_models:
            self._session_models[session_id]["currentModelId"] = model_id
        return result

    def get_available_models(self, session_id: str) -> list:
        return self._session_models.get(session_id, {}).get("availableModels", [])

    def get_current_model(self, session_id: str) -> str:
        return self._session_models.get(session_id, {}).get("currentModelId", "")

    # ── JSON-RPC transport ──

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _send_raw(self, msg: dict):
        data = json.dumps(msg, ensure_ascii=False) + "\n"
        log.debug("[ACP] >>> %s", data.strip()[:500])
        self._proc.stdin.write(data.encode())
        self._proc.stdin.flush()

    def _send_notification(self, method: str, params: dict):
        self._send_raw({"jsonrpc": "2.0", "method": method, "params": params})

    def _send_request(self, method: str, params: dict, timeout: float = 300) -> dict:
        return self._send_request_with_id(method, params, self._next_id(), timeout)

    def _send_request_with_id(self, method: str, params: dict, req_id: int, timeout: float = 300) -> dict:
        evt = threading.Event()
        holder: list = []
        self._pending[req_id] = (evt, holder)

        self._send_raw({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        if not evt.wait(timeout=timeout):
            self._pending.pop(req_id, None)
            raise TimeoutError(f"Request {method} (id={req_id}) timed out")

        self._pending.pop(req_id, None)
        if len(holder) == 2 and holder[0] is None:
            err = holder[1]
            raise RuntimeError(f"RPC error {err.get('code')}: {err.get('message')}")
        return holder[0] if holder else {}

    # ── Read loops ──

    def _read_loop(self):
        while self._running:
            try:
                line = self._proc.stdout.readline(_BUF_SIZE)
                if not line:
                    break
                self._handle_line(line.decode(errors="replace").strip())
            except Exception as e:
                if self._running:
                    log.error("[ACP] Read error: %s", e)
                break
        self._running = False

    def _read_stderr(self):
        while self._running:
            try:
                line = self._proc.stderr.readline()
                if not line:
                    break
                log.debug("[ACP stderr] %s", line.decode(errors="replace").strip())
            except Exception:
                break

    def _handle_line(self, line: str):
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log.warning("[ACP] Non-JSON: %s", line[:200])
            return

        log.debug("[ACP] <<< %s", line[:500])
        msg_id = msg.get("id")
        method = msg.get("method")

        # Response to pending request
        if msg_id is not None and method is None:
            pending = self._pending.get(msg_id)
            if pending:
                evt, holder = pending
                if msg.get("error"):
                    holder.extend([None, msg["error"]])
                else:
                    holder.append(msg.get("result", {}))
                evt.set()
            return

        # Request from kiro (needs response)
        if msg_id is not None and method:
            if method == "session/request_permission":
                self._handle_permission_request(msg_id, msg.get("params", {}))
            return

        # Notification
        if method and msg_id is None:
            params = msg.get("params", {})
            session_id = params.get("sessionId", "")
            if method == "session/update" and session_id:
                self._handle_session_update(session_id, params.get("update", {}))
            elif method == "_kiro.dev/metadata" and session_id:
                pct = params.get("contextUsagePercentage")
                if pct is not None:
                    self._context_usage[session_id] = round(pct, 1)

    def _handle_session_update(self, session_id: str, update: dict):
        updates = self._session_updates.get(session_id)
        if updates is not None:
            updates.append(update)

        if update.get("sessionUpdate") == "agent_message_chunk":
            content = update.get("content", {})
            if isinstance(content, dict) and content.get("type") == "text":
                chunk = content.get("text", "")
                cb = self._stream_callbacks.get(session_id)
                accum = self._stream_accum.get(session_id)
                if cb and accum is not None and chunk:
                    accum.append(chunk)
                    try:
                        cb(chunk, "".join(accum))
                    except Exception as e:
                        log.debug("[ACP] Stream callback error: %s", e)

    def _handle_permission_request(self, msg_id: int, params: dict):
        session_id = params.get("sessionId", "")
        tool_call = params.get("toolCall", {})
        title = tool_call.get("title", "Unknown operation")

        if self._permission_handler is None:
            log.info("[ACP] No permission handler, auto-approving: %s", title)
            self._send_permission_response(msg_id, "allow_once")
            return

        request = PermissionRequest(
            session_id=session_id,
            tool_call_id=tool_call.get("toolCallId", ""),
            title=title,
            options=params.get("options", []),
        )

        def _handle():
            try:
                decision = self._permission_handler(request)
                self._send_permission_response(msg_id, decision or "deny")
            except Exception as e:
                log.error("[ACP] Permission handler error: %s", e)
                self._send_permission_response(msg_id, "deny")

        threading.Thread(target=_handle, daemon=True).start()

    def _send_permission_response(self, msg_id: int, option_id: str):
        if option_id == "deny":
            outcome = {"outcome": "cancelled"}
        else:
            outcome = {"outcome": "selected", "optionId": option_id}
        self._send_raw({"jsonrpc": "2.0", "id": msg_id, "result": {"outcome": outcome}})

    # ── Result building ──

    def _build_prompt_result(self, session_id: str, rpc_result: dict) -> PromptResult:
        updates = self._session_updates.pop(session_id, [])
        result = PromptResult(stop_reason=rpc_result.get("stopReason", ""))
        text_parts = []
        tool_calls: dict[str, ToolCallInfo] = {}

        for update in updates:
            st = update.get("sessionUpdate", "")
            if st == "agent_message_chunk":
                content = update.get("content", {})
                if isinstance(content, dict) and content.get("type") == "text":
                    text_parts.append(content.get("text", ""))
            elif st == "tool_call":
                tc_id = update.get("toolCallId", "")
                tool_calls[tc_id] = ToolCallInfo(
                    tool_call_id=tc_id,
                    title=update.get("title", ""),
                    kind=update.get("kind", ""),
                    status=update.get("status", "pending"),
                )
            elif st == "tool_call_update":
                tc_id = update.get("toolCallId", "")
                tc = tool_calls.get(tc_id)
                if tc:
                    tc.status = update.get("status", tc.status)
                    if update.get("title"):
                        tc.title = update["title"]
                    for c in update.get("content", []):
                        if isinstance(c, dict):
                            inner = c.get("content", {})
                            if isinstance(inner, dict):
                                if inner.get("type") == "text":
                                    tc.content = inner.get("text", "")
                                elif inner.get("type") == "image":
                                    path = inner.get("path", "")
                                    if path:
                                        tc.image_paths.append(path)

        result.text = "".join(text_parts)
        result.tool_calls = list(tool_calls.values())
        result.image_paths = [p for tc in result.tool_calls for p in tc.image_paths]
        return result

    # ── Helpers ──

    @staticmethod
    def _detect_image_mime(b64_data: str) -> str | None:
        if b64_data.startswith("iVBORw"):
            return "image/png"
        elif b64_data.startswith("/9j/"):
            return "image/jpeg"
        elif b64_data.startswith("R0lGOD"):
            return "image/gif"
        elif b64_data.startswith("UklGR"):
            return "image/webp"
        return None

    def _kill_children(self, parent_pid: int):
        import shutil
        pgrep = shutil.which("pgrep") or "/usr/bin/pgrep"
        try:
            r = subprocess.run([pgrep, "-P", str(parent_pid)], capture_output=True, text=True)
            for pid_str in r.stdout.strip().split("\n"):
                if pid_str:
                    child_pid = int(pid_str)
                    self._kill_children(child_pid)
                    try:
                        os.kill(child_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        except Exception as e:
            log.debug("[ACP] Error killing children: %s", e)
            