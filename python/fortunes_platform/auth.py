"""Identity policy — the mode arbiter (auth-track Step 4; design §4.3).

Three modes, chosen by the `auth:` config section (file > DB/env):

- **legacy** (no `auth:` section at all): exactly the pre-OIDC behaviour —
  the proxy header is trusted from anywhere, an absent header falls back to
  the shared `local` user. Existing single-user and forward-auth installs
  keep working untouched.
- **header** (`auth.mode: header`): hardened proxy auth — the header is
  honored only when trusted_header.enabled AND the immediate client IP is
  inside the trusted_header.proxies CIDR allowlist (no default). No
  anonymous fallback.
- **oidc** (`auth.mode: oidc`): the app runs its own OIDC login (oidc.py);
  identity comes from the session cookie. The hardened header path may be
  enabled alongside as an alternative surface.

This module stays free of top-level database imports (sessions/users are
resolved by the request dependency in api/app.py); it owns the pure policy:
mode selection, header trust, sanitization, and the cookie contract.

The session cookie is a plain host-only cookie (NOT `__Host-` — that prefix
requires Secure, and LAN access is plain http). Host-only already keeps it
off sibling subdomains; `Secure` is added automatically on https requests.
"""

import ipaddress
import re

from fastapi import Request

from fortunes_platform import config as cfgfile, host

# The app's environment is read when a value is needed, not at import, so the
# app's name and prefix (fortunes_platform.host) are known by then and a test
# can change a variable without reloading anything.


def auth_header() -> str:
    """The proxy header that carries the username (`<prefix>AUTH_HEADER`)."""
    return host.env("AUTH_HEADER", "x-authentik-username")


def fallback_user() -> str:
    """Who a request with no identity is in legacy mode (`<prefix>FALLBACK_USER`)."""
    return host.env("FALLBACK_USER", "local")


def cookie_name() -> str:
    """The session cookie: `<id>_session`, e.g. `iching_session`."""
    return f"{host.current().id}_session"


def logout_url() -> str:
    """Where the logout button sends the browser in legacy mode
    (`<prefix>LOGOUT_URL`). authentik's forward-auth outpost serves its
    sign-out endpoint under the protected domain by default. In oidc mode the
    SPA calls POST /auth/logout instead."""
    return host.env("LOGOUT_URL", "/outpost.goauthentik.io/sign_out")


def admin_users() -> set[str]:
    """Env-seeded admins (`<prefix>ADMIN_USERS`, comma-separated; the fallback
    user by default): consulted only when creating a user row (users.touch)
    and by the _m9 migration sweep. Request-time admin checks read the
    registry (users.is_admin) — flip flags in-app, not here."""
    return {
        u.strip().lower()
        for u in host.env("ADMIN_USERS", fallback_user()).split(",")
        if u.strip()
    }


def env_is_admin(user: str) -> bool:
    return user in admin_users()


LEGACY, HEADER, OIDC = "legacy", "header", "oidc"


def mode() -> str:
    """Resolved auth mode. Config file wins; DB setting next; env last; an
    absent/unknown value means legacy (full back-compat)."""
    from fortunes_platform.host import db

    m = cfgfile.get("auth", "mode")
    if m is None:
        m = db.get_setting("auth_mode") or host.env("AUTH_MODE")
    m = str(m).strip().lower()
    return m if m in (HEADER, OIDC) else LEGACY


def session_days() -> int:
    try:
        return max(1, int(cfgfile.get("auth", "session_days", 90)))
    except (TypeError, ValueError):
        return 90


def auto_provision() -> bool:
    """JIT user provisioning (default ON — the IdP's access policy is the
    real gate; turn off to require pre-created users)."""
    v = cfgfile.get("auth", "auto_provision")
    if v is None:
        from fortunes_platform.host import db

        v = db.get_setting("auth_auto_provision") or None
    if v is None:
        return True
    return str(v).strip().lower() not in ("false", "0", "no", "off")


def _trusted_header_cfg() -> dict:
    v = cfgfile.get("auth", "trusted_header")
    return v if isinstance(v, dict) else {}


def header_trusted(request: Request) -> bool:
    """May this request's proxy header be believed?

    legacy mode: always (the historical contract).
    header/oidc: only when explicitly enabled AND the immediate peer is in
    the CIDR allowlist — an empty list trusts no one."""
    if mode() == LEGACY:
        return True
    th = _trusted_header_cfg()
    if not th.get("enabled"):
        return False
    client = request.client.host if request.client else ""
    if not client:
        return False
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        return False
    for cidr in th.get("proxies") or []:
        try:
            if ip in ipaddress.ip_network(str(cidr), strict=False):
                return True
        except ValueError:
            continue
    return False


_SAFE = re.compile(r"[^a-z0-9._-]")


def sanitize_identity(raw: str) -> str:
    """The stable storage identity for a raw IdP/header username.

    Byte-for-byte the historical transform for any normal username (every
    owner string in the DB and on disk was produced by it), with one guard:
    the result is used directly as a per-user directory segment, so a value
    that is reserved as a path name must never pass. `.` and `..` survive the
    character filter (dots are allowed in real usernames), and `..` would make
    a per-user directory resolve to the data root itself (or to a parent of a
    shared directory), so deleting such a user would remove everything. A
    result made only of dots (or empty) collapses to a safe sentinel instead. Only those are remapped:
    names that sanitize to underscores (a username in a non-Latin script) or
    dashes are ordinary directory names and keep their historical identity."""
    out = _SAFE.sub("_", raw.lower())[:64]
    if set(out) <= {"."}:
        return "_"
    return out


def _raw_identity(request: Request) -> str:
    return request.headers.get(auth_header(), "").strip()


def current_user(request: Request) -> str:
    """Header-derived storage identity (legacy/header modes)."""
    raw = _raw_identity(request).lower()
    if not raw:
        return fallback_user()
    return sanitize_identity(raw)


def is_authenticated(request: Request) -> bool:
    """True when a real identity backs this request (header supplied, or a
    session cookie is present — the dependency has already validated it by
    the time anyone asks)."""
    return bool(_raw_identity(request)) or bool(request.cookies.get(cookie_name()))


def display_name_from(raw: str) -> str:
    """Friendly name for a raw identity: emails collapse to their local part
    so 'someone@example.com' shows as 'someone'."""
    if not raw:
        return fallback_user()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    return raw[:64]


def display_name(request: Request) -> str:
    """Friendly name from the request header. Does not affect the storage
    identity; a self-chosen display name (user_settings override) wins over
    this everywhere names are shown."""
    return display_name_from(_raw_identity(request))
