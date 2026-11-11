"""User registry — the app's record of who exists.

Identity itself still comes from the proxy header (see auth.py); this is a
projection of it, never an authentication source. Rows appear on first sight, so
the registry is complete without anyone provisioning it: authentik's group
membership is already the gate on who can reach the app at all.

Before this existed, "who is a user" was answered three unconnected ways — the
request header, `readings.owner` strings, and directories under data/users/ —
and a username existed only as a side effect of having saved something.
"""

import queue
import threading
import time

from fortunes_platform import db
from fortunes_platform.auth import env_is_admin, fallback_user

# Refresh last_seen at most this often, so ordinary browsing doesn't write on
# every request.
TOUCH_INTERVAL = 300

KIND_PERSON = "person"
KIND_SYSTEM = "system"


def kind_for(username: str) -> str:
    """`local` is the shared header-absent LAN identity, not a person. It owns
    real data but can never be a meaningful share recipient."""
    return KIND_SYSTEM if username == fallback_user() else KIND_PERSON


def _row(r) -> dict:
    d = dict(r)
    d["active"] = bool(d["active"])
    d["is_admin"] = bool(d["is_admin"])
    return d


# touch() is BEST-EFFORT and asynchronous: the 2026-08-03 storage stall on
# the host hung every endpoint on this per-request bookkeeping write while the
# disk was unresponsive. A single daemon worker drains a small queue; when
# the queue is full or the write fails, the touch is simply dropped —
# last-seen freshness is advisory and never worth availability.
_touch_queue: "queue.Queue[tuple[str, str | None]]" = queue.Queue(maxsize=256)
_touch_worker_started = False
_touch_worker_guard = threading.Lock()


def _touch_worker() -> None:
    while True:
        username, display = _touch_queue.get()
        try:
            touch_sync(username, display)
        except Exception:
            pass
        finally:
            _touch_queue.task_done()


def touch(username: str, display: str | None = None) -> None:
    """Queue a presence update; never blocks and never raises."""
    global _touch_worker_started
    if not _touch_worker_started:
        with _touch_worker_guard:
            if not _touch_worker_started:
                threading.Thread(target=_touch_worker, name="users-touch", daemon=True).start()
                _touch_worker_started = True
    try:
        _touch_queue.put_nowait((username, display))
    except queue.Full:
        pass


def flush_touches() -> None:
    """Block until queued touches are applied — for tests and callers that
    need read-your-write on the registry."""
    _touch_queue.join()


def touch_sync(username: str, display: str | None = None) -> None:
    """Record that `username` is here. Cheap on the common path: a primary-key
    read, and a write only when the row is new, stale, or renamed.

    display=None means "no identity source on this request" (cookie-session
    traffic carries no header) — it must only refresh last_seen, never
    rewrite display_name, or every session request would clobber the name
    learned at login back to the bare username."""
    now = int(time.time())
    with db.connect() as con:
        row = con.execute(
            "SELECT display_name, last_seen FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            name = (display or username)[:64]
            con.execute(
                "INSERT INTO users (username, display_name, kind, active, is_admin,"
                " first_seen, last_seen) VALUES (?,?,?,1,?,?,?)"
                " ON CONFLICT(username) DO NOTHING",
                (username, name, kind_for(username), int(env_is_admin(username)), now, now),
            )
            return
        renamed = display is not None and row["display_name"] != display[:64]
        if now - row["last_seen"] >= TOUCH_INTERVAL or renamed:
            con.execute(
                "UPDATE users SET last_seen = ?, display_name = ? WHERE username = ?",
                (now, (display[:64] if display is not None else row["display_name"]), username),
            )


def get(username: str) -> dict | None:
    with db.connect() as con:
        row = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _row(row) if row else None


def is_admin(username: str) -> bool:
    """Admin is DATA (users.is_admin), managed in-app. The `<prefix>ADMIN_USERS`
    env var only seeds new rows (and the _m9 sweep) — it is not consulted at
    request time, so revoking admin in the UI actually revokes it. Falls back
    to the env seed for identities whose row hasn't landed yet (the touch
    queue is async)."""
    row = get(username)
    if row is None:
        return env_is_admin(username)
    return row["is_admin"] and row["active"]


def active_admin_exists() -> bool:
    with db.connect() as con:
        return con.execute(
            "SELECT 1 FROM users WHERE is_admin = 1 AND active = 1 LIMIT 1"
        ).fetchone() is not None


def other_active_admin_exists(username: str) -> bool:
    """Is there an active admin BESIDES `username`? (last-admin guard)"""
    with db.connect() as con:
        return con.execute(
            "SELECT 1 FROM users WHERE is_admin = 1 AND active = 1 AND username != ? LIMIT 1",
            (username,),
        ).fetchone() is not None


def set_admin(username: str, value: bool) -> dict | None:
    """Toggle the admin flag. Refuses to demote the LAST active admin —
    someone must always hold the keys."""
    if not value and not other_active_admin_exists(username):
        return None
    with db.connect() as con:
        con.execute("UPDATE users SET is_admin = ? WHERE username = ?",
                    (int(value), username))
    return get(username)


def list_people(include_inactive: bool = False) -> list[dict]:
    """People who can be picked as share recipients. Excludes system identities
    (`local`) and, by default, anyone an admin has deactivated."""
    sql = "SELECT * FROM users WHERE kind = ?"
    params: list = [KIND_PERSON]
    if not include_inactive:
        sql += " AND active = 1"
    sql += " ORDER BY display_name COLLATE NOCASE, username"
    with db.connect() as con:
        return [_row(r) for r in con.execute(sql, params)]


def display_names() -> dict[str, str]:
    """{username: the name other people see}. A self-chosen display name
    (the user_settings override) wins over the registry's header- or
    IdP-derived one, the same resolution /api/me and /api/users use.
    Usernames with no row (the "former member" tombstone, a deleted
    account) are absent: callers fall back to the username itself."""
    with db.connect() as con:
        names = {
            r["username"]: r["display_name"] or r["username"]
            for r in con.execute("SELECT username, display_name FROM users")
        }
    names.update({u: n for u, n in db.display_name_overrides().items() if n})
    return names


def list_all() -> list[dict]:
    with db.connect() as con:
        return [
            _row(r)
            for r in con.execute(
                "SELECT * FROM users ORDER BY kind, display_name COLLATE NOCASE, username"
            )
        ]


def is_grantable(username: str) -> bool:
    """Whether a reading may be shared with `username`."""
    u = get(username)
    return bool(u and u["active"] and u["kind"] == KIND_PERSON)


def update(username: str, display_name: str | None = None, active: bool | None = None) -> dict | None:
    """Admin curation: rename, or hide a stale/mistyped entry from pickers."""
    with db.connect() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is None:
            return None
        if display_name is not None:
            con.execute(
                "UPDATE users SET display_name = ? WHERE username = ?",
                (display_name[:64] or username, username),
            )
        if active is not None:
            con.execute(
                "UPDATE users SET active = ? WHERE username = ?", (int(active), username)
            )
        return _row(con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())
