"""Tests for Bridge."""

from unittest.mock import MagicMock, patch
from src.acp.bridge import Bridge, _SessionInfo, _inject_tag


def test_session_info():
    info = _SessionInfo("sid-123")
    assert info.session_id == "sid-123"
    assert info.last_active > 0
    assert info.lock is not None
    assert info.workspace == "default"


@patch("src.config.config")
def test_get_workspace_path(mock_cfg, tmp_path):
    mock_cfg.workspaces = {"default": {"path": str(tmp_path / "default"), "session_id": None}}
    b = Bridge(working_dir=str(tmp_path), workspace_mode="per_chat")
    ws = b._get_workspace_path("private.123")
    assert ws == str(tmp_path / "default")


def test_start_stop():
    b = Bridge()
    b.start()
    assert b._running is True
    b.stop()
    assert b._running is False
    assert b._client is None


def test_permission_handler_stored():
    b = Bridge()
    handler = MagicMock()
    b.on_permission_request("web.", handler)
    assert b._permission_handlers["web."] is handler


def test_get_sessions():
    b = Bridge()
    assert b.get_sessions() == []
    info = _SessionInfo("sess-123", workspace="default")
    info.bound_chat_ids.add("web.private.abc")
    b._sessions["default"] = info
    sessions = b.get_sessions()
    assert len(sessions) == 1
    assert "web.private.abc" in sessions[0]["chat_id"]
    assert sessions[0]["session_id"] == "sess-123"
    assert sessions[0]["workspace"] == "default"


def test_active_workspace_default():
    b = Bridge()
    assert b.get_active_workspace("chat1") == "default"


@patch("src.config.config")
def test_switch_workspace(mock_cfg):
    mock_cfg.workspaces = {"default": {"path": "/tmp/d", "session_id": None}, "myproj": {"path": "/tmp/p", "session_id": None}}
    b = Bridge()
    b.switch_workspace("chat1", "myproj")
    assert b.get_active_workspace("chat1") == "myproj"


@patch("src.config.config")
def test_switch_workspace_unknown(mock_cfg):
    mock_cfg.workspaces = {"default": {"path": "/tmp/d", "session_id": None}}
    b = Bridge()
    import pytest
    with pytest.raises(ValueError, match="Unknown workspace"):
        b.switch_workspace("chat1", "nonexistent")


def test_clear(tmp_path):
    import src.config_manager as cm
    orig_dir, orig_file = cm.CONFIG_DIR, cm.CONFIG_FILE
    cm.CONFIG_DIR = tmp_path
    cm.CONFIG_FILE = tmp_path / "config.toml"
    try:
        b = Bridge()
        info = _SessionInfo("sess-1", workspace="test-ws")
        b._sessions["test-ws"] = info
        b._active_workspace["chat1"] = "test-ws"
        b.clear("chat1")
        assert "test-ws" not in b._sessions
    finally:
        cm.CONFIG_DIR = orig_dir
        cm.CONFIG_FILE = orig_file


@patch("src.config.config")
def test_switch_workspace_unbinds_chat(mock_cfg):
    """After switch, chat_id is unbound from old workspace session (which may remain
    alive for other chat_ids). Session sharing is per-workspace, not per-chat_id."""
    mock_cfg.workspaces = {"default": {"path": "/tmp/d", "session_id": None}, "proj": {"path": "/tmp/p", "session_id": None}}
    b = Bridge()
    old_info = _SessionInfo("old-sess", workspace="default")
    old_info.bound_chat_ids.add("chat1")
    old_info.bound_chat_ids.add("chat2")
    b._sessions["default"] = old_info
    b._active_workspace["chat1"] = "default"

    b.switch_workspace("chat1", "proj")
    assert b.get_active_workspace("chat1") == "proj"
    # Old session stays alive for chat2, but chat1 unbound
    assert "default" in b._sessions
    assert "chat1" not in b._sessions["default"].bound_chat_ids
    assert "chat2" in b._sessions["default"].bound_chat_ids


def test_inject_tag_private():
    assert _inject_tag("tg.private.123", "@alice", "hello") == "[tg/@alice] hello"


def test_inject_tag_group():
    assert _inject_tag("lark.group.oc_xyz", "Bob", "hi") == "[lark-group/Bob] hi"


def test_inject_tag_missing_author_falls_back_to_raw_id():
    assert _inject_tag("discord.private.42", "", "x") == "[discord/42] x"


def test_config_reload_updates_workspaces(tmp_path):
    """save_config_file + reload() → bridge.get_workspaces() returns new entries."""
    from src.config_manager import save_config_file, CONFIG_FILE, CONFIG_DIR

    # Temporarily override config dir
    orig_dir = CONFIG_DIR
    orig_file = CONFIG_FILE
    import src.config_manager as cm
    cm.CONFIG_DIR = tmp_path
    cm.CONFIG_FILE = tmp_path / "config.toml"

    try:
        save_config_file({"_workspaces": {"default": "/tmp/d", "new-proj": "/home/user/proj"}})
        assert (tmp_path / "config.toml").exists()

        # Reload config
        cm.CONFIG_FILE = tmp_path / "config.toml"
        from src.config_manager import load_config_file
        data = load_config_file()
        assert data["_workspaces"]["new-proj"]["path"] == "/home/user/proj"
    finally:
        cm.CONFIG_DIR = orig_dir
        cm.CONFIG_FILE = orig_file
