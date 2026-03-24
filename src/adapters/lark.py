"""Lark/Feishu adapter for kiro2chat — powered by ACP via Bridge."""

import asyncio
import concurrent.futures
import json
import logging
import os
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest, CreateMessageRequestBody,
    PatchMessageRequest, PatchMessageRequestBody,
    GetMessageResourceRequest,
    P2ImMessageReceiveV1,
)

from .base import BaseAdapter
from ..acp.bridge import Bridge
from ..acp.client import PermissionRequest, ToolCallInfo

logger = logging.getLogger(__name__)

EDIT_INTERVAL = 15


class LarkAdapter(BaseAdapter):
    def __init__(self, bridge: Bridge, app_id: str, app_secret: str, domain: str = "feishu"):
        self._bridge = bridge
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = lark.LARK_DOMAIN if domain == "lark" else lark.FEISHU_DOMAIN
        self._client: lark.Client | None = None
        self._ws: lark.ws.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # permission futures: chat_id -> concurrent.futures.Future
        self._permission_futures: dict[str, concurrent.futures.Future] = {}
        self._pending_permission_chat: dict[str, PermissionRequest] = {}
        self._session_locks: dict[str, threading.Lock] = {}

    def _chat_id(self, event) -> str:
        """Derive session key from lark event."""
        msg = event.event.message
        chat_id = msg.chat_id
        chat_type = msg.chat_type
        # Use root_id (topic) if available, otherwise chat_id
        root_id = msg.root_id or ""
        if chat_type == "group":
            return f"lark.group.{root_id or chat_id}"
        return f"lark.private.{chat_id}"

    def _is_mentioned(self, event) -> bool:
        """Check if bot is @mentioned in group chat."""
        msg = event.event.message
        return bool(msg.mentions)

    def _extract_text(self, event) -> str:
        """Extract plain text from message content."""
        msg = event.event.message
        msg_type = msg.message_type
        if msg_type == "text":
            try:
                content = json.loads(msg.content)
                text = content.get("text", "")
                # Remove @mentions from text
                if msg.mentions:
                    for m in msg.mentions:
                        text = text.replace(m.key, "").strip()
                return text
            except (json.JSONDecodeError, AttributeError):
                return ""
        return ""

    def _send_message(self, chat_id: str, text: str, msg_type: str = "text") -> str | None:
        """Send a message and return message_id."""
        if not self._client:
            return None
        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type(msg_type) \
            .content(json.dumps({"text": text})) \
            .build()
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.create(req)
        if resp.success():
            return resp.data.message_id
        logger.error("[Lark] Send failed: %s", resp.msg)
        return None

    def _update_message(self, message_id: str, text: str):
        """Update an existing message."""
        if not self._client:
            return
        body = PatchMessageRequestBody.builder() \
            .content(json.dumps({"text": text})) \
            .build()
        req = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.patch(req)
        if not resp.success():
            logger.debug("[Lark] Update failed: %s", resp.msg)

    def _download_image(self, message_id: str, file_key: str) -> tuple[str, str] | None:
        """Download image from lark, return (base64_data, mime_type) or None."""
        if not self._client:
            return None
        try:
            req = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(file_key) \
                .type("image") \
                .build()
            resp = self._client.im.v1.message_resource.get(req)
            if not resp.success():
                logger.error("[Lark] Image download failed: %s", resp.msg)
                return None
            import base64
            data = resp.file.read()
            b64 = base64.b64encode(data).decode()
            # Detect mime from magic bytes
            from ..acp.client import ACPClient
            mime = ACPClient._detect_image_mime(b64) or "image/jpeg"
            return b64, mime
        except Exception as e:
            logger.error("[Lark] Image download error: %s", e)
            return None

    def _extract_image(self, event) -> tuple[str, str] | None:
        """Extract image from message, return (base64, mime) or None."""
        msg = event.event.message
        if msg.message_type != "image":
            return None
        try:
            content = json.loads(msg.content)
            image_key = content.get("image_key", "")
            if image_key:
                return self._download_image(msg.message_id, image_key)
        except Exception:
            pass
        return None

    def _handle_message(self, data: P2ImMessageReceiveV1):
        """Handle incoming message event (called from SDK thread)."""
        event = data
        msg = event.event.message
        chat_type = msg.chat_type

        # Group chat: only respond to @mentions
        if chat_type == "group" and not self._is_mentioned(event):
            return

        # Check if this is a permission reply
        chat_id_for_perm = f"lark.group.{msg.root_id or msg.chat_id}" if chat_type == "group" else f"lark.private.{msg.chat_id}"
        text = self._extract_text(event)
        lower = text.lower().strip()

        # Cancel support
        if lower == "cancel":
            self._bridge.cancel(chat_id_for_perm)
            self._send_message(msg.chat_id, "🛑 Cancelled")
            return

        # Permission reply
        if chat_id_for_perm in self._permission_futures and lower in ("y", "yes", "ok", "n", "no", "t", "trust", "always"):
            fut = self._permission_futures.pop(chat_id_for_perm, None)
            self._pending_permission_chat.pop(chat_id_for_perm, None)
            if fut and not fut.done():
                if lower in ("y", "yes", "ok"):
                    fut.set_result("allow_once")
                elif lower in ("t", "trust", "always"):
                    fut.set_result("allow_always")
                else:
                    fut.set_result("deny")
            return

        # Extract image if present
        images = None
        img = self._extract_image(event)
        if img:
            images = [img]

        if not text and not images:
            return

        cid = self._chat_id(event)

        # Concurrency: one request at a time per session
        if cid not in self._session_locks:
            self._session_locks[cid] = threading.Lock()
        lock = self._session_locks[cid]
        if lock.locked():
            self._send_message(msg.chat_id, "⏳ 上一条消息还在处理中，请稍候...")
            with lock:
                pass

        with lock:
            reply_id = self._send_message(msg.chat_id, "⏳ Thinking...")

            chunk_count = 0

            def on_stream(chunk: str, accumulated: str):
                nonlocal chunk_count
                chunk_count += 1
                if chunk_count % EDIT_INTERVAL == 0 and reply_id:
                    display = accumulated[:4000]
                    if display:
                        self._update_message(reply_id, display)

        try:
            result = self._bridge.prompt(cid, text, images=images, timeout=300, on_stream=on_stream)

            # Build final display
            parts = []
            if result.tool_calls:
                for tc in result.tool_calls:
                    icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "🔧")
                    parts.append(f"{icon} {tc.title}")
                parts.append("")
            if result.text:
                parts.append(result.text)

            display = "\n".join(parts) or "(empty response)"
            if reply_id:
                self._update_message(reply_id, display[:4000])

        except Exception as e:
            logger.error("[Lark] Chat error: %s", e)
            if reply_id:
                self._update_message(reply_id, f"❌ Error: {e}")

    def _handle_permission(self, request: PermissionRequest) -> str | None:
        """Sync handler called from Bridge thread."""
        # Find chat_id for this session
        chat_id_str = None
        lark_chat_id = None
        for cid, info in self._bridge._sessions.items():
            if info.session_id == request.session_id:
                chat_id_str = cid
                break
        if not chat_id_str:
            return "allow_once"

        # Extract lark chat_id from session key (lark.group.xxx or lark.private.xxx)
        parts = chat_id_str.split(".", 2)
        lark_chat_id = parts[2] if len(parts) > 2 else parts[-1]

        import concurrent.futures
        fut = concurrent.futures.Future()
        self._permission_futures[chat_id_str] = fut
        self._pending_permission_chat[chat_id_str] = request

        self._send_message(
            lark_chat_id,
            f"🔐 Kiro 请求执行操作:\n📋 {request.title}\n\n回复: y(允许) / n(拒绝) / t(信任)\n⏱️ 60秒内未回复将自动拒绝",
        )

        try:
            return fut.result(timeout=60)
        except Exception:
            self._permission_futures.pop(chat_id_str, None)
            self._pending_permission_chat.pop(chat_id_str, None)
            return "deny"

    # ── Lifecycle ──

    async def start(self):
        self._loop = asyncio.get_running_loop()

        self._client = lark.Client.builder() \
            .app_id(self._app_id) \
            .app_secret(self._app_secret) \
            .domain(self._domain) \
            .build()

        self._bridge.on_permission_request(self._handle_permission)

        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._handle_message) \
            .build()

        self._ws = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        logger.info("🐦 Lark bot starting...")
        # ws.start() blocks, run in thread
        ws_thread = threading.Thread(target=self._ws.start, daemon=True)
        ws_thread.start()

        # Keep alive
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        pass

    # BaseAdapter interface
    async def send_text(self, chat_id: str, text: str):
        parts = chat_id.split(".", 2)
        lark_id = parts[2] if len(parts) > 2 else parts[-1]
        self._send_message(lark_id, text)

    async def send_streaming_update(self, chat_id: str, chunk: str, accumulated: str):
        pass  # handled inline via on_stream callback in _handle_message

    async def send_tool_status(self, chat_id: str, tool: ToolCallInfo):
        pass  # handled inline in final display

    async def request_permission(self, chat_id: str, request: PermissionRequest) -> str:
        return "allow_once"  # actual approval via _handle_permission sync handler

    async def send_image(self, chat_id: str, path: str):
        if not self._client or not os.path.isfile(path):
            return
        parts = chat_id.split(".", 2)
        lark_id = parts[2] if len(parts) > 2 else parts[-1]
        try:
            from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
            # Upload image
            with open(path, "rb") as f:
                body = CreateImageRequestBody.builder() \
                    .image_type("message") \
                    .image(f) \
                    .build()
                req = CreateImageRequest.builder().request_body(body).build()
                resp = self._client.im.v1.image.create(req)
            if not resp.success():
                logger.error("[Lark] Image upload failed: %s", resp.msg)
                return
            image_key = resp.data.image_key
            # Send image message
            self._send_message(lark_id, json.dumps({"image_key": image_key}), msg_type="image")
        except Exception as e:
            logger.error("[Lark] Send image error: %s", e)


def get_lark_config() -> tuple[str, str] | None:
    """Return (app_id, app_secret) or None."""
    app_id = os.environ.get("LARK_APP_ID", "")
    app_secret = os.environ.get("LARK_APP_SECRET", "")
    if app_id and app_secret:
        return app_id, app_secret
    return None
