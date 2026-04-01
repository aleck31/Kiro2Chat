"""Tests for Telegram adapter."""

from unittest.mock import MagicMock

from src.adapters.telegram import (
    _chat_id, _clean_response, _md_to_html, _tool_icon, _tool_status_icon,
    _escape_html, _table_to_pre,
)
from src.adapters.base import dispatch_command


def _make_message(chat_id=123, chat_type="private", user_id=456):
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.from_user.id = user_id
    return msg


def test_chat_id_private():
    msg = _make_message(chat_id=100, chat_type="private", user_id=100)
    assert _chat_id(msg) == "tg.private.100"


def test_chat_id_group():
    msg = _make_message(chat_id=-100123, chat_type="group", user_id=456)
    assert _chat_id(msg) == "tg.group.100123"


def test_chat_id_supergroup():
    msg = _make_message(chat_id=-100999, chat_type="supergroup", user_id=789)
    assert _chat_id(msg) == "tg.group.100999"


def test_clean_response():
    text = "Hello <function_calls>hidden</function_calls> world"
    assert _clean_response(text) == "Hello  world"


def test_clean_response_strips_invoke():
    text = "before <invoke name='test'>data</invoke> after"
    assert _clean_response(text) == "before  after"


def test_clean_response_collapses_newlines():
    text = "a\n\n\n\n\nb"
    assert _clean_response(text) == "a\n\nb"


def test_escape_html():
    assert _escape_html("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_md_to_html_bold():
    assert "<b>bold</b>" in _md_to_html("**bold**")


def test_md_to_html_code():
    assert "<code>x</code>" in _md_to_html("`x`")


def test_md_to_html_code_block():
    result = _md_to_html("```python\nprint(1)\n```")
    assert "<pre>" in result
    assert "print(1)" in result


def test_table_to_pre():
    table = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = _table_to_pre(table)
    assert "A" in result
    assert "1" in result
    # Separator line exists
    assert "-" in result


def test_tool_icon():
    assert _tool_icon("file_read") == "📄"
    assert _tool_icon("terminal") == "⚡"
    assert _tool_icon("unknown") == "🔧"


def test_tool_status_icon():
    assert _tool_status_icon("completed") == "✅"
    assert _tool_status_icon("failed") == "❌"
    assert _tool_status_icon("running") == "⏳"


# ── dispatch_command (shared by all adapters) ──


def test_workspace_switch_message():
    bridge = MagicMock()
    result = dispatch_command(bridge, "chat1", "/workspace switch proj")
    assert "已切换到 proj" in result
    assert "空闲" in result
