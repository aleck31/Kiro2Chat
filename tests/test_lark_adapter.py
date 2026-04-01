"""Tests for Lark adapter."""

from unittest.mock import MagicMock

from src.adapters.lark import LarkAdapter
from src.adapters.base import dispatch_command


def _make_adapter():
    bridge = MagicMock()
    return LarkAdapter(bridge, "app_id", "app_secret")


def _make_event(chat_id="oc_123", chat_type="p2p", text="hello", mentions=None, root_id=""):
    event = MagicMock()
    event.event.message.chat_id = chat_id
    event.event.message.chat_type = chat_type
    event.event.message.message_type = "text"
    event.event.message.root_id = root_id
    event.event.message.mentions = mentions or []
    import json
    event.event.message.content = json.dumps({"text": text})
    return event


def test_chat_id_private():
    a = _make_adapter()
    event = _make_event(chat_id="oc_abc", chat_type="p2p")
    assert a._chat_id(event) == "lark.private.oc_abc"


def test_chat_id_group_no_topic():
    a = _make_adapter()
    event = _make_event(chat_id="oc_group1", chat_type="group", root_id="")
    assert a._chat_id(event) == "lark.group.oc_group1"


def test_chat_id_group_with_topic():
    a = _make_adapter()
    event = _make_event(chat_id="oc_group1", chat_type="group", root_id="om_topic123")
    assert a._chat_id(event) == "lark.group.om_topic123"


def test_is_mentioned_true():
    a = _make_adapter()
    mention = MagicMock()
    event = _make_event(mentions=[mention])
    assert a._is_mentioned(event) is True


def test_is_mentioned_false():
    a = _make_adapter()
    event = _make_event(mentions=[])
    assert a._is_mentioned(event) is False


def test_extract_text():
    a = _make_adapter()
    event = _make_event(text="hello world")
    assert a._extract_text(event) == "hello world"


def test_extract_text_with_mention():
    a = _make_adapter()
    mention = MagicMock()
    mention.key = "@_user_1"
    event = _make_event(text="@_user_1 hello", mentions=[mention])
    assert a._extract_text(event) == "hello"


def test_extract_text_non_text_message():
    a = _make_adapter()
    event = _make_event()
    event.event.message.message_type = "image"
    assert a._extract_text(event) == ""


# ── /workspace command test ──


def test_workspace_list():
    bridge = MagicMock()
    bridge.get_active_workspace.return_value = "default"
    bridge.get_workspaces.return_value = {"default": "/tmp/d", "proj": "/tmp/p"}
    result = dispatch_command(bridge, "lark.private.x", "/workspace list")
    assert "default" in result
    assert "✓" in result
    assert "proj" in result
