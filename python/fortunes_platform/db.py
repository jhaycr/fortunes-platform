"""The journal every Little Fortunes app keeps, in SQLite (one file, journal.db,
in the app's data directory): readings with their sharing and interpretations,
the user registry and sessions, settings, the AI usage ledger and the reading
limit's charges, and the spoken-audio cache's index.

What differs between apps is their readings' own columns (a tarot reading's
deck and cards, an I Ching cast's lines). The app hands those parts in as a
`Journal` (see `host.Host.journal`): how to read its columns, how many pieces a
reading's interpretation has, what a share list shows, and its own migrations.

Migrations come in two numbered series: the core's, here, and the app's own.
Before the split both apps numbered one series; its first nine steps are the
core's, and an app's later steps are its own, from 1. A database from before
the split is mapped once (see `_adopt_legacy_numbering`).
"""

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from fortunes_platform import host


# What an app re-exports as its own db module (`from fortunes_platform.db import *`).
__all__ = [
    "Journal",
    "SCHEMA",
    "CORE_KEY",
    "APP_KEY",
    "LEGACY_KEY",
    "LEGACY_CORE_STEPS",
    "connect",
    "PRIVATE",
    "SPECIFIC",
    "EVERYONE",
    "VISIBILITIES",
    "POSITION_WHOLE",
    "list_readings",
    "get_reading",
    "update_reading",
    "set_reading_books",
    "set_focused_interpretation",
    "set_comprehensive_interpretation",
    "get_focused_interpretations",
    "set_sharing",
    "shares_granted",
    "shares_received",
    "owned_reading_count",
    "get_interpretation",
    "get_user_setting",
    "set_user_setting",
    "user_settings_all",
    "display_name_overrides",
    "record_usage",
    "readings_charged",
    "reading_charge_exists",
    "try_charge_reading",
    "llm_tokens_since",
    "tts_bytes_since",
    "usage_summary",
    "tts_cache_all",
    "tts_cache_upsert",
    "tts_cache_touch",
    "tts_cache_delete",
    "get_setting",
    "set_setting",
    "delete_reading",
]


@dataclass(frozen=True)
class Journal:
    """The app's own part of the journal."""

    reading: Callable[[dict], dict]
    """Shapes a readings row (as a dict) for the API: parses the app's own
    columns and drops any it does not use."""

    pieces: Callable[[sqlite3.Row], int]
    """How many pieces a reading's guided interpretation reads (a reading of
    one piece is complete when that piece is read)."""

    share_columns: str
    """The app's readings columns a share list shows, comma-separated (they
    name a reading that has no question)."""

    share: Callable[[dict], dict] = lambda d: d
    """Shapes a share-list row from those columns."""

    known_users: Callable[[], Iterable[str]] = lambda: ()
    """Users the app knew of before the user registry (core migration 1)."""

    migrations: Sequence[tuple[int, Callable[[sqlite3.Connection], None]]] = field(default_factory=tuple)
    """The app's own migrations, numbered from 1."""

    legacy_step_3: Callable[[], None] | None = None
    """Core migration 3 was one app's own step on its files (Tarotarium
    published its shared decks); an app that had it hands it in."""


def _journal() -> Journal:
    return host.current().journal()


def data_dir():
    return host.data_dir()


SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    question TEXT,
    deck TEXT NOT NULL,
    spread TEXT NOT NULL,
    cards TEXT NOT NULL,
    notes TEXT DEFAULT '',
    shared INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_readings_owner ON readings(owner, created_at DESC);
