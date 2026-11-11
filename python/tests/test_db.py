"""The journal: both migration series, the one-time mapping of an old
database, and the reading helpers that go through the app's hooks."""

import json
import sqlite3
import time

import pytest

from fortunes_platform import db


def _keys(path):
    con = sqlite3.connect(path)
    try:
        return dict(con.execute("SELECT key, value FROM app_settings WHERE key LIKE '%schema_version'"))
    finally:
        con.close()


def _tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()


def _reopen():
    db._migrated_paths.clear()
    with db.connect():
        pass


def _app_step(ran):
    def step(con):
        ran.append(1)
        con.execute("ALTER TABLE readings ADD COLUMN extra TEXT")

    return step


def test_a_new_database_runs_both_series(tmp_path, journal):
    ran = []
    journal["current"] = db.Journal(**{**db_kwargs(), "migrations": [(1, _app_step(ran))]})
    with db.connect():
        pass
    assert _keys(tmp_path / "journal.db") == {"core_schema_version": "9", "app_schema_version": "1"}
    assert {"users", "sessions", "user_settings", "ai_usage", "reading_charges", "tts_cache",
            "reading_shares", "reading_interpretations"} <= _tables(tmp_path / "journal.db")
    assert ran == [1]
    _reopen()
    assert ran == [1], "a step runs once"


def db_kwargs():
    from conftest import JOURNAL

    return {f: getattr(JOURNAL, f) for f in ("reading", "pieces", "share_columns", "share", "known_users")}


@pytest.mark.parametrize(
    "legacy, core, app_, app_ran",
    [
        (9, "9", "1", True),  # every old step was the core's (Tarotarium today); the app's own run after
        (10, "9", "1", False),  # I Ching today: its old step 10 is its step 1
        (5, "9", "1", True),  # an old install: the rest of the core, then the app's
    ],
)
def test_an_old_database_is_mapped_once(tmp_path, journal, legacy, core, app_, app_ran):
    ran = []
    journal["current"] = db.Journal(**{**db_kwargs(), "migrations": [(1, _app_step(ran))]})
    # Build the old database: the old series up to `legacy`, then forget the new keys.
    old = [(v, fn) for v, fn in db.CORE_MIGRATIONS if v <= legacy]
    con = sqlite3.connect(tmp_path / "journal.db")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    con.isolation_level = None
    for v, fn in old:
        fn(con)
    if legacy >= 10:
        con.execute("ALTER TABLE readings ADD COLUMN extra TEXT")
    con.execute("INSERT INTO app_settings (key, value) VALUES ('schema_version', ?)", (str(legacy),))
    con.close()

    _reopen()
    keys = _keys(tmp_path / "journal.db")
    assert keys["core_schema_version"] == core
    assert keys["app_schema_version"] == app_
    assert keys["schema_version"] == str(legacy), "the old number is left for an older build"
    assert (ran == [1]) is app_ran


def test_a_failed_step_rolls_back_and_stops(tmp_path, journal):
    def bad(con):
        con.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("no")

    journal["current"] = db.Journal(**{**db_kwargs(), "migrations": [(1, bad)]})
    with pytest.raises(RuntimeError):
        with db.connect():
            pass
    assert _keys(tmp_path / "journal.db").get("app_schema_version", "0") == "0"
    assert "half_done" not in _tables(tmp_path / "journal.db")
    assert (tmp_path / "journal.db.bak-app-v0").exists(), "a snapshot before the step"


def _save(owner="sam", tiles=("a", "b"), question=None):
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO readings (owner, created_at, question, deck, spread, cards, interpretation_mode,"
            " interpretation_status) VALUES (?,?,?,'','three',?, 'isolated', 'in_progress')",
            (owner, int(time.time()), question, json.dumps(list(tiles))),
        )
        return cur.lastrowid


def test_readings_are_shaped_by_the_app(app):
    rid = _save()
    r = db.get_reading(rid, "sam")
    assert r["tiles"] == ["a", "b"] and "deck" not in r and "cards" not in r
    assert r["interpretation"]["status"] == "in_progress"
    assert r["books"] == []


def test_a_one_piece_reading_is_complete_once_its_piece_is_read(app):
    two, one = _save(), _save(tiles=("a",))
    assert db.set_focused_interpretation(two, "sam", 0, "text")["interpretation"]["status"] == "in_progress"
    assert db.set_focused_interpretation(one, "sam", 0, "text")["interpretation"]["status"] == "complete"
    assert db.set_focused_interpretation(one, "robin", 0, "text") is None, "owner only"


def test_sharing_returns_the_whole_reading_and_lists_by_the_app_columns(app):
    from fortunes_platform import users

    users.touch_sync("robin")
    rid = _save(question=None)
    db.set_focused_interpretation(rid, "sam", 0, "first")
    r = db.set_sharing(rid, "sam", db.SPECIFIC, ["robin"])
    assert r["shared_with"] == ["robin"] and r["interpretation"]["focused"] == {"0": "first"}
    granted = db.shares_granted("sam")
    assert granted[0]["spread"] == "three" and granted[0]["shared_with"] == ["robin"]
    assert db.shares_received("robin")[0]["spread"] == "three"


def test_books_merge_and_an_empty_list_writes_nothing(app):
    rid = _save()
    db.set_reading_books(rid, "sam", ["b2"])
    db.set_reading_books(rid, "sam", ["b1", "b2"])
    db.set_reading_books(rid, "sam", [])
    assert db.get_reading(rid, "sam")["books"] == ["b1", "b2"]
    with db.connect() as con:
        con.execute("UPDATE readings SET books = 'not json' WHERE id = ?", (rid,))
    assert db.get_reading(rid, "sam")["books"] == [], "a damaged list reads as none"


def test_core_step_one_registers_the_users_the_app_knew(tmp_path):
    with db.connect() as con:
        names = {r[0] for r in con.execute("SELECT username FROM users")}
    assert "robin" in names
