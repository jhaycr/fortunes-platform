"""OIDC relying party — /auth/* routes (auth-track Step 4; design §4.2).

Authlib runs the authorization-code + PKCE flow against the configured
issuer (authentik, for one, but any OIDC provider works). All
tokens stay server-side: the browser only ever gets the opaque session
cookie. Starlette's SessionMiddleware (mounted in app.py with a per-boot
secret and a short max_age) holds nothing but the handshake state/nonce
and the post-login destination.

Config resolution follows the house rule (file > admin UI/DB > env):

    auth:
      mode: oidc
      issuer: https://auth.example.com/application/o/<app>/
      client_id: <app>
      client_secret_env: <prefix>OIDC_CLIENT_SECRET   # or client_secret:
      auto_provision: true      # JIT user rows (default on)
      session_days: 90

Identity = the `preferred_username` claim run through the EXACT historical
sanitizer (auth.sanitize_identity) — byte-for-byte continuity with every
owner string the header era wrote to the DB and disk.
"""

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from fortunes_platform import auth, config as cfgfile, host, sessions, users

router = APIRouter()

_oauth = None
_oauth_key: tuple | None = None


def oidc_config() -> dict | None:
    """Resolved issuer/client_id/client_secret, or None when not configured."""
    from fortunes_platform import crypto
    from fortunes_platform import db

    issuer = str(
        cfgfile.get("auth", "issuer")
        or db.get_setting("auth_issuer")
        or host.env("OIDC_ISSUER")
    ).strip().rstrip("/")
    client_id = str(
        cfgfile.get("auth", "client_id")
        or db.get_setting("auth_client_id")
        or host.env("OIDC_CLIENT_ID")
    ).strip()
    secret = cfgfile.auth_client_secret()
    if secret is None:
        stored = db.get_setting("auth_client_secret")
        secret = crypto.decrypt(stored) if stored else host.env("OIDC_CLIENT_SECRET")
    if not issuer or not client_id:
        return None
    return {"issuer": issuer, "client_id": client_id, "client_secret": secret or ""}


def _client():
    """The Authlib client, rebuilt whenever the resolved config changes."""
    global _oauth, _oauth_key
    from authlib.integrations.starlette_client import OAuth

    cfg = oidc_config()
    if cfg is None:
        raise HTTPException(404, "OIDC sign-in is not configured")
    key = (cfg["issuer"], cfg["client_id"], cfg["client_secret"])
    if _oauth is None or _oauth_key != key:
        oauth = OAuth()
        oauth.register(
            "idp",
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            server_metadata_url=f"{cfg['issuer']}/.well-known/openid-configuration",
            client_kwargs={"scope": "openid profile email",
                           "code_challenge_method": "S256"},
        )
        _oauth, _oauth_key = oauth, key
    return _oauth.idp