CREATE TABLE IF NOT EXISTS user_prompts (
    owner TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# Each series records how far it has run, in app_settings. `schema_version`
# is the one series from before the split. It is read once, to map an old
# database, and then left as it was, so an older build of the app can still
# open the file as long as no newer step has changed it.
CORE_KEY = "core_schema_version"
APP_KEY = "app_schema_version"
LEGACY_KEY = "schema_version"
LEGACY_CORE_STEPS = 9  # the old series' first nine steps are the core's

# (version, migrate_fn). Applied in order, once each, newest last. A migration
# gets a connection in autocommit mode inside an explicit transaction; it must
# use con.execute directly rather than the module's public helpers, which would
# open a second connection and recurse.
def _m1_user_registry(con: sqlite3.Connection) -> None:
    """Create the user registry and backfill it from the three places that
    answered 'who is a user' before it existed.

    The three sources genuinely differ, so they must be unioned rather than
    trusted individually: a reading-only user has no directory, and a directory
    outlives the decks that created it.
    """
    from fortunes_platform.auth import admin_users, fallback_user

    con.execute(
        """
        CREATE TABLE users (
            username     TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            kind         TEXT NOT NULL DEFAULT 'person'
                           CHECK (kind IN ('person','system')),
            active       INTEGER NOT NULL DEFAULT 1,
            is_admin     INTEGER NOT NULL DEFAULT 0,
            first_seen   INTEGER NOT NULL,
            last_seen    INTEGER NOT NULL
        )
        """
    )

    now = int(time.time())
    names: set[str] = set()
    names.update(r[0] for r in con.execute("SELECT DISTINCT owner FROM readings"))
    names.update(r[0] for r in con.execute("SELECT owner FROM user_prompts"))
    names.update(_journal().known_users())

    # Earliest reading is a better first_seen than "now" for anyone who has one.
    earliest = {
        r[0]: r[1]
        for r in con.execute("SELECT owner, MIN(created_at) FROM readings GROUP BY owner")
    }

    for name in sorted(n for n in names if n):
        kind = "system" if name == fallback_user() else "person"
        con.execute(
            "INSERT INTO users (username, display_name, kind, active, is_admin,"
            " first_seen, last_seen) VALUES (?,?,?,1,?,?,?)",
            (name, name, kind, int(name in admin_users()), earliest.get(name, now), now),
        )


def _m2_reading_visibility(con: sqlite3.Connection) -> None:
    """Replace the binary `shared` flag with three-state visibility.

    The mapping is unambiguous because there was no way to be 'specific' before:
    shared=1 meant everyone on the instance, shared=0 meant nobody.
    """
    con.execute(
        "ALTER TABLE readings ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'"
        " CHECK (visibility IN ('private','specific','everyone'))"
    )
    con.execute(
        "UPDATE readings SET visibility = CASE WHEN shared = 1"
        " THEN 'everyone' ELSE 'private' END"
    )
    con.execute("ALTER TABLE readings DROP COLUMN shared")
    con.execute(
        """
        CREATE TABLE reading_shares (
            reading_id INTEGER NOT NULL REFERENCES readings(id) ON DELETE CASCADE,
            grantee    TEXT    NOT NULL REFERENCES users(username),
            granted_at INTEGER NOT NULL,
            PRIMARY KEY (reading_id, grantee)
        )
        """
    )
    # Serves "readings shared with me", which would otherwise scan.
    con.execute(
        "CREATE INDEX idx_reading_shares_grantee ON reading_shares(grantee)"
    )


def _m3_app_step(con: sqlite3.Connection) -> None:
    """Tarotarium published its legacy shared decks into its library here, on
    the filesystem only. Kept so the numbering holds; an app with a step of its
    own at this point hands it in as `Journal.legacy_step_3`."""
    step = _journal().legacy_step_3
    if step is not None:
        step()


def _m4_guided_interpretations(con: sqlite3.Connection) -> None:
    """Persist guided-reading interpretation text.

    Reading-level singletons (which context mode, overall status) live as columns
    on `readings` so the journal list can badge a reading without a join. The
    per-card and whole-spread text lives in a child table, CASCADE-deleted with
    the parent, matching the reading_shares precedent. A focused row exists iff
    that card position has been interpreted, so completion state is intrinsic and
    the flow is resumable.
    """
    con.execute(
        "ALTER TABLE readings ADD COLUMN interpretation_mode TEXT "
        "CHECK (interpretation_mode IS NULL OR "
        "interpretation_mode IN ('isolated','cumulative','single'))"
    )
    con.execute(
        "ALTER TABLE readings ADD COLUMN interpretation_status TEXT "
        "CHECK (interpretation_status IS NULL OR "
        "interpretation_status IN ('in_progress','complete'))"
    )
    con.execute(
        """
        CREATE TABLE reading_interpretations (
            reading_id INTEGER NOT NULL REFERENCES readings(id) ON DELETE CASCADE,
            position   INTEGER NOT NULL,   -- 0-based ordinal into readings.cards;
                                           -- -1 = whole-spread (comprehensive/single)
            kind       TEXT    NOT NULL CHECK (kind IN ('focused','comprehensive')),
            text       TEXT    NOT NULL,
            persona    TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (reading_id, position),
            CHECK ((kind = 'focused'       AND position >= 0) OR
                   (kind = 'comprehensive' AND position = -1))
        )
        """
    )


def _m5_tts(con: sqlite3.Connection) -> None:
    """TTS audio cache index (LRU by last_used_at; files live in
    /data/tts-cache) + a per-user voice block for the custom persona."""
    con.execute(
        """
        CREATE TABLE tts_cache (
            hash         TEXT PRIMARY KEY,
            size_bytes   INTEGER NOT NULL,
            created_at   INTEGER NOT NULL,
            last_used_at INTEGER NOT NULL
        )
        """
    )
    con.execute("ALTER TABLE user_prompts ADD COLUMN voice TEXT")


def _m6_usage_and_user_settings(con: sqlite3.Connection) -> None:
    """Per-user settings k/v (first user: auto_read_audio) and the AI usage
    ledger — one row per paid provider call (LLM or TTS), no FK to readings
    so the record outlives deletion."""
    con.execute(
        """
        CREATE TABLE user_settings (
            owner TEXT NOT NULL,
            key   TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (owner, key)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE ai_usage (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                INTEGER NOT NULL,
            owner             TEXT    NOT NULL,
            component         TEXT    NOT NULL CHECK (component IN ('llm','tts')),
            kind              TEXT    NOT NULL,  -- single|focused|comprehensive|speak
            model             TEXT    NOT NULL,
            reading_id        INTEGER,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            characters        INTEGER,  -- llm: output chars (usage fallback); tts: input chars
            audio_bytes       INTEGER
        )
        """
    )
    con.execute("CREATE INDEX idx_ai_usage_ts ON ai_usage(ts)")


def _m7_reading_charges(con: sqlite3.Connection) -> None:
    """Daily reading-limit charges. One row per (owner, day, fingerprint);
    INSERT OR IGNORE makes charging idempotent, so resuming a guided reading
    or re-interpreting the same quick draw never double-bills."""
    con.execute(
        """
        CREATE TABLE reading_charges (
            owner       TEXT NOT NULL,
            day         TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            created_at  INTEGER NOT NULL,
            PRIMARY KEY (owner, day, fingerprint)
        )
        """
    )


def _m_reading_books(con: sqlite3.Connection) -> None:
    """Provenance: which guidebooks informed a reading's interpretation
    (JSON array of book slugs; [] = none)."""
    con.execute("ALTER TABLE readings ADD COLUMN books TEXT NOT NULL DEFAULT '[]'")


def _m_sessions(con: sqlite3.Connection) -> None:
    """App-local login sessions (auth-track Step 3). Only the sha256 of the
    cookie token is stored; id_token (encrypted) is kept solely for
    RP-initiated logout. Also a one-time is_admin seed sweep: rows created
    before this migration got is_admin from the env var at INSERT time, so
    re-seed from the CURRENT env value to catch users added to
    the app's <prefix>ADMIN_USERS after their row appeared (env var becomes inert once
    admin is managed in-app)."""
    con.execute(
        """
        CREATE TABLE sessions (
            token_hash   TEXT PRIMARY KEY,
            username     TEXT NOT NULL REFERENCES users(username),
            created_at   INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            expires_at   INTEGER NOT NULL,
            id_token     TEXT
        )
        """
    )
    con.execute("CREATE INDEX sessions_user ON sessions(username)")
    admins = [a.strip() for a in host.env("ADMIN_USERS").split(",") if a.strip()]
    for name in admins:
        con.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (name,))


