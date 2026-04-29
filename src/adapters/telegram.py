"""Telegram adapter for kiro2chat — powered by ACP via Bridge."""

import asyncio
import base64
import logging
import os
import re
import unicodedata
from collections import defaultdict
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.enums import ParseMode

from .base import BaseAdapter
from ..acp.bridge import Bridge
from ..acp.client import PermissionRequest, ToolCallInfo

logger = logging.getLogger(__name__)

EDIT_INTERVAL = 15  # edit message every N chunks

router = Router()

# Module-level refs set by TelegramAdapter.start()
_bridge: Bridge | None = None
_bot: Bot | None = None
_allowed_user_ids: frozenset[int] = frozenset()
_require_auth: bool = True
_notified_unauthorized: set[int] = set()


async def _allowlist_guard(handler, event, data):
    """Reject unauthorized senders when `require_auth` is on.

    `/claim <token>` always bypasses so new users can self-authorize via a
    one-time token from the dashboard.
    """
    user = getattr(event, "from_user", None)
    uid = getattr(user, "id", None)
    text = getattr(event, "text", "") or ""
    if text.startswith("/claim"):
        return await handler(event, data)
    if not _require_auth:
        return await handler(event, data)
    if uid is None or uid not in _allowed_user_ids:
        logger.warning(
            "[TG] Rejected update from unauthorized user id=%s username=%s",
            uid, getattr(user, "username", None),
        )
        if uid is not None and uid not in _notified_unauthorized:
            _notified_unauthorized.add(uid)
            try:
                from ..security import UNAUTHORIZED_HINT
                await event.answer(UNAUTHORIZED_HINT)
            except Exception:
                pass
        return
    return await handler(event, data)


def _refresh_allowlist():
    """Re-read allowlist + require_auth from config."""
    global _allowed_user_ids, _require_auth
    from .. import config as cfg_mod
    _allowed_user_ids = frozenset(cfg_mod.config.telegram.allowed_user_ids)
    _require_auth = bool(cfg_mod.config.telegram.require_auth)

# Per-session state
_session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
# Permission request futures: msg_id -> Future[str]
_permission_futures: dict[int, asyncio.Future] = {}


def _chat_id(message: Message) -> str:
    from .base import make_chat_id
    cid = abs(message.chat.id)
    scope = "group" if message.chat.type in ("group", "supergroup") else "direct"
    return make_chat_id("tg", scope, cid)


def _tg_author(message: Message) -> str:
    u = message.from_user
    if not u:
        return ""
    if u.username:
        return f"@{u.username}"
    name = (u.first_name or "") + (f" {u.last_name}" if u.last_name else "")
    return name.strip() or str(u.id)


# ── Markdown / HTML rendering (reused from original bot) ──

def _display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * (width - _display_width(s))


def _table_to_pre(text: str) -> str:
    lines = text.split("\n")
    result = []
    table_lines: list[list[str]] = []

    def flush():
        if not table_lines:
            return
        widths = [max(_display_width(row[c]) for row in table_lines) for c in range(len(table_lines[0]))]
        result.append("  ".join(_pad(cell, w) for cell, w in zip(table_lines[0], widths)))
        result.append("  ".join("-" * w for w in widths))
        for row in table_lines[1:]:
            result.append("  ".join(_pad(cell, w) for cell, w in zip(row, widths)))
        table_lines.clear()

    for line in lines:
        stripped = line.strip()
        if re.match(r'^\|(.+\|)+\s*$', stripped):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            if table_lines and len(cells) != len(table_lines[0]):
                flush()
            table_lines.append(cells)
        else:
            if table_lines:
                flush()
                result.append("")
            result.append(line)
    flush()
    return "\n".join(result)


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _apply_inline(s: str) -> str:
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'__(.+?)__', r'<b>\1</b>', s)
    s = re.sub(r'(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)', r'<i>\1</i>', s)
    s = re.sub(r'(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)', r'<i>\1</i>', s)
    return s


