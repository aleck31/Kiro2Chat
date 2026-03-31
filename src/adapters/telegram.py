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

# Per-session state
_session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
# Permission request futures: msg_id -> Future[str]
_permission_futures: dict[int, asyncio.Future] = {}


def _chat_id(message: Message) -> str:
    cid = abs(message.chat.id)
    if message.chat.type in ("group", "supergroup"):
        return f"group.{cid}"
    return f"private.{cid}"


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

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Hi! I'm Kiro bot — send me a message and I'll reply.\n\n"
        "Commands:\n"
        "/model — 查看/切换模型\n"
        "/agent — 查看/切换 Agent\n"
        "/cancel — 取消当前操作\n"
        "/clear — 重置会话\n"
        "/help — 帮助"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if _bridge:
        _bridge.cancel(_chat_id(message))
    await message.answer("🛑 Cancelled")


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    # Remove session so next message creates a fresh one
    if _bridge:
        cid = _chat_id(message)
        _bridge._sessions.pop(cid, None)
    await message.answer("🗑 会话已重置")


@router.message(Command("model"))
async def cmd_model(message: Message):
    if not _bridge:
        return
    cid = _chat_id(message)
    args = (message.text or "").split(maxsplit=1)

    if len(args) < 2:
        models = _bridge.get_available_models(cid)
        current = _bridge.get_current_model(cid)
        if models:
            lines = []
            for m in models:
                mid = m.get("modelId", m) if isinstance(m, dict) else str(m)
                desc = m.get("description", "") if isinstance(m, dict) else ""
                marker = " ✓" if mid == current else ""
                lines.append(f"• `{mid}`{marker}" + (f"\n  {desc}" if desc else ""))
            model_list = "\n".join(lines)
        else:
            model_list = "(start a chat first)"
        await message.answer(
            f"Current: `{current or 'unknown'}`\n\n{model_list}\n\nSet: `/model <name>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    chosen = args[1].strip()
    available = _bridge.get_available_models(cid)
    valid_ids = {(m.get("modelId") if isinstance(m, dict) else str(m)) for m in available}
    if valid_ids and chosen not in valid_ids:
        await message.answer(f"Unknown model `{chosen}`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        _bridge.set_model(cid, chosen)
        await message.answer(f"✅ Model: `{chosen}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await message.answer(f"❌ {e}")


@router.message(Command("agent"))
async def cmd_agent(message: Message):
    if not _bridge:
        return
    cid = _chat_id(message)
    args = (message.text or "").split(maxsplit=1)

    if len(args) < 2:
        modes = _bridge.get_available_modes(cid)
        current = _bridge.get_current_mode(cid)
        if modes:
            lines = []
            for m in modes:
                mid = m.get("id", m) if isinstance(m, dict) else str(m)
                desc = m.get("description", "") if isinstance(m, dict) else ""
                marker = " ✓" if mid == current else ""
                lines.append(f"• `{mid}`{marker}" + (f"\n  {desc}" if desc else ""))
            mode_list = "\n".join(lines)
        else:
            mode_list = "(start a chat first)"
        await message.answer(
            f"Current: `{current or 'unknown'}`\n\n{mode_list}\n\nSwitch: `/agent <name>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    chosen = args[1].strip()
    try:
        _bridge.set_mode(cid, chosen)
        await message.answer(f"✅ Agent: `{chosen}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await message.answer(f"❌ {e}")


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
    # Update the permission message
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.edit_text(f"{callback.message.text}\n\n{label}")
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
                lambda: _bridge.prompt(cid, text, images=images, timeout=300, on_stream=on_stream),
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

    async def start(self):
        global _bridge, _bot
        _bridge = self._bridge
        self._bot = Bot(token=self._token)
        _bot = self._bot
        self._loop = asyncio.get_event_loop()
        self._dp = Dispatcher()
        self._dp.include_router(router)

        # Register permission handler
        self._bridge.on_permission_request(self._handle_permission)

        await self._bot.set_my_commands([
            BotCommand(command="model", description="切换/查看模型"),
            BotCommand(command="agent", description="切换/查看 Agent"),
            BotCommand(command="cancel", description="取消当前操作"),
            BotCommand(command="clear", description="重置会话"),
            BotCommand(command="help", description="帮助信息"),
        ])

        logger.info("🤖 Telegram bot starting...")
        await self._dp.start_polling(self._bot, handle_signals=False)

    async def stop(self):
        if self._dp:
            await self._dp.stop_polling()

    def _handle_permission(self, request: PermissionRequest) -> str | None:
        """Sync handler called from Bridge thread — bridges to async TG."""
        if not _bot or not self._loop:
            return "allow_once"

        import concurrent.futures
        f = concurrent.futures.Future()

        async def _ask():
            nonlocal f
            async_fut = self._loop.create_future()

            chat_id_str = None
            for cid, info in self._bridge._sessions.items():
                if info.session_id == request.session_id:
                    chat_id_str = cid
                    break
            if not chat_id_str:
                f.set_result("allow_once")
                return

            tg_chat_id = int(chat_id_str.split(":")[0])
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
    return os.environ.get("TG_BOT_TOKEN")