CORE_MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _m1_user_registry),
    (2, _m2_reading_visibility),
    (3, _m3_app_step),
    (4, _m4_guided_interpretations),
    (5, _m5_tts),
    (6, _m6_usage_and_user_settings),
    (7, _m7_reading_charges),
    (8, _m_reading_books),
    (9, _m_sessions),
]


def _version(con: sqlite3.Connection, key: str) -> int:
    row = con.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return -1  # unmarked: never migrated, not even baselined
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _set_version(con: sqlite3.Connection, key: str, version: int) -> None:
    con.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(version)),
    )


def _adopt_legacy_numbering(con: sqlite3.Connection) -> None:
    """Once, for a database from before the split: its one series becomes two.

    Steps 1 to 9 of the old series were the core's, so they map straight
    across; an app's later steps are its own, renumbered from 1 (I Ching's old
    step 10 is its step 1). A database with no old number (a new one, or one
    from before any numbering) runs both series from the start, as before.
    """
    if _version(con, CORE_KEY) >= 0:
        return
    legacy = _version(con, LEGACY_KEY)
    if legacy < 0:
        return
    _set_version(con, CORE_KEY, min(legacy, LEGACY_CORE_STEPS))
    _set_version(con, APP_KEY, max(legacy - LEGACY_CORE_STEPS, 0))
    con.commit()


