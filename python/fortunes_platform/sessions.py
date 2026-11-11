"""App-local login sessions (auth-track Step 3; design §4.1).

A session is an opaque 256-bit token handed to the browser as the
`__Host-session` cookie. The DB stores only the token's sha256, so a
database leak yields nothing a browser can replay. Expiry is sliding
(`session_days`, default 90) but refreshed at most once a day per session
— one bookkeeping write per device per day, not per request.

The encrypted id_token rides along solely for RP-initiated logout
(authentik's end_session wants an id_token_hint); access/refresh tokens
are never stored — OIDC is authentication only.
"""

import hashlib
import secrets
import time

from fortunes_platform import crypto
from fortunes_platform import db

DEFAULT_SESSION_DAYS = 90
# Refresh the sliding expiry only when the stored last_seen is older than
# this — keeps the common path read-only.
REFRESH_AFTER = 86400


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(username: str, id_token: str | None = None,
           session_days: int = DEFAULT_SESSION_DAYS) -> str:
    """New session for `username`; returns the cookie value (the only time
    the raw token ever exists outside the browser)."""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db.connect() as con:
        con.execute(
            "INSERT INTO sessions (token_hash, username, created_at, last_seen_at, expires_at,"
            " id_token) VALUES (?,?,?,?,?,?)",
            (_hash(token), username, now, now, now + session_days * 86400,
             crypto.encrypt(id_token) if id_token else None),
        )
    return token


def resolve(token: str, session_days: int = DEFAULT_SESSION_DAYS) -> str | None:
    """Username for a live session cookie, or None. Joins users.active so a
    deactivated account's sessions die with it. Slides the expiry window
    forward (throttled to daily)."""
    if not token:
        return None
    now = int(time.time())
    h = _hash(token)
    with db.connect() as con:
        row = con.execute(
            "SELECT s.username, s.last_seen_at FROM sessions s"
            " JOIN users u ON u.username = s.username"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
            (h, now),
        ).fetchone()
        if row is None:
            return None
        if now - row["last_seen_at"] >= REFRESH_AFTER:
            con.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                (now, now + session_days * 86400, h),
            )
    return row["username"]


def id_token_for(token: str) -> str | None:
    """The (decrypted) id_token of a session, for logout's id_token_hint."""
    with db.connect() as con:
        row = con.execute(
            "SELECT id_token FROM sessions WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
    if row is None or not row["id_token"]:
        return None
    try:
        return crypto.decrypt(row["id_token"])
    except Exception:
        return None


def destroy(token: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def destroy_all(username: str) -> int:
    """Kill every session of one user (deactivation, delete-my-data)."""
    with db.connect() as con:
        cur = con.execute("DELETE FROM sessions WHERE username = ?", (username,))
        return cur.rowcount


def reap() -> int:
    """Drop expired sessions; called at startup and daily."""
    with db.connect() as con:
        cur = con.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        return cur.rowcount