def _safe_next(next_url: str | None) -> str:
    """Same-origin paths only — no open redirect through the login flow."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@router.get("/auth/login")
async def login(request: Request, next: str | None = None):
    client = _client()
    request.session["next"] = _safe_next(next)
    redirect_uri = str(request.url.replace(path="/auth/callback", query=""))
    try:
        return await client.authorize_redirect(request, redirect_uri)
    except Exception as e:
        raise HTTPException(502, f"sign-in provider unreachable: {e}")


_RETRY_PAGE = """<!doctype html><meta charset="utf-8"><title>Sign-in problem</title>
<body style="font-family:system-ui;max-width:30rem;margin:4rem auto;text-align:center">
<h1>{title}</h1><p>{message}</p><p><a href="/auth/login">Try signing in again</a></p></body>"""


@router.get("/auth/callback")
async def callback(request: Request):
    client = _client()
    try:
        token = await client.authorize_access_token(request)
    except Exception:
        return HTMLResponse(_RETRY_PAGE.format(
            title="Sign-in expired", message="The sign-in attempt expired or was invalid."),
            status_code=400)
    claims = token.get("userinfo") or {}
    raw = str(claims.get("preferred_username") or claims.get("email") or "").strip()
    if not raw:
        return HTMLResponse(_RETRY_PAGE.format(
            title="Sign-in failed", message="The identity provider sent no usable username."),
            status_code=403)
    user = auth.sanitize_identity(raw.lower())
    if not auth.auto_provision() and users.get(user) is None:
        return HTMLResponse(_RETRY_PAGE.format(
            title="Account not provisioned",
            message="Your sign-in worked, but this account hasn't been set up here yet — ask your admin."),
            status_code=403)
    # Synchronous on purpose: the session row references users(username).
    users.touch_sync(user, auth.display_name_from(raw))
    days = auth.session_days()
    cookie = sessions.create(user, id_token=token.get("id_token"), session_days=days)
    resp = RedirectResponse(request.session.pop("next", "/") or "/", status_code=303)
    resp.set_cookie(
        auth.cookie_name(), cookie,
        max_age=days * 86400, httponly=True, samesite="lax",
        secure=request.url.scheme == "https", path="/",
    )
    return resp


@router.post("/auth/logout")
async def logout(request: Request):
    """Destroy the app session and hand the SPA the IdP's end-session URL
    (best effort — the browser navigates there to also end the SSO session)."""
    token = request.cookies.get(auth.cookie_name(), "")
    end_url = None
    if token:
        id_token = sessions.id_token_for(token)
        sessions.destroy(token)
        try:
            client = _client()
            meta = await client.load_server_metadata()
            end = meta.get("end_session_endpoint")
            if end:
                base = str(request.url.replace(path="/", query=""))
                end_url = f"{end}?post_logout_redirect_uri={base}"
                if id_token:
                    end_url += f"&id_token_hint={id_token}"
        except Exception:
            end_url = None
    resp = JSONResponse({"logout_url": end_url or "/"})
    resp.delete_cookie(auth.cookie_name(), path="/")
    return resp


# --- first-admin bootstrap (design §4.3) ------------------------------------
# Regenerated each boot while no active admin exists; printed to the log;
# single-use. Promotion applies to the CURRENTLY SIGNED-IN user.

_setup_token: str | None = None
_setup_token_born: float = 0


def local_becomes_admin() -> bool:
    """Will an admin appear without the token? In legacy mode anyone who
    opens the app without a username header IS the shared `local` user, and
    a user listed in `<prefix>ADMIN_USERS` (`local` is the default) is made admin
    when its registry row is created, on that first visit. So a single-user
    install that nobody has opened yet is not adminless in any way that
    needs the setup token."""
    return (auth.mode() == auth.LEGACY
            and auth.env_is_admin(auth.fallback_user())
            and users.get(auth.fallback_user()) is None)


def ensure_setup_token(on_request: bool = False) -> None:
    """Issue (and log) the one-time setup token while no admin exists.

    At boot the token is skipped when `local` is about to become admin by
    itself (local_becomes_admin), so a plain install does not log a setup
    step it will never need. `on_request` is the /auth/setup page asking:
    whoever opened it is signed in and wants the token, so it is issued
    whenever no admin exists yet."""
    global _setup_token, _setup_token_born
    import secrets

    if users.active_admin_exists() or (not on_request and local_becomes_admin()):
        _setup_token = None
        return
    if _setup_token is None:
        _setup_token = secrets.token_urlsafe(24)
        _setup_token_born = time.time()
        print(f"{host.current().id.upper()} SETUP: no admin exists — sign in, then visit /auth/setup "
              f"and enter: {_setup_token}", flush=True)


def consume_setup_token(supplied: str, user: str) -> bool:
    global _setup_token
    if not _setup_token or not supplied or supplied != _setup_token:
        return False
    users.flush_touches()
    users.set_admin(user, True)
    _setup_token = None
    return True
