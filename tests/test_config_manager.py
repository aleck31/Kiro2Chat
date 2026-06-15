"""Tests for config_manager TOML serialization, focused on key quoting.

Regression: a workspace name with a space produced an invalid `[workspaces.<name>]`
header (`Expected ']'`), crashing the whole service on next load.
"""
import tomllib

import pytest

from src import config_manager as cm


@pytest.mark.parametrize(
    "key,expected",
    [
        ("default", "default"),
        ("my-bot", "my-bot"),
        ("my_bot", "my_bot"),
        ("kiro2chat", "kiro2chat"),
        ("ubuntu os", '"ubuntu os"'),       # space → quoted
        ("a.b", '"a.b"'),                    # dot → quoted (would nest otherwise)
        ('he"llo', '"he\\"llo"'),            # quote → escaped
        ("back\\slash", '"back\\\\slash"'),  # backslash → escaped
        ("日本語", '"日本語"'),                # non-ascii → quoted
    ],
)
def test_fmt_key(key, expected):
    assert cm._fmt_key(key) == expected


def test_save_load_roundtrip_with_spaced_name(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm, "CONFIG_FILE", cfg)

    sections = {
        "telegram": {"enabled": True, "bot_token": "abc"},
        "_workspaces": {
            "default": {"path": "/home/u/d", "session_id": "sid-1"},
            "ubuntu os": {"path": "/home/u", "session_id": "sid-2"},
        },
    }
    cm.save_config_file(sections)

    # The raw file must be valid TOML (this is what previously crashed).
    raw = cfg.read_text()
    assert '[workspaces."ubuntu os"]' in raw
    tomllib.loads(raw)  # raises if invalid

    loaded = cm.load_config_file()
    ws = loaded["_workspaces"]
    assert ws["ubuntu os"]["path"] == "/home/u"
    assert ws["ubuntu os"]["session_id"] == "sid-2"
    assert ws["default"]["session_id"] == "sid-1"
