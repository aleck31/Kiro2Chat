"""Tests for the Cognito OIDC auth helpers (pure logic, no network)."""
from src.config import AuthConfig, config
from src.webui import auth


def test_authconfig_urls():
    a = AuthConfig()
    a.cognito_region = "ap-southeast-1"
    a.cognito_user_pool_id = "ap-southeast-1_ABC"
    a.cognito_domain = "mydomain"
    assert a.issuer == (
        "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_ABC"
    )
    assert a.metadata_url == a.issuer + "/.well-known/openid-configuration"
    assert a.hosted_ui_base == (
        "https://mydomain.auth.ap-southeast-1.amazoncognito.com"
    )


def test_is_public_paths():
    for p in ("/auth/login", "/auth/callback", "/static/avatar.svg",
              "/_nicegui/abc", "/favicon.ico", "/robots.txt"):
        assert auth._is_public(p), p
    for p in ("/", "/settings", "/chat", "/sessions"):
        assert not auth._is_public(p), p


def test_is_allowed_empty_allows_any(monkeypatch):
    monkeypatch.setattr(config.auth, "allowed_emails", [])
    assert auth._is_allowed("anyone@example.com")
    assert auth._is_allowed("")  # empty allowlist => no restriction


def test_is_allowed_enforced_case_insensitive(monkeypatch):
    monkeypatch.setattr(config.auth, "allowed_emails", ["Me@Example.com", "you@x.io"])
    assert auth._is_allowed("me@example.com")
    assert auth._is_allowed("YOU@X.IO")
    assert not auth._is_allowed("stranger@example.com")
    assert not auth._is_allowed("")


def test_redirect_uri_strips_trailing_slash(monkeypatch):
    monkeypatch.setattr(config.auth, "base_url", "https://kiro.myners.net/")
    assert auth._redirect_uri() == "https://kiro.myners.net/auth/callback"
    monkeypatch.setattr(config.auth, "base_url", "http://localhost:7860")
    assert auth._redirect_uri() == "http://localhost:7860/auth/callback"


def test_register_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config.auth, "enabled", False)
    assert auth.register() is False