def _escape_and_format(s: str) -> str:
    result = []
    last = 0
    for m in re.finditer(r'`([^`]+)`', s):
        result.append(_apply_inline(_escape_html(s[last:m.start()])))
        result.append(f"<code>{_escape_html(m.group(1))}</code>")
        last = m.end()
    result.append(_apply_inline(_escape_html(s[last:])))
    return "".join(result)


def _md_to_html(text: str) -> str:
    text = _table_to_pre(text)
    parts = []
    last = 0
    for m in re.finditer(r'```(?:\w+)?\n?(.*?)```', text, re.DOTALL):
        parts.append(_escape_and_format(text[last:m.start()]))
        parts.append(f"<pre>{_escape_html(m.group(1).rstrip())}</pre>")
        last = m.end()
    parts.append(_escape_and_format(text[last:]))
    return "".join(parts)


def _clean_response(text: str) -> str:
    text = re.sub(r"<function_calls>.*?</function_calls>", "", text, flags=re.DOTALL)
    text = re.sub(r"<invoke.*?</invoke>", "", text, flags=re.DOTALL)
    text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _tool_icon(kind: str) -> str:
    return {"file_read": "📄", "file_edit": "📝", "terminal": "⚡"}.get(kind, "🔧")


def _tool_status_icon(status: str) -> str:
    return {"completed": "✅", "failed": "❌", "cancelled": "🚫"}.get(status, "⏳")


# ── Commands ──

@router.message(Command("claim"))
async def cmd_claim(message: Message):
    """Self-service authorization via a one-time token from the dashboard."""
    from ..security import consume_claim
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Usage: /claim <token>")
        return
    u = message.from_user
    if u is None:
        return
    uid = u.id
    uname = u.username or ((u.first_name or "") + (f" {u.last_name}" if u.last_name else "")).strip()
    status = consume_claim("telegram", parts[1].strip(), uid, uname)
    if status == "ok":
        from ..security import authorized_message
        _refresh_allowlist()
        _notified_unauthorized.discard(uid)
        logger.info("[TG] Authorized user id=%s via claim token", uid)
        await message.answer(authorized_message(uname, uid))
    elif status == "expired":
        await message.answer("❌ Token expired. Ask the operator to generate a new one.")
    elif status == "missing":
        await message.answer("❌ No active claim token. Generate one in the dashboard first.")
    else:  # mismatch
        await message.answer("❌ Invalid token.")


@router.message(Command("start"))
async def cmd_start(message: Message):
    from .base import HELP_TEXT
    await message.answer(f"👋 Hi! I'm Kiro bot — send me a message and I'll reply.\n\n{HELP_TEXT}")


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)


@router.message(Command("cancel"))
@router.message(Command("reset"))
@router.message(Command("model"))
@router.message(Command("agent"))
@router.message(Command("workspace"))
@router.message(Command("context"))
async def cmd_dispatch(message: Message):
    if not _bridge:
        return
    from .base import dispatch_command
    result = dispatch_command(_bridge, _chat_id(message), message.text or "")
    if result:
        await message.answer(result)



# ── Permission callback ──

@router.callback_query(F.data.startswith("perm:"))
async def handle_permission_callback(callback: CallbackQuery):
    _, msg_id_str, decision = callback.data.split(":", 2)
    msg_id = int(msg_id_str)
    fut = _permission_futures.pop(msg_id, None)
    if fut and not fut.done():
        fut.set_result(decision)
    label = {"allow_once": "✅ Allowed", "allow_always": "✅ Trusted", "deny": "🚫 Denied"}.get(decision, decision)
    await callback.answer(label)
    # Show result briefly then delete the permission message
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.edit_text(f"{callback.message.text}\n\n{label}")
            await asyncio.sleep(1)
            await callback.message.delete()
        except Exception:
            pass


# ── Message handlers ──

@router.message(F.photo)
async def handle_photo(message: Message):
    await _handle_message(message, has_photo=True)


@router.message(F.document)
async def handle_document(message: Message):
    doc = message.document
    if doc and doc.mime_type and doc.mime_type.startswith("image/"):
        await _handle_message(message, has_document_image=True)