def _backup(con: sqlite3.Connection, label: str) -> None:
    """Snapshot the DB before a migration alters it.

    Uses the sqlite backup API rather than copying the file: in WAL mode recent
    commits can still be sitting in journal.db-wal, so a plain copy can lose
    them.
    """
    dest = data_dir() / f"journal.db.bak-{label}"
    bak = sqlite3.connect(dest)
    try:
        con.backup(bak)
    finally:
        bak.close()


def _run(con: sqlite3.Connection, key: str, migrations, label: str) -> None:
    """Apply one series' pending steps, in order, each in its own transaction."""
    marked = _version(con, key)
    current = max(marked, 0)
    pending = [(v, fn) for v, fn in migrations if v > current]
    if not pending:
        if marked < 0:
            _set_version(con, key, 0)  # baseline an existing/new DB
            con.commit()
        return

    _backup(con, f"{label}-v{current}")

    # Autocommit so BEGIN/COMMIT are ours: SQLite DDL *is* transactional, but
    # Python's legacy isolation handling won't wrap it for us.
    prior = con.isolation_level
    con.isolation_level = None
    try:
        for version, fn in pending:
            con.execute("BEGIN")
            try:
                fn(con)
                _set_version(con, key, version)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
    finally:
        con.isolation_level = prior


def _migrate(con: sqlite3.Connection) -> None:
    _adopt_legacy_numbering(con)
    _run(con, CORE_KEY, CORE_MIGRATIONS, "core")
    _run(con, APP_KEY, _journal().migrations, "app")


# Schema creation + migration run once per DB per process, under a lock:
# concurrent first connections (startup threads, the async touch worker)
# would otherwise race between reading schema_version and running DDL.
_migrate_lock = threading.Lock()
_migrated_paths: set[str] = set()


@contextmanager
def connect():
    data_dir().mkdir(parents=True, exist_ok=True)
    path = data_dir() / "journal.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    if str(path) not in _migrated_paths:
        with _migrate_lock:
            if str(path) not in _migrated_paths:
                con.executescript(SCHEMA)
                _migrate(con)
                _migrated_paths.add(str(path))
    try:
        yield con
        con.commit()
    finally:
        con.close()


PRIVATE, SPECIFIC, EVERYONE = "private", "specific", "everyone"
VISIBILITIES = (PRIVATE, SPECIFIC, EVERYONE)

# Visible if you own it, it's public, or someone granted it to you.
_VISIBLE = (
    "(owner = :me OR visibility = 'everyone'"
    " OR id IN (SELECT reading_id FROM reading_shares WHERE grantee = :me))"
)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return _journal().reading(dict(row))


