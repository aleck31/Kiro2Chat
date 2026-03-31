"""Tests for Discord adapter."""

from unittest.mock import MagicMock, AsyncMock
import discord
import pytest

from src.adapters.discord import DiscordAdapter


def _make_adapter():
    bridge = MagicMock()
    adapter = DiscordAdapter(bridge, "fake-token")
    adapter._bot_id = 12345
    return adapter


def _make_message(content="hello", author_id=99, channel_type="dm", mentions=None, guild=None):
    msg = MagicMock(spec=discord.Message)
    msg.content = content
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.guild = guild
    msg.attachments = []
    msg.mentions = mentions or []
    msg.reply = AsyncMock()

    if channel_type == "dm":
        msg.channel = MagicMock(spec=discord.DMChannel)
    elif channel_type == "thread":
        msg.channel = MagicMock(spec=discord.Thread)
        msg.channel.id = 777
    else:
        msg.channel = MagicMock(spec=discord.TextChannel)
        msg.channel.id = 888
    return msg


# ── chat_id tests ──

def test_chat_id_dm():
    a = _make_adapter()
    msg = _make_message(channel_type="dm", author_id=42)
    assert a._chat_id(msg) == "discord.private.42"


def test_chat_id_guild_channel():
    a = _make_adapter()
    msg = _make_message(channel_type="text")
    assert a._chat_id(msg) == "discord.group.888"


def test_chat_id_thread():
    a = _make_adapter()
    msg = _make_message(channel_type="thread")
    assert a._chat_id(msg) == "discord.group.777"


# ── mention tests ──

def test_is_mentioned_true():
    a = _make_adapter()
    bot_user = MagicMock()
    bot_user.id = 12345
    msg = _make_message(mentions=[bot_user])
    assert a._is_mentioned(msg) is True


def test_is_mentioned_false():
    a = _make_adapter()
    msg = _make_message(mentions=[])
    assert a._is_mentioned(msg) is False


def test_is_mentioned_no_bot_id():
    a = _make_adapter()
    a._bot_id = None
    msg = _make_message()
    assert a._is_mentioned(msg) is False


# ── extract_text tests ──

def test_extract_text_plain():
    a = _make_adapter()
    msg = _make_message(content="hello world")
    assert a._extract_text(msg) == "hello world"


def test_extract_text_strips_mention():
    a = _make_adapter()
    msg = _make_message(content="<@12345> hello world")
    assert a._extract_text(msg) == "hello world"


# ── send_long tests ──


@pytest.mark.asyncio
async def test_send_long_short():
    a = _make_adapter()
    reply = AsyncMock()
    await a._send_long(reply, "short message")
    reply.edit.assert_called_once_with(content="short message")


@pytest.mark.asyncio
async def test_send_long_splits():
    a = _make_adapter()
    reply = AsyncMock()
    reply.channel = AsyncMock()
    text = "x" * 3000
    await a._send_long(reply, text)
    reply.edit.assert_called_once_with(content="x" * 2000)
    reply.channel.send.assert_called_once_with("x" * 1000)


# ── /workspace command test ──

def test_workspace_command():
    """Test /workspace triggers handle_workspace_command."""
    a = _make_adapter()
    msg = _make_message(content="/workspace", channel_type="dm", author_id=42)
    a._bridge.get_active_workspace.return_value = "default"
    a._bridge.get_workspaces.return_value = {"default": "/tmp/d"}
    import asyncio
    asyncio.run(a.on_message(msg))
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "default" in reply_text