@router.message(F.text)
async def handle_text(message: Message):
    await _handle_message(message)


async def _handle_message(message: Message, *, has_photo=False, has_document_image=False):
    if not _bridge:
        await message.answer("❌ Bridge not initialized")
        return

    cid = _chat_id(message)
    lock = _session_locks[cid]

    if lock.locked():
        await message.reply("⏳ 上一条消息还在处理中，请稍候...")
        async with lock:
            pass

    async with lock:
        reply = await message.answer("⏳ Thinking...")
        text = message.caption or message.text or ""
        author = _tg_author(message)

        # Collect images
        images: list[tuple[str, str]] | None = None
        if has_photo:
            photo = message.photo[-1]
            bio = await message.bot.download(photo)
            b64 = base64.b64encode(bio.read()).decode()
            images = [(b64, "image/jpeg")]
        elif has_document_image:
            doc = message.document
            bio = await message.bot.download(doc)
            b64 = base64.b64encode(bio.read()).decode()
            mime = doc.mime_type or "image/jpeg"
            images = [(b64, mime)]

        # Streaming state
        chunk_count = 0
        tool_lines: list[str] = []
        loop = asyncio.get_running_loop()

        def on_stream(chunk: str, accumulated: str):
            nonlocal chunk_count
            chunk_count += 1
            if chunk_count % EDIT_INTERVAL == 0:
                display = _clean_response(accumulated)
                if display:
                    preview = display
                    if tool_lines:
                        preview = "\n".join(tool_lines) + "\n\n" + display
                    asyncio.run_coroutine_threadsafe(
                        _safe_edit(reply, _md_to_html(preview)[:4096]),
                        loop,
                    )

        try:
            result = await loop.run_in_executor(
                None,
                lambda: _bridge.prompt(cid, text, images=images, on_stream=on_stream, author=author),
            )

            # Build final display
            parts = []
            if result.tool_calls:
                for tc in result.tool_calls:
                    icon = _tool_icon(tc.kind)
                    status = _tool_status_icon(tc.status)
                    parts.append(f"{icon} {tc.title} {status}")
                parts.append("")
            clean = _clean_response(result.text)
            if clean:
                parts.append(clean)

            display = "\n".join(parts) or "(empty response)"
            if result.tool_calls:
                # Permission cards were inserted after "Thinking...",
                # so send a new message at the bottom instead of editing.
                try:
                    await reply.edit_text("✅ Done")
                except Exception:
                    pass
                try:
                    await message.answer(_md_to_html(display)[:4096], parse_mode=ParseMode.HTML)
                except Exception:
                    await message.answer(display[:4096])
            else:
                try:
                    await reply.edit_text(_md_to_html(display)[:4096], parse_mode=ParseMode.HTML)
                except Exception:
                    try:
                        await reply.edit_text(display[:4096])
                    except Exception:
                        pass

            # Send output images
            for path in result.image_paths:
                try:
                    await message.answer_photo(FSInputFile(path))
                except Exception as e:
                    logger.debug("Failed to send image %s: %s", path, e)

        except Exception as e:
            logger.error("Chat error: %s", e)
            try:
                await reply.edit_text(f"❌ Error: {e}")
            except Exception:
                pass


async def _safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ── Adapter class ──