def _attach_shares(con: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Fill in `shared_with` for the whole batch in one query."""
    ids = [r["id"] for r in rows if r["visibility"] == SPECIFIC]
    grants: dict[int, list[str]] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for rid, grantee in con.execute(
            f"SELECT reading_id, grantee FROM reading_shares"
            f" WHERE reading_id IN ({marks}) ORDER BY grantee",
            ids,
        ):
            grants.setdefault(rid, []).append(grantee)
    for r in rows:
        r["shared_with"] = grants.get(r["id"], [])
    return rows


# Guided-reading interpretation. The whole reading's (comprehensive/single) row uses
# this sentinel position so a plain composite PK enforces one per line and exactly
# one whole row (SQLite lets NULL duplicate in a PK, hence -1 not NULL).
POSITION_WHOLE = -1


def _attach_interpretations(con: sqlite3.Connection, rows: list[dict], full: bool = False) -> list[dict]:
    """Fold the reading-level interpretation columns into a nested object, and —
    when full — the per-piece and whole-reading text rows. Keeps the raw columns from
    leaking as top-level keys."""
    for r in rows:
        r["interpretation"] = {
            "mode": r.pop("interpretation_mode", None),
            "status": r.pop("interpretation_status", None) or "none",
        }
        # readings.books (migration 8): the library texts that informed the reading
        try:
            books = json.loads(r.pop("books", None) or "[]")
        except (TypeError, json.JSONDecodeError):
            books = []
        r["books"] = [b for b in books if isinstance(b, str)] if isinstance(books, list) else []
    if not full:
        return rows
    ids = [r["id"] for r in rows]
    if ids:
        marks = ",".join("?" * len(ids))
        by_reading: dict[int, list] = {}
        for ir in con.execute(
            f"SELECT reading_id, position, kind, text FROM reading_interpretations"
            f" WHERE reading_id IN ({marks}) ORDER BY position",
            ids,
        ):
            by_reading.setdefault(ir["reading_id"], []).append(ir)
        for r in rows:
            focused, comprehensive, done = {}, None, []
            for ir in by_reading.get(r["id"], []):
                if ir["kind"] == "comprehensive":
                    comprehensive = ir["text"]
                else:
                    focused[str(ir["position"])] = ir["text"]
                    done.append(ir["position"])
            r["interpretation"].update(
                focused=focused, comprehensive=comprehensive, done_positions=done
            )
    return rows


def _reading_dict(con: sqlite3.Connection, row, full: bool) -> dict:
    """Standard single-reading dict: the app's columns shaped, shares + interpretation attached."""
    d = _attach_shares(con, [_row_to_dict(row)])[0]
    return _attach_interpretations(con, [d], full=full)[0]


def list_readings(owner: str, include_shared: bool = True, limit: int | None = None) -> list[dict]:
    """Newest first. `limit` is for callers that only want the latest few (the
    home page's recent strip) — a full journal is a lot of payload to send for
    three tiles."""
    cap = "" if limit is None else " LIMIT :limit"
    with connect() as con:
        if include_shared:
            rows = con.execute(
                f"SELECT * FROM readings WHERE {_VISIBLE} ORDER BY created_at DESC{cap}",
                {"me": owner, **({} if limit is None else {"limit": limit})},
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT * FROM readings WHERE owner = :me ORDER BY created_at DESC{cap}",
                {"me": owner, **({} if limit is None else {"limit": limit})},
            ).fetchall()
        dicts = _attach_shares(con, [_row_to_dict(r) for r in rows])
        return _attach_interpretations(con, dicts, full=False)


def get_reading(reading_id: int, owner: str) -> dict | None:
    with connect() as con:
        row = con.execute(
            f"SELECT * FROM readings WHERE id = :id AND {_VISIBLE}",
            {"id": reading_id, "me": owner},
        ).fetchone()
        return _reading_dict(con, row, full=True) if row else None


def update_reading(reading_id: int, owner: str, notes: str | None = None) -> dict | None:
    with connect() as con:
        if notes is not None:
            con.execute(
                "UPDATE readings SET notes = ? WHERE id = ? AND owner = ?",
                (notes, reading_id, owner),
            )
        row = con.execute(
            "SELECT * FROM readings WHERE id = ? AND owner = ?", (reading_id, owner)
        ).fetchone()
        return _reading_dict(con, row, full=True) if row else None


def _owns(con: sqlite3.Connection, reading_id: int, owner: str) -> bool:
    return con.execute(
        "SELECT 1 FROM readings WHERE id = ? AND owner = ?", (reading_id, owner)
    ).fetchone() is not None


def set_reading_books(reading_id: int, owner: str, books: list[str]) -> None:
    """Record which library texts informed this reading (provenance; owner-gated).
    Merged with what is already recorded: a text that informed any part of the
    reading stays on its record (Tarotarium's rule)."""
    if not books:
        return
    with connect() as con:
        if not _owns(con, reading_id, owner):
            return
        row = con.execute("SELECT books FROM readings WHERE id = ?", (reading_id,)).fetchone()
        try:
            current = set(json.loads(row["books"] or "[]")) if row else set()
        except (TypeError, json.JSONDecodeError):
            current = set()
        con.execute("UPDATE readings SET books = ? WHERE id = ?",
                    (json.dumps(sorted(current | set(books))), reading_id))


def set_focused_interpretation(
    reading_id: int, owner: str, position: int, text: str, persona: str | None = None,
) -> dict | None:
    """Upsert one piece's focused reading. Owner-gated; idempotent (re-stream overwrites)."""
    with connect() as con:
        if not _owns(con, reading_id, owner):
            return None
        now = int(time.time())
        con.execute(
            "INSERT INTO reading_interpretations"
            " (reading_id, position, kind, text, persona, created_at, updated_at)"
            " VALUES (?,?, 'focused', ?,?,?,?)"
            " ON CONFLICT(reading_id, position) DO UPDATE SET"
            " text = excluded.text, persona = excluded.persona, updated_at = excluded.updated_at",
            (reading_id, position, text, persona, now, now),
        )
        row = con.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
        if _journal().pieces(row) == 1:
            # a reading of one piece has nothing left to read once that piece is
            con.execute("UPDATE readings SET interpretation_status = 'complete' WHERE id = ?", (reading_id,))
            row = con.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
        return _reading_dict(con, row, full=True)


def set_comprehensive_interpretation(
    reading_id: int, owner: str, text: str, persona: str | None = None,
) -> dict | None:
    """Upsert the whole reading's interpretation and mark the reading complete."""
    with connect() as con:
        if not _owns(con, reading_id, owner):
            return None
        now = int(time.time())
        con.execute(
            "INSERT INTO reading_interpretations"
            " (reading_id, position, kind, text, persona, created_at, updated_at)"
            " VALUES (?,?, 'comprehensive', ?,?,?,?)"
            " ON CONFLICT(reading_id, position) DO UPDATE SET"
            " text = excluded.text, persona = excluded.persona, updated_at = excluded.updated_at",
            (reading_id, POSITION_WHOLE, text, persona, now, now),
        )
        con.execute(
            "UPDATE readings SET interpretation_status = 'complete' WHERE id = ?", (reading_id,)
        )
        row = con.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
        return _reading_dict(con, row, full=True)


def get_focused_interpretations(reading_id: int, owner: str) -> dict[int, str]:
    """position -> focused text, for feeding the comprehensive prompt. Owner-gated."""
    with connect() as con:
        if not _owns(con, reading_id, owner):
            return {}
        return {
            ir["position"]: ir["text"]
            for ir in con.execute(
                "SELECT position, text FROM reading_interpretations"
                " WHERE reading_id = ? AND kind = 'focused' ORDER BY position",
                (reading_id,),
            )
        }


def set_sharing(reading_id: int, owner: str, visibility: str, grantees: list[str]) -> dict | None:
    """Set visibility and grantee list together. Owner-gated.

    Anything but 'specific' clears the grantee list, so state can't drift into
    'private, but with dangling grants'.
    """
    with connect() as con:
        owned = con.execute(
            "SELECT 1 FROM readings WHERE id = ? AND owner = ?", (reading_id, owner)
        ).fetchone()
        if owned is None:
            return None
        con.execute(
            "UPDATE readings SET visibility = ? WHERE id = ? AND owner = ?",
            (visibility, reading_id, owner),
        )
        con.execute("DELETE FROM reading_shares WHERE reading_id = ?", (reading_id,))
        if visibility == SPECIFIC and grantees:
            now = int(time.time())
            con.executemany(
                "INSERT INTO reading_shares (reading_id, grantee, granted_at) VALUES (?,?,?)",
                [(reading_id, g, now) for g in grantees],
            )
        row = con.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()
        # the same reading GET returns: the journal page swaps its copy for this one
        return _reading_dict(con, row, full=True)


def shares_granted(owner: str) -> list[dict]:
    """Readings this user has shared, each with its grantee list — the account
    page's revoke list. `everyone`-visible readings are included too, with an
    empty grantee list, so they can be seen and made private again.
    """
    with connect() as con:
        readings = con.execute(
            f"SELECT id, question, {_journal().share_columns}, created_at, visibility FROM readings"
            " WHERE owner = ? AND visibility != 'private' ORDER BY created_at DESC",
            (owner,),
        ).fetchall()
        rows = [_share_row(r) for r in readings]
        _attach_shares(con, rows)  # fills shared_with per row
        return rows


def shares_received(user: str) -> list[dict]:
    """Readings other people have shared specifically with this user."""
    with connect() as con:
        rows = con.execute(
            f"SELECT r.id, r.owner, r.question, {_prefixed(_journal().share_columns)}, r.created_at, s.granted_at"
            " FROM reading_shares s JOIN readings r ON r.id = s.reading_id"
            " WHERE s.grantee = ? ORDER BY s.granted_at DESC",
            (user,),
        ).fetchall()
        return [_share_row(r) for r in rows]


def _share_row(row: sqlite3.Row) -> dict:
    """A shared reading for the account page's lists, shaped by the app."""
    return _journal().share(dict(row))


def _prefixed(columns: str) -> str:
    return ", ".join(f"r.{c.strip()}" for c in columns.split(","))


def owned_reading_count(owner: str) -> int:
    with connect() as con:
        return con.execute(
            "SELECT count(*) FROM readings WHERE owner = ?", (owner,)
        ).fetchone()[0]


# NOTE: the user_prompts table still exists (dormant) — the custom reader
# persona and its accessors were removed deliberately: a user-supplied system
# prompt is a prompt-injection surface. Personas are the fixed built-in triad.


def get_interpretation(reading_id: int, position: int) -> dict | None:
    """One interpretation row (text + persona). NOT visibility-gated — call
    after get_reading() has already established the viewer may see it."""
    with connect() as con:
        row = con.execute(
            "SELECT position, kind, text, persona FROM reading_interpretations"
            " WHERE reading_id = ? AND position = ?",
            (reading_id, position),
        ).fetchone()
        return dict(row) if row else None


def get_user_setting(owner: str, key: str, default: str = "") -> str:
    with connect() as con:
        row = con.execute(
            "SELECT value FROM user_settings WHERE owner = ? AND key = ?", (owner, key)
        ).fetchone()
        return row["value"] if row else default


def set_user_setting(owner: str, key: str, value: str) -> None:
    with connect() as con:
        if value == "":
            con.execute("DELETE FROM user_settings WHERE owner = ? AND key = ?", (owner, key))
            return
        con.execute(
            "INSERT INTO user_settings (owner, key, value) VALUES (?,?,?)"
            " ON CONFLICT(owner, key) DO UPDATE SET value = excluded.value",
            (owner, key, value),
        )


def user_settings_all(owner: str) -> dict[str, str]:
    """Every stored setting for one user, as {key: value}."""
    with connect() as con:
        return {
            r["key"]: r["value"]
            for r in con.execute("SELECT key, value FROM user_settings WHERE owner = ?", (owner,))
        }


def display_name_overrides() -> dict[str, str]:
    """Self-chosen display names ({username: name}) — they win over the
    header/IdP-derived name wherever names are shown."""
    with connect() as con:
        return {
            r["owner"]: r["value"]
            for r in con.execute("SELECT owner, value FROM user_settings WHERE key = 'display_name'")
        }


def record_usage(
    owner: str, component: str, kind: str, model: str,
    reading_id: int | None = None,
    prompt_tokens: int | None = None, completion_tokens: int | None = None,
    characters: int | None = None, audio_bytes: int | None = None,
) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO ai_usage (ts, owner, component, kind, model, reading_id,"
            " prompt_tokens, completion_tokens, characters, audio_bytes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), owner, component, kind, model, reading_id,
             prompt_tokens, completion_tokens, characters, audio_bytes),
        )


