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
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._stopping = False
        # permission futures: chat_id -> queue of futures (multiple can be pending)
        self._permission_queues: dict[str, list[concurrent.futures.Future]] = {}
        self._session_locks: dict[str, threading.Lock] = {}
        self._seen_messages: set[str] = set()
        self._require_auth: bool = False
        self._allowed_user_ids: frozenset[str] = frozenset()
        self._notified_unauthorized: set[str] = set()

    def _refresh_allowlist(self):
        from .. import config as cfg_mod
        self._require_auth = bool(cfg_mod.config.lark.require_auth)
        self._allowed_user_ids = frozenset(cfg_mod.config.lark.allowed_user_ids)

    def _lookup_user_name(self, open_id: str) -> str:
        """Resolve display name via contact.v3.user.get. Requires the
        `contact:contact.base:readonly` + `contact:user.base:readonly`
        scopes on the Lark app. Returns empty string on failure."""
        if not self._client or not open_id:
            return ""
        try:
            from lark_oapi.api.contact.v3 import GetUserRequest
            req = GetUserRequest.builder().user_id(open_id).user_id_type("open_id").build()
            resp = self._client.contact.v3.user.get(req)
            if resp.success() and resp.data and resp.data.user:
                return resp.data.user.name or ""
            logger.debug("[Lark] user.get(%s) not successful: %s",
                         open_id, getattr(resp, "msg", ""))
        except Exception as e:
            logger.debug("[Lark] user.get(%s) failed: %s", open_id, e)
        return ""

    def _handle_claim(self, chat_id: str, text: str, open_id: str):
        from ..security import consume_claim
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self._send_message(chat_id, "Usage: /claim <token>")
            return
        if not open_id:
            self._send_message(chat_id, "❌ Could not identify your Lark user id.")
            return
        uname = self._lookup_user_name(open_id)
        status = consume_claim("lark", parts[1].strip(), open_id, uname)
        if status == "ok":
            from ..security import authorized_message
            self._refresh_allowlist()
            self._notified_unauthorized.discard(open_id)
            logger.info("[Lark] Authorized open_id=%s via claim token", open_id)
            self._send_message(chat_id, authorized_message(uname, open_id))
        elif status == "expired":
            self._send_message(chat_id, "❌ Token expired. Ask the operator for a new one.")
        elif status == "missing":
            self._send_message(chat_id, "❌ No active claim token. Generate one in the dashboard first.")
        else:
            self._send_message(chat_id, "❌ Invalid token.")

    def _chat_id(self, event) -> str:
        """Derive session key from lark event."""
        from .base import make_chat_id
        msg = event.event.message
        chat_id = msg.chat_id
        chat_type = msg.chat_type
        root_id = msg.root_id or ""
        if chat_type == "group":
            return make_chat_id("lark", "group", root_id or chat_id)
        return make_chat_id("lark", "direct", chat_id)

    def _is_mentioned(self, event) -> bool:
        """Check if bot is @mentioned in group chat."""
        msg = event.event.message
        return bool(msg.mentions)

    def _author(self, event) -> str:
        """Best-effort sender identifier for tag injection."""
        try:
            sender = event.event.sender
            sid = getattr(sender, "sender_id", None)
            if sid:
                for attr in ("open_id", "user_id", "union_id"):
                    val = getattr(sid, attr, None)
                    if val:
                        return val
        except Exception:
            pass
        return ""

    def _extract_text(self, event) -> str:
        """Extract plain text from message content."""
        msg = event.event.message
        msg_type = msg.message_type
        try:
            content = json.loads(msg.content)
        except (json.JSONDecodeError, AttributeError):
            return ""
        if msg_type == "text":
            text = content.get("text", "")
            if msg.mentions:
                for m in msg.mentions:
                    text = text.replace(m.key, "").strip()
            return text
        if msg_type == "post":
            parts = []
            for line in content.get("content", []):
                for node in line:
                    if node.get("tag") == "text":
                        parts.append(node.get("text", ""))
            text = " ".join(parts).strip()
            if msg.mentions:
                for m in msg.mentions:
                    text = text.replace(m.key, "").strip()
            return text
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

    def _send_updatable(self, chat_id: str, text: str) -> str | None:
        """Send a minimal card message (patchable via Patch API) and return message_id.
        Feishu Patch API only supports interactive (card) messages, not plain text."""
        card = {"elements": [{"tag": "markdown", "content": text}]}
        return self._send_card(chat_id, card)

    def _update_message(self, message_id: str, text: str):
        """Update a card message content via Patch API."""
        if not self._client:
            return
        card = {"elements": [{"tag": "markdown", "content": text}]}
        body = PatchMessageRequestBody.builder() \
            .content(json.dumps(card)) \
            .build()
        req = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.patch(req)
        if not resp.success():
            logger.error("[Lark] Update message failed: code=%s msg=%s", resp.code, resp.msg)

    _update_card = _update_message

    def _send_image_file(self, chat_id: str, path: str):
        """Upload image and send to chat."""
        if not self._client:
            return
        try:
            from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
            with open(path, "rb") as f:
                img_req = CreateImageRequest.builder().request_body(
                    CreateImageRequestBody.builder().image_type("message").image(f).build()
                ).build()
                img_resp = self._client.im.v1.image.create(img_req)
                if not img_resp.success():
                    logger.error("[Lark] Image upload failed: %s", img_resp.msg)
                    return
                image_key = img_resp.data.image_key
            self._send_message(chat_id, json.dumps({"image_key": image_key}), msg_type="image")
        except Exception as e:
            logger.error("[Lark] Send image error: %s", e)

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

    def _extract_images(self, event) -> list[tuple[str, str]] | None:
        """Extract images from message, return list of (base64, mime) or None."""
        msg = event.event.message
        msg_type = msg.message_type
        try:
            content = json.loads(msg.content)
        except (json.JSONDecodeError, AttributeError):
            return None
        image_keys = []
        if msg_type == "image":
            key = content.get("image_key", "")
            if key:
                image_keys.append(key)
        elif msg_type == "post":
            for line in content.get("content", []):
                for node in line:
                    if node.get("tag") == "img":
                        key = node.get("image_key", "")
                        if key:
                            image_keys.append(key)
        if not image_keys:
            return None
        images = []
        for key in image_keys:
            result = self._download_image(msg.message_id, key)
            if result:
                images.append(result)
        return images or None

    def _handle_message(self, data: P2ImMessageReceiveV1):
        """Handle incoming message event (called from SDK event loop).
        Dispatches permission replies inline; offloads prompt work to a thread
        so the SDK event loop stays free to receive subsequent messages."""
        event = data
        msg = event.event.message

        # Dedup: skip if we've already seen this message_id
        mid = msg.message_id
        if mid in self._seen_messages:
            return
        self._seen_messages.add(mid)
        if len(self._seen_messages) > 500:
            self._seen_messages = set(list(self._seen_messages)[-200:])

        chat_type = msg.chat_type
        if chat_type == "group" and not self._is_mentioned(event):
            return

        chat_id_for_perm = self._chat_id(data)
        text = self._extract_text(event)
        lower = text.lower().strip()
        open_id = self._author(event)

        # /claim always bypasses the allowlist so new users can self-authorize.
        if text.strip().startswith("/claim"):
            self._handle_claim(msg.chat_id, text, open_id)
            return

        # Allowlist gate — require_auth on + not on the list → drop.
        if self._require_auth and open_id not in self._allowed_user_ids:
            logger.warning("[Lark] Rejected message from unauthorized open_id=%s", open_id)
            if open_id and open_id not in self._notified_unauthorized:
                self._notified_unauthorized.add(open_id)
                from ..security import UNAUTHORIZED_HINT
                self._send_message(msg.chat_id, UNAUTHORIZED_HINT)
            return

        # Commands
        from .base import dispatch_command
        result = dispatch_command(self._bridge, chat_id_for_perm, text)
        if result:
            self._send_message(msg.chat_id, result)
            return

        # Permission reply — resolve the oldest pending future for this chat
        queue = self._permission_queues.get(chat_id_for_perm, [])
        if queue:
            if lower in ("y", "yes", "ok", "n", "no", "t", "trust", "always"):
                fut = queue.pop(0)
                if not queue:
                    self._permission_queues.pop(chat_id_for_perm, None)
                if fut and not fut.done():
                    if lower in ("y", "yes", "ok"):
                        fut.set_result("allow_once")
                    elif lower in ("t", "trust", "always"):
                        fut.set_result("allow_always")
                    else:
                        fut.set_result("deny")
                return
            # Non y/n/t message while permissions pending — auto-deny all
            for fut in queue:
                if not fut.done():
                    fut.set_result("deny")
            self._permission_queues.pop(chat_id_for_perm, None)

        # Extract images if present
        images = self._extract_images(event)

        if not text and not images:
            return

        author = self._author(event)

        # Offload blocking prompt work to a separate thread so the SDK
        # event loop remains free to dispatch permission replies.
        threading.Thread(
            target=self._do_prompt,
            args=(msg, chat_id_for_perm, text, images, author),
            daemon=True,
        ).start()

    def _do_prompt(self, msg, cid: str, text: str, images, author: str = ""):
        """Run bridge.prompt in a worker thread (blocking)."""
        if cid not in self._session_locks:
            self._session_locks[cid] = threading.Lock()
        lock = self._session_locks[cid]
        if lock.locked():
            self._send_message(msg.chat_id, "⏳ 上一条消息还在处理中，请稍候...")
            with lock:
                pass

        with lock:
            reply_id = self._send_updatable(msg.chat_id, "⏳ Thinking...")

            chunk_count = 0

            def on_stream(chunk: str, accumulated: str):
                nonlocal chunk_count
                chunk_count += 1
                if chunk_count % EDIT_INTERVAL == 0 and reply_id:
                    display = accumulated[:4000]
                    if display:
                        self._update_message(reply_id, display)

            try:
                result = self._bridge.prompt(cid, text, images=images, on_stream=on_stream, author=author)

                parts = []
                if result.tool_calls:
                    for tc in result.tool_calls:
                        icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "🔧")
                        parts.append(f"{icon} {tc.title}")
                    parts.append("")
                if result.text:
                    parts.append(result.text)

                display = "\n".join(parts) or "(empty response)"

                if result.tool_calls and reply_id:
                    tool_lines = []
                    for tc in result.tool_calls:
                        icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "🔧")
                        tool_lines.append(f"{icon} {tc.title}")
                    self._update_message(reply_id, "\n".join(tool_lines)[:4000])
                    if result.text:
                        self._send_message(msg.chat_id, result.text[:4000])
                elif reply_id:
                    self._update_message(reply_id, display[:4000])

                for path in result.image_paths:
                    self._send_image_file(msg.chat_id, path)

            except Exception as e:
                logger.error("[Lark] Chat error: %s", e)
                if reply_id:
                    self._update_message(reply_id, f"❌ Error: {e}")

    def _handle_permission(self, chat_id_str: str, request: PermissionRequest) -> str | None:
        """Sync handler called from Bridge thread."""
        parts = chat_id_str.split(".", 2)
        lark_chat_id = parts[2] if len(parts) > 2 else parts[-1]

        import concurrent.futures
        fut = concurrent.futures.Future()
        if chat_id_str not in self._permission_queues:
            self._permission_queues[chat_id_str] = []
        self._permission_queues[chat_id_str].append(fut)

        # Send permission request as text card (interactive buttons don't work in WebSocket mode)
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🔐 Kiro 请求执行操作"},
                "template": "orange",
            },
            "elements": [
                {"tag": "markdown", "content": f"**{request.title}**"},
                {"tag": "markdown", "content": "回复: **y**(允许) / **n**(拒绝) / **t**(信任)"},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": "⏱️ 3分钟内未回复将自动拒绝"},
                ]},
            ],
        }
        card_msg_id = self._send_card(lark_chat_id, card)

        try:
            result = fut.result(timeout=180)
            if card_msg_id:
                label = {"allow_once": "✅ 已允许", "allow_always": "🔓 已信任", "deny": "🚫 已拒绝"}.get(result, result)
                self._update_card(card_msg_id, label)
            return result
        except Exception:
            if card_msg_id:
                self._update_card(card_msg_id, "⏱️ 超时自动拒绝")
            # Remove this specific future from the queue
            queue = self._permission_queues.get(chat_id_str, [])
            if fut in queue:
                queue.remove(fut)
            if not queue:
                self._permission_queues.pop(chat_id_str, None)
            return "deny"

    def _send_card(self, chat_id: str, card: dict) -> str | None:
        """Send an interactive message card."""
        if not self._client:
            return None
        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("interactive") \
            .content(json.dumps(card)) \
            .build()
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.create(req)
        if resp.success():
            return resp.data.message_id
        logger.error("[Lark] Send card failed: %s", resp.msg)
        return None

    # ── Lifecycle ──

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._refresh_allowlist()
        if not self._require_auth:
            logger.warning("[Lark] require_auth is OFF — anyone who can add the bot can use it")
        elif not self._allowed_user_ids:
            logger.warning(
                "[Lark] require_auth is ON but allowlist is empty — bot will reject ALL messages. "
                "Generate a claim token from Settings."
            )
        else:
            logger.info("[Lark] Allowlist: %d user(s) authorized", len(self._allowed_user_ids))

        self._client = lark.Client.builder() \
            .app_id(self._app_id) \
            .app_secret(self._app_secret) \
            .domain(self._domain) \
            .build()

        self._bridge.on_permission_request("lark.", self._handle_permission)

        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._handle_message) \
            .build()

        logger.info("🐦 Lark bot starting...")
        # lark SDK uses a module-level `loop` variable captured at import time (uvloop).
        # We must replace it with a standard asyncio loop to avoid conflicts.
        def _run_ws():
            import asyncio as _aio
            import lark_oapi.ws.client as _ws_mod
            ws_loop = _aio.new_event_loop()
            _ws_mod.loop = ws_loop
            self._ws_loop = ws_loop

            self._ws = lark.ws.Client(
                self._app_id,
                self._app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
                domain=self._domain,
            )
            try:
                self._ws.start()
            except Exception as e:
                if not self._stopping:
                    logger.error("[Lark] ws.start() failed: %s", e)

        self._ws_thread = threading.Thread(target=_run_ws, daemon=True)
        self._ws_thread.start()

        # Keep alive
        try:
            while not self._stopping:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self._stopping = True
        self._bridge.off_permission_request("lark.")
        # Close the websocket and stop the ws thread's event loop so the
        # daemon thread can exit (loop.run_until_complete(_select()) blocks
        # forever otherwise).
        if self._ws and self._ws_loop and self._ws_loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._ws._disconnect(), self._ws_loop
                ).result(timeout=5)
            except Exception as e:
                logger.debug("[Lark] _disconnect failed: %s", e)
            try:
                self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)
            except Exception:
                pass
        if self._ws_thread:
            self._ws_thread.join(timeout=3)

    # BaseAdapter interface — used by the scheduler to push proactively.
    async def send_text(self, chat_id: str, text: str):
        """Scheduler gives us "lark.direct.<open_id>" for per-user pushes.
        That raw id is an open_id, not a chat_id, so we must use the
        open_id receive type when calling Lark."""
        if not self._client:
            return
        parts = chat_id.split(".", 2)
        raw = parts[2] if len(parts) > 2 else parts[-1]
        receive_type = "open_id" if raw.startswith("ou_") else "chat_id"
        body = CreateMessageRequestBody.builder() \
            .receive_id(raw) \
            .msg_type("text") \
            .content(json.dumps({"text": text})) \
            .build()
        req = CreateMessageRequest.builder() \
            .receive_id_type(receive_type) \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.create(req)
        if not resp.success():
            logger.warning("[Lark] send_text(%s) failed: %s", raw, resp.msg)

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