class TelegramAdapter(BaseAdapter):
    def __init__(self, bridge: Bridge, token: str):
        self._bridge = bridge
        self._token = token
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _refresh_allowlist(self):
        """Expose module-level refresh as an instance method so the manager
        can poke each adapter uniformly on config reload."""
        _refresh_allowlist()

    async def start(self):
        global _bridge, _bot, _allowed_user_ids, _require_auth
        _bridge = self._bridge
        self._bot = Bot(token=self._token)
        _bot = self._bot
        self._loop = asyncio.get_event_loop()
        self._dp = Dispatcher()
        if router.parent_router is not None:
            router._parent_router = None  # type: ignore[attr-defined]

        from ..config import config
        _allowed_user_ids = frozenset(config.telegram.allowed_user_ids)
        _require_auth = bool(config.telegram.require_auth)
        if not _require_auth:
            logger.warning("[TG] require_auth is OFF — anyone can talk to the bot")
        elif not _allowed_user_ids:
            logger.warning(
                "[TG] require_auth is ON but allowlist is empty — bot will reject ALL messages. "
                "Generate a claim token from Settings and DM the bot /claim <token>."
            )
        else:
            logger.info("[TG] Allowlist: %d user(s) authorized", len(_allowed_user_ids))
        router.message.outer_middleware(_allowlist_guard)
        router.callback_query.outer_middleware(_allowlist_guard)

        self._dp.include_router(router)

        # Register permission handler
        self._bridge.on_permission_request("tg.", self._handle_permission)

        from .base import COMMANDS
        await self._bot.set_my_commands([
            BotCommand(command=cmd.lstrip("/"), description=desc)
            for cmd, desc in COMMANDS
        ])

        logger.info("🤖 Telegram bot starting...")
        await self._dp.start_polling(self._bot, handle_signals=False)

    async def stop(self):
        # Stop polling first so Telegram server releases the getUpdates slot,
        # then close the bot session to avoid stale HTTP connection blocking
        # the next instance with a Conflict error.
        self._bridge.off_permission_request("tg.")
        if self._dp:
            try:
                await self._dp.stop_polling()
            except Exception:
                pass
        if self._bot:
            try:
                await self._bot.session.close()
            except Exception:
                pass

    def _handle_permission(self, chat_id: str, request: PermissionRequest) -> str | None:
        """Sync handler called from Bridge thread — bridges to async TG."""
        if not _bot or not self._loop:
            return "allow_once"

        import concurrent.futures
        f = concurrent.futures.Future()

        tg_chat_id = int(chat_id.split(".")[2])
        if ".group." in chat_id:
            tg_chat_id = -tg_chat_id

        async def _ask():
            nonlocal f
            async_fut = self._loop.create_future()

            msg = await _bot.send_message(
                tg_chat_id,
                f"🔐 Kiro 请求执行操作:\n📋 {request.title}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Allow", callback_data=f"perm:{0}:allow_once"),
                    InlineKeyboardButton(text="🔒 Trust", callback_data=f"perm:{0}:allow_always"),
                    InlineKeyboardButton(text="❌ Deny", callback_data=f"perm:{0}:deny"),
                ]]),
            )
            _permission_futures[msg.message_id] = async_fut
            await msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Allow", callback_data=f"perm:{msg.message_id}:allow_once"),
                InlineKeyboardButton(text="🔒 Trust", callback_data=f"perm:{msg.message_id}:allow_always"),
                InlineKeyboardButton(text="❌ Deny", callback_data=f"perm:{msg.message_id}:deny"),
            ]]))

            try:
                result = await asyncio.wait_for(async_fut, timeout=60)
                f.set_result(result)
            except asyncio.TimeoutError:
                f.set_result("deny")

        asyncio.run_coroutine_threadsafe(_ask(), self._loop)

        try:
            return f.result(timeout=65)
        except Exception:
            return "deny"

    # BaseAdapter interface (used if called programmatically)
    async def send_text(self, chat_id: str, text: str):
        if _bot:
            tg_id = int(chat_id.split(":")[0])
            await _bot.send_message(tg_id, text)

    async def send_streaming_update(self, chat_id: str, chunk: str, accumulated: str):
        pass  # handled inline via on_stream callback

    async def send_tool_status(self, chat_id: str, tool: ToolCallInfo):
        pass  # handled inline in final display

    async def request_permission(self, chat_id: str, request: PermissionRequest) -> str:
        return "allow_once"

    async def send_image(self, chat_id: str, path: str):
        if _bot and os.path.isfile(path):
            from aiogram.types import FSInputFile
            tg_id = int(chat_id.split(":")[0])
            await _bot.send_photo(tg_id, FSInputFile(path))


def get_bot_token() -> Optional[str]:
    from ..config import config
    return config.telegram.bot_token or None