def readings_charged(owner: str, day: str) -> int:
    with connect() as con:
        return con.execute(
            "SELECT count(*) FROM reading_charges WHERE owner = ? AND day = ?",
            (owner, day),
        ).fetchone()[0]


def reading_charge_exists(owner: str, fingerprint: str, day: str | None = None) -> bool:
    """Was this fingerprint ever charged (any day), or on `day` if given?"""
    with connect() as con:
        q = "SELECT 1 FROM reading_charges WHERE owner = ? AND fingerprint = ?"
        args: list = [owner, fingerprint]
        if day is not None:
            q += " AND day = ?"
            args.append(day)
        return con.execute(q + " LIMIT 1", args).fetchone() is not None


def try_charge_reading(owner: str, day: str, fingerprint: str, limit: float | None) -> bool:
    """Charge idempotently; returns False only when the cap blocks a NEW charge.

    BEGIN IMMEDIATE takes the write lock before the count, so two racing
    requests can't both squeeze under the cap via separate counts.
    """
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        seen = con.execute(
            "SELECT 1 FROM reading_charges WHERE owner = ? AND day = ? AND fingerprint = ?",
            (owner, day, fingerprint),
        ).fetchone()
        if seen:
            return True
        if limit is not None:
            used = con.execute(
                "SELECT count(*) FROM reading_charges WHERE owner = ? AND day = ?",
                (owner, day),
            ).fetchone()[0]
            if used >= limit:
                return False
        con.execute(
            "INSERT OR IGNORE INTO reading_charges (owner, day, fingerprint, created_at)"
            " VALUES (?,?,?,?)",
            (owner, day, fingerprint, int(time.time())),
        )
        return True


