"""Tests for AdapterManager."""

from unittest.mock import MagicMock, patch
from src.manager import AdapterManager


def test_init_all_unconfigured():
    m = AdapterManager()
    bridge = MagicMock()
    with patch("src.manager.AdapterManager._detect_configured"):
        m.init(bridge)
    assert m.bridge is bridge
    assert set(m._adapters.keys()) == {"telegram", "lark", "discord"}
    for s in m._adapters.values():
        assert s.status == "unconfigured"


def test_detect_configured():
    m = AdapterManager()
    bridge = MagicMock()
    with patch("src.config.config") as cfg:
        cfg.telegram.bot_token = "tok123"
        cfg.lark.app_id = ""
        cfg.lark.app_secret = ""
        cfg.discord.bot_token = "disc456"
        m.init(bridge)
    assert m._adapters["telegram"].status == "stopped"
    assert m._adapters["lark"].status == "unconfigured"
    assert m._adapters["discord"].status == "stopped"


def test_get_states():
    m = AdapterManager()
    bridge = MagicMock()
    with patch("src.manager.AdapterManager._detect_configured"):
        m.init(bridge)
    states = m.get_states()
    assert "telegram" in states
    assert states["telegram"]["status"] == "unconfigured"
    assert states["telegram"]["uptime"] == 0


def test_start_unconfigured_raises():
    m = AdapterManager()
    bridge = MagicMock()
    with patch("src.manager.AdapterManager._detect_configured"):
        m.init(bridge)
    import pytest
    with pytest.raises(ValueError, match="not configured"):
        m.start_adapter("telegram")


def test_stop_not_running():
    m = AdapterManager()
    bridge = MagicMock()
    with patch("src.manager.AdapterManager._detect_configured"):
        m.init(bridge)
    # Should not raise
    m.stop_adapter("telegram")
