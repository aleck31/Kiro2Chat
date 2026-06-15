"""Web dashboard authentication — Cognito OIDC (Hosted UI, Authorization Code).

Enabled via the `[auth]` config section. When on, `AuthMiddleware` gates every
page route: unauthenticated requests are redirected to Cognito's Hosted UI. A
successful callback validates the id_token and stores the user's identity in
NiceGUI's signed per-browser storage (`app.storage.user`).

Authlib needs Starlette's SessionMiddleware to carry the transient OAuth
`state`/`nonce` across the redirect; the persisted login lives in
`app.storage.user` so page handlers can read it too.
"""
import logging

from authlib.integrations.starlette_client import OAuth, OAuthError
from nicegui import app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

from ..config import config

log = logging.getLogger(__name__)

# Key under app.storage.user holding the logged-in user dict (or None).
SESSION_USER_KEY = "k2c_user"
# Where the user wanted to go before being bounced to login.
REFERRER_KEY = "k2c_referrer"

# Path prefixes reachable without authentication: the auth dance itself,
# static assets, and NiceGUI's internal HTTP/WS endpoints.
_PUBLIC_PREFIXES = ("/auth/", "/static", "/_nicegui")
_PUBLIC_EXACT = ("/favicon.ico", "/robots.txt")

_oauth: OAuth | None = None


def _get_oauth() -> OAuth:
    """Lazily build the authlib OAuth registry (uses OIDC discovery)."""
    global _oauth
    if _oauth is None:
        oauth = OAuth()
        oauth.register(
            name="cognito",
            server_metadata_url=config.auth.metadata_url,
            client_id=config.auth.cognito_client_id,
            client_secret=config.auth.cognito_client_secret,
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
    return _oauth


def _redirect_uri() -> str:
    return config.auth.base_url.rstrip("/") + "/auth/callback"


def _is_allowed(email: str) -> bool:
    """True if the email may sign in. Empty allowlist = any pool user."""
    allow = [e.lower() for e in config.auth.allowed_emails]
    return True if not allow else (email or "").lower() in allow


def current_user() -> dict | None:
    """The logged-in user dict ({sub,email,name}) or None. Safe anywhere."""
    try:
        return app.storage.user.get(SESSION_USER_KEY)
    except Exception:
        return None


def _is_public(path: str) -> bool:
    return (
        path in _PUBLIC_EXACT
        or any(path.startswith(p) for p in _PUBLIC_PREFIXES)
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated requests for gated pages to the login flow."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_public(path) or current_user():
            return await call_next(request)
        # Remember the target so we can return there after login.
        try:
            app.storage.user[REFERRER_KEY] = path
        except Exception:
            pass
        return RedirectResponse("/auth/login")


async def _login(request: Request):
    return await _get_oauth().cognito.authorize_redirect(request, _redirect_uri())


async def _callback(request: Request):
    try:
        token = await _get_oauth().cognito.authorize_access_token(request)
    except OAuthError as e:
        log.warning("OIDC callback failed: %s", e)
        return PlainTextResponse(f"Login failed: {e.error}", status_code=401)

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email", "")
    if not _is_allowed(email):
        log.warning("Denied login for non-allowlisted email: %s", email)
        return PlainTextResponse(
            "Access denied: your account is not permitted to use this dashboard.",
            status_code=403,
        )

    app.storage.user[SESSION_USER_KEY] = {
        "sub": userinfo.get("sub", ""),
        "email": email,
        "username": userinfo.get("cognito:username", ""),
        "name": userinfo.get("name") or userinfo.get("cognito:username") or email,
    }
    dest = app.storage.user.pop(REFERRER_KEY, "/") or "/"
    log.info("User signed in: %s", email or userinfo.get("sub", "?"))
    return RedirectResponse(dest)


async def _logout(request: Request):
    app.storage.user[SESSION_USER_KEY] = None
    # End the Cognito Hosted UI session too, then return to the dashboard root.
    logout_url = (
        f"{config.auth.hosted_ui_base}/logout"
        f"?client_id={config.auth.cognito_client_id}"
        f"&logout_uri={config.auth.base_url.rstrip('/')}/"
    )
    return RedirectResponse(logout_url)


def _validate_config() -> None:
    a = config.auth
    missing = [
        name for name, val in (
            ("cognito_region", a.cognito_region),
            ("cognito_user_pool_id", a.cognito_user_pool_id),
            ("cognito_client_id", a.cognito_client_id),
            ("cognito_client_secret", a.cognito_client_secret),
            ("cognito_domain", a.cognito_domain),
            ("base_url", a.base_url),
        ) if not val
    ]
    if missing:
        raise RuntimeError(
            "auth.enabled is true but these [auth] settings are missing: "
            + ", ".join(missing)
        )


def register() -> bool:
    """Wire SessionMiddleware, auth routes and the gating middleware.

    Returns True if auth was enabled and wired, False if disabled (no-op).
    Must be called before `ui.run()`.
    """
    if not config.auth.enabled:
        return False

    _validate_config()
    _get_oauth()  # fail fast if discovery/registration is misconfigured

    # NiceGUI installs its own Starlette SessionMiddleware (driven by `storage_secret` in ui.run) as the OUTERMOST middleware. 
    # We deliberately do NOT add a second one.
    app.get("/auth/login")(_login)
    app.get("/auth/callback")(_callback)
    app.get("/auth/logout")(_logout)

    app.add_middleware(AuthMiddleware)
    log.info("Web auth enabled (Cognito OIDC); base_url=%s", config.auth.base_url)
    return True