def llm_tokens_since(owner: str, since_ts: int) -> int:
    """Prompt+completion tokens today; calls whose provider omitted usage fall
    back to output-chars/4 so nothing rides free."""
    with connect() as con:
        return int(con.execute(
            "SELECT coalesce(sum(coalesce(prompt_tokens, 0)"
            " + coalesce(completion_tokens, coalesce(characters, 0) / 4)), 0)"
            " FROM ai_usage WHERE owner = ? AND component = 'llm' AND ts >= ?",
            (owner, since_ts),
        ).fetchone()[0])


def tts_bytes_since(owner: str, since_ts: int) -> int:
    with connect() as con:
        return int(con.execute(
            "SELECT coalesce(sum(audio_bytes), 0) FROM ai_usage"
            " WHERE owner = ? AND component = 'tts' AND ts >= ?",
            (owner, since_ts),
        ).fetchone()[0])


def usage_summary(days: int) -> dict:
    """Aggregates for the admin usage page: totals per component+model, a
    per-day series, and per-user totals, over the last `days` days."""
    since = int(time.time()) - days * 86400
    with connect() as con:
        by_model = [
            dict(r) for r in con.execute(
                "SELECT component, model, count(*) AS calls,"
                " coalesce(sum(prompt_tokens),0) AS prompt_tokens,"
                " coalesce(sum(completion_tokens),0) AS completion_tokens,"
                " coalesce(sum(characters),0) AS characters,"
                " coalesce(sum(audio_bytes),0) AS audio_bytes"
                " FROM ai_usage WHERE ts >= ?"
                " GROUP BY component, model ORDER BY component, calls DESC",
                (since,),
            )
        ]
        daily = [
            dict(r) for r in con.execute(
                "SELECT date(ts, 'unixepoch', 'localtime') AS day, count(*) AS calls,"
                " coalesce(sum(prompt_tokens),0) AS prompt_tokens,"
                " coalesce(sum(completion_tokens),0) AS completion_tokens,"
                " coalesce(sum(CASE WHEN component='tts' THEN characters END),0) AS tts_characters,"
                " coalesce(sum(audio_bytes),0) AS audio_bytes"
                " FROM ai_usage WHERE ts >= ?"
                " GROUP BY day ORDER BY day DESC",
                (since,),
            )
        ]
        by_user = [
            dict(r) for r in con.execute(
                "SELECT owner, component, count(*) AS calls,"
                " coalesce(sum(prompt_tokens),0) AS prompt_tokens,"
                " coalesce(sum(completion_tokens),0) AS completion_tokens,"
                " coalesce(sum(audio_bytes),0) AS audio_bytes"
                " FROM ai_usage WHERE ts >= ?"
                " GROUP BY owner, component ORDER BY owner",
                (since,),
            )
        ]
    return {"days": days, "by_model": by_model, "daily": daily, "by_user": by_user}


