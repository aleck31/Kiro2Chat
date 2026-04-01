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
        self._permission_futures: dict[str, concurrent.futures.Future] = {}
        self._pending_permission_chat: dict[str, PermissionRequest] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._bot_id: int | None = None

        self._client.event(self.on_ready)
        self._client.event(self.on_message)

    def _chat_id(self, message: discord.Message) -> str:
        if isinstance(message.channel, discord.DMChannel):
            return f"discord.private.{message.author.id}"
        # Use thread id if in thread, otherwise channel id
        channel = message.channel
        if isinstance(channel, discord.Thread):
            return f"discord.group.{channel.id}"
        return f"discord.group.{channel.id}"

    def _is_mentioned(self, message: discord.Message) -> bool:
        if not self._bot_id:
            return False
        return self._bot_id in [u.id for u in message.mentions]

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

        # Permission reply
        if cid in self._permission_futures and lower in ("y", "yes", "ok", "n", "no", "t", "trust", "always"):
            fut = self._permission_futures.pop(cid, None)
            self._pending_permission_chat.pop(cid, None)
            if fut and not fut.done():
                if lower in ("y", "yes", "ok"):
                    fut.set_result("allow_once")
                elif lower in ("t", "trust", "always"):
                    fut.set_result("allow_always")
                else:
                    fut.set_result("deny")
            return

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
                return self._bridge.prompt(cid, text, images=images, timeout=300, on_stream=on_stream)

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
        self._permission_futures[chat_id] = fut
        self._pending_permission_chat[chat_id] = request

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
            self._permission_futures.pop(chat_id, None)
            self._pending_permission_chat.pop(chat_id, None)
            return "deny"

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._bridge.on_permission_request("discord.", self._handle_permission)
        await self._client.start(self._token)
