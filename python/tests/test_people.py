"""Handing a person's data to someone else, and erasing an account: the
journal's part here, the app's files through the steps it hands in."""

import json

from fortunes_platform import db, people, sessions, users


def _reading(owner: str) -> int:
    with db.connect() as con:
        return con.execute(
            "INSERT INTO readings (owner, created_at, question, deck, spread, cards)"
            " VALUES (?, 1, 'q', '', 'one', ?)", (owner, json.dumps(["a"]))).lastrowid


def _people(*names: str) -> None:
    for name in names:
        users.touch_sync(name)


def _rows(sql: str, *args) -> list[tuple]:
    with db.connect() as con:
        return [tuple(r) for r in con.execute(sql, args)]


def test_a_handover_moves_the_journal_and_signs_the_source_out():
    _people("robin", "kit", "ash")
    mine = _reading("robin")
    theirs = _reading("ash")
    db.set_user_setting("robin", "persona", "imp")
    db.set_user_setting("robin", "auto_read_audio", "true")
    db.set_user_setting("kit", "persona", "sage")
    with db.connect() as con:
        con.execute("INSERT INTO reading_shares VALUES (?, 'robin', 1)", (theirs,))
        for owner in ("robin", "kit"):
            con.execute("INSERT INTO reading_charges VALUES (?, '2026-01-01', 'same', 1)", (owner,))
        con.execute("INSERT INTO reading_charges VALUES ('robin', '2026-01-01', 'only', 1)")
        con.execute("INSERT INTO ai_usage (ts, owner, component, kind, model) VALUES (1, 'robin', 'llm', 'single', 'm')")
    token = sessions.create("robin")
    calls = []

    report = people.reassign("robin", "kit", lambda src, dst: calls.append((src, dst)) or {"files": ["f"]})

    assert report == {"from": "robin", "to": "kit", "readings": 1, "settings": 2, "usage_rows": 1,
                      "files": ["f"], "deactivated": True}
    assert calls == [("robin", "kit")]
    assert _rows("SELECT owner FROM readings WHERE id = ?", mine) == [("kit",)]
    assert db.get_user_setting("kit", "persona") == "sage", "the destination's own choice wins"
    assert db.get_user_setting("kit", "auto_read_audio") == "true"
    assert _rows("SELECT owner, fingerprint FROM reading_charges ORDER BY fingerprint") == \
        [("kit", "only"), ("kit", "same")], "a charge both had is kept once"
    assert _rows("SELECT owner FROM ai_usage") == [("kit",)]
    assert _rows("SELECT grantee FROM reading_shares") == [("robin",)], "a share given to robin stays theirs"
    assert sessions.resolve(token) is None
    assert users.get("robin")["active"] is False


def test_a_handover_run_twice_changes_nothing_more():
    _people("robin", "kit")
    _reading("robin")
    files = lambda src, dst: {}
    people.reassign("robin", "kit", files)
    again = people.reassign("robin", "kit", files)
    assert (again["readings"], again["settings"], again["usage_rows"]) == (0, 0, 0)
    assert _rows("SELECT owner FROM readings") == [("kit",)]


def test_erasing_an_account_leaves_the_ledger_and_other_peoples_readings():
    _people("robin", "kit")
    mine = _reading("robin")
    kits = _reading("kit")
    db.set_user_setting("robin", "persona", "imp")
    with db.connect() as con:
        con.execute("INSERT INTO reading_shares VALUES (?, 'kit', 1)", (mine,))
        con.execute("INSERT INTO reading_shares VALUES (?, 'robin', 1)", (kits,))
        con.execute("INSERT INTO reading_charges VALUES ('robin', '2026-01-01', 'x', 1)")
        con.execute("INSERT INTO ai_usage (ts, owner, component, kind, model) VALUES (1, 'robin', 'llm', 'single', 'm')")
    sessions.create("robin")
    seen = []

    def erase_files(username: str) -> dict:
        seen.append(username)
        assert users.get(username), "the app's files go before the account does"
        return {"drafts_removed": []}

    report = people.erase("robin", erase_files)

    assert report == {"user": "robin", "readings": 1, "shares_received": 1, "settings": 1,
                      "drafts_removed": [], "deleted": True}
    assert seen == ["robin"]
    assert users.get("robin") is None
    assert _rows("SELECT owner FROM readings") == [("kit",)]
    assert _rows("SELECT * FROM reading_shares") == [], "robin's reading took its share with it"
    assert _rows("SELECT * FROM reading_charges") == [] and _rows("SELECT * FROM sessions") == []
    assert _rows("SELECT owner FROM ai_usage") == [("robin",)], "the usage ledger keeps its rows"