# --- TTS cache index (LRU; files live in /data/tts-cache) -------------------

def tts_cache_all() -> list[dict]:
    """All entries, least-recently-played first (eviction order)."""
    with connect() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT hash, size_bytes, last_used_at FROM tts_cache ORDER BY last_used_at"
            )
        ]


def tts_cache_upsert(hash_: str, size_bytes: int) -> None:
    now = int(time.time())
    with connect() as con:
        con.execute(
            "INSERT INTO tts_cache (hash, size_bytes, created_at, last_used_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(hash) DO UPDATE SET"
            " size_bytes = excluded.size_bytes, last_used_at = excluded.last_used_at",
            (hash_, size_bytes, now, now),
        )


def tts_cache_touch(hash_: str) -> None:
    """Bump last_used_at, throttled to hourly so replays don't churn writes.

    Hourly (not daily): a day-long throttle left today's most-played file
    stuck at the front of the LRU eviction order all day."""
    now = int(time.time())
    with connect() as con:
        con.execute(
            "UPDATE tts_cache SET last_used_at = ? WHERE hash = ? AND last_used_at < ?",
            (now, hash_, now - 3600),
        )


def tts_cache_delete(hash_: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM tts_cache WHERE hash = ?", (hash_,))


def get_setting(key: str) -> str:
    with connect() as con:
        row = con.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""


def set_setting(key: str, value: str) -> None:
    with connect() as con:
        if value:
            con.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        else:
            con.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def delete_reading(reading_id: int, owner: str) -> bool:
    with connect() as con:
        cur = con.execute("DELETE FROM readings WHERE id = ? AND owner = ?", (reading_id, owner))
        return cur.rowcount > 0
