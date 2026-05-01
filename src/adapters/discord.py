"""Discord adapter for kiro2chat — powered by ACP via Bridge."""

import asyncio
import concurrent.futures
import logging
import threading

import discord

from .base import BaseAdapter
from ..acp.bridge import Bridge
from ..acp.client import PermissionRequest

logger = logging.getLogger(__name__)

EDIT_INTERVAL = 15
MAX_MSG_LEN = 2000


class DiscordAdapter(BaseAdapter):
    def __init__(self, bridge: Bridge, token: str):
        self._bridge = bridge
        self._token = token
        self._session_locks: dict[str, threading.Lock] = {}
        self._permission_queues: dict[str, list[concurrent.futures.Future]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._require_auth: bool = False
        self._allowed_user_ids: frozenset[int] = frozenset()
        self._notified_unauthorized: set[int] = set()

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._bot_id: int | None = None

        self._client.event(self.on_ready)
        self._client.event(self.on_message)

    def _refresh_allowlist(self):
        from .. import config as cfg_mod
        self._require_auth = bool(cfg_mod.config.discord.require_auth)
        self._allowed_user_ids = frozenset(cfg_mod.config.discord.allowed_user_ids)

    async def _handle_claim(self, message: "discord.Message", text: str):
        from ..security import consume_claim
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.channel.send("Usage: /claim <token>")
            return
        uid = message.author.id
        uname = getattr(message.author, "name", "") or getattr(message.author, "display_name", "")
        status = consume_claim("discord", parts[1].strip(), uid, uname)
        if status == "ok":
            from ..security import authorized_message
            self._refresh_allowlist()
            self._notified_unauthorized.discard(uid)
            logger.info("[Discord] Authorized user id=%s via claim token", uid)
            await message.channel.send(authorized_message(uname, uid))
        elif status == "expired":
            await message.channel.send("❌ Token expired. Ask the operator for a new one.")
        elif status == "missing":
            await message.channel.send("❌ No active claim token. Generate one in the dashboard first.")
        else:
            await message.channel.send("❌ Invalid token.")

    def _chat_id(self, message: discord.Message) -> str:
        from .base import make_chat_id
        if isinstance(message.channel, discord.DMChannel):
            return make_chat_id("discord", "direct", message.author.id)
        channel = message.channel
        cid = channel.id
        return make_chat_id("discord", "group", cid)

    def _is_mentioned(self, message: discord.Message) -> bool:
        if not self._bot_id:
            return False
        return self._bot_id in [u.id for u in message.mentions]

    def _author(self, message: discord.Message) -> str:
        u = message.author
        name = getattr(u, "name", "") or getattr(u, "display_name", "")
        return name or str(u.id)

    def _extract_text(self, message: discord.Message) -> str:
        text = message.content
        # Strip bot mention
        if self._bot_id:
            text = text.replace(f"<@{self._bot_id}>", "").strip()
        return text

    async def _download_image(self, attachment: discord.Attachment) -> tuple[str, str] | None:
        try:
            import base64
            data = await attachment.read()
            b64 = base64.b64encode(data).decode()
            from ..acp.client import ACPClient
            mime = ACPClient._detect_image_mime(b64) or attachment.content_type or "image/jpeg"
            return b64, mime
        except Exception as e:
            logger.error("[Discord] Image download error: %s", e)
            return None

    async def _extract_images(self, message: discord.Message) -> list[tuple[str, str]] | None:
        images = []
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                img = await self._download_image(att)
                if img:
                    images.append(img)
        return images or None

    async def on_ready(self):
        self._bot_id = self._client.user.id
        logger.info("[Discord] Logged in as %s (id=%s)", self._client.user, self._bot_id)

    async def on_message(self, message: discord.Message):
        # Ignore own messages
        if message.author == self._client.user:
            return

        # In guild channels, only respond to @mentions
        if message.guild and not self._is_mentioned(message):
            return

        text = self._extract_text(message)
        lower = text.lower().strip()
        cid = self._chat_id(message)
        author = self._author(message)

        # /claim always bypasses the allowlist.
        if text.strip().startswith("/claim"):
            await self._handle_claim(message, text)
            return

        # Allowlist gate.
        if self._require_auth and message.author.id not in self._allowed_user_ids:
            logger.warning(
                "[Discord] Rejected message from unauthorized user id=%s name=%s",
                message.author.id, message.author.name,
            )
            if message.author.id not in self._notified_unauthorized:
                self._notified_unauthorized.add(message.author.id)
                try:
                    from ..security import UNAUTHORIZED_HINT
                    await message.channel.send(UNAUTHORIZED_HINT)
                except Exception:
                    pass
            return

        # Permission reply
        queue = self._permission_queues.get(cid, [])
        if queue:
            if lower in ("y", "yes", "ok", "n", "no", "t", "trust", "always"):
                fut = queue.pop(0)
                if not queue:
                    self._permission_queues.pop(cid, None)
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
            self._permission_queues.pop(cid, None)

        # Commands
        from .base import dispatch_command
        result = dispatch_command(self._bridge, cid, text)
        if result:
            await message.reply(result)
            return

        # Extract images
        images = await self._extract_images(message)

        if not text and not images:
            return

        # Concurrency lock
        if cid not in self._session_locks:
            self._session_locks[cid] = threading.Lock()
        lock = self._session_locks[cid]
        if lock.locked():
            await message.reply("⏳ 上一条消息还在处理中，请稍候...")
            return

        # Send thinking indicator
        reply = await message.reply("⏳ Thinking...")

        accumulated = ""
        chunk_count = 0

        def on_stream(chunk: str, acc: str):
            nonlocal accumulated, chunk_count
            accumulated = acc
            chunk_count += 1

        def do_prompt():
            with lock:
                return self._bridge.prompt(cid, text, images=images, on_stream=on_stream, author=author)

        # Run in thread, poll for streaming updates
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, do_prompt)

        last_edit_chunk = 0
        while not future.done():
            await asyncio.sleep(0.5)
            if accumulated and chunk_count - last_edit_chunk >= EDIT_INTERVAL:
                last_edit_chunk = chunk_count
                display = accumulated[:MAX_MSG_LEN]
                try:
                    await reply.edit(content=display)
                except Exception:
                    pass

        try:
            result = future.result()
            parts = []
            if result.tool_calls:
                for tc in result.tool_calls:
                    icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "🔧")
                    parts.append(f"{icon} {tc.title}")
                parts.append("")
            if result.text:
                parts.append(result.text)
            final = "\n".join(parts) or accumulated or "(no response)"

            # Split long messages
            await self._send_long(reply, final)

            # Send output images
            for path in result.image_paths:
                try:
                    await reply.channel.send(file=discord.File(path))
                except Exception as e:
                    logger.debug("[Discord] Failed to send image %s: %s", path, e)

        except Exception as e:
            logger.error("[Discord] Chat error: %s", e)
            try:
                await reply.edit(content=f"❌ Error: {e}")
            except Exception:
                pass

    async def _send_long(self, reply: discord.Message, text: str):
        """Edit reply with text, splitting if > 2000 chars."""
        if len(text) <= MAX_MSG_LEN:
            await reply.edit(content=text)
            return

        # First chunk edits the reply
        await reply.edit(content=text[:MAX_MSG_LEN])
        # Remaining chunks as follow-up messages
        remaining = text[MAX_MSG_LEN:]
        while remaining:
            chunk = remaining[:MAX_MSG_LEN]
            remaining = remaining[MAX_MSG_LEN:]
            await reply.channel.send(chunk)

    def _handle_permission(self, chat_id: str, request: PermissionRequest) -> str | None:
        fut = concurrent.futures.Future()
        if chat_id not in self._permission_queues:
            self._permission_queues[chat_id] = []
        self._permission_queues[chat_id].append(fut)

        # Send permission prompt via asyncio
        async def send_prompt():
            for (cid, _ws), info in self._bridge._sessions.items():
                if cid == chat_id:
                    break
            logger.info("[Discord] Permission requested for %s: %s", chat_id, request.description)

        if self._loop:
            asyncio.run_coroutine_threadsafe(send_prompt(), self._loop)

        try:
            return fut.result(timeout=120)
        except concurrent.futures.TimeoutError:
            queue = self._permission_queues.get(chat_id, [])
            if fut in queue:
                queue.remove(fut)
            if not queue:
                self._permission_queues.pop(chat_id, None)
            return "deny"

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._refresh_allowlist()
        if not self._require_auth:
            logger.warning("[Discord] require_auth is OFF — anyone sharing a server / DMing the bot can use it")
        elif not self._allowed_user_ids:
            logger.warning(
                "[Discord] require_auth is ON but allowlist is empty — bot will reject ALL messages. "
                "Generate a claim token from Settings."
            )
        else:
            logger.info("[Discord] Allowlist: %d user(s) authorized", len(self._allowed_user_ids))
        self._bridge.on_permission_request("discord.", self._handle_permission)
        await self._client.start(self._token)

    async def stop(self):
        self._bridge.off_permission_request("discord.")
        try:
            await self._client.close()
        except Exception as e:
            logger.debug("[Discord] close() failed: %s", e)

    # BaseAdapter interface — used by the scheduler to push proactively.
    async def send_text(self, chat_id: str, text: str):
        """chat_id is "discord.<scope>.<id>".
        - direct: <id> is the recipient user id → open DM and send
        - group:  <id> is the channel id → send there
        """
        parts = chat_id.split(".")
        scope = parts[1] if len(parts) >= 2 else "direct"
        raw = parts[-1]
        try:
            target_id = int(raw)
        except ValueError:
            logger.warning("[Discord] send_text: malformed chat_id %r", chat_id)
            return
        if scope == "group":
            channel = self._client.get_channel(target_id)
            if channel is None:
                logger.warning("[Discord] send_text: channel %s not found", target_id)
                return
            await channel.send(text)
        else:
            user = self._client.get_user(target_id) or await self._client.fetch_user(target_id)
            if user is None:
                logger.warning("[Discord] send_text: user %s not found", target_id)
                return
            await user.send(text)
