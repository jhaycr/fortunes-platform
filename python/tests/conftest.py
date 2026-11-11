"""Every test runs inside a stand-in app: its own data directory, its own
environment prefix, and a journal whose readings are a list of tiles."""

import json

import pytest

from fortunes_platform import db, host


def _reading(d: dict) -> dict:
    d["tiles"] = json.loads(d.pop("cards") or "[]")
    for dormant in ("deck", "spread"):
        d.pop(dormant, None)
    return d


JOURNAL = db.Journal(
    reading=_reading,
    pieces=lambda row: len(json.loads(row["cards"] or "[]")),
    share_columns="spread",
    known_users=lambda: ["robin"],
)


PROMPTS = {
    "personas": [
        {"slug": "sage", "name": "The Sage", "description": "calm", "prompt": "You are calm."},
        {"slug": "imp", "name": "The Imp", "description": "playful", "prompt": "You are playful."},
    ],
    "default_persona": "sage",
    "max_tokens": {"default": 700},
}

VOICES = {
    "sage": {"voice": "onyx", "speed": 1.0, "instructions": "Slow and low."},
    "imp": {"voice": "nova", "speed": 1.1, "instructions": "Quick and bright."},
}


@pytest.fixture
def journal():
    """The stand-in app's part of the journal; a test may swap it."""
    return {"current": JOURNAL}


@pytest.fixture
def settings(app):
    """Write to the stand-in database's app_settings table."""

    class Settings:
        def __setitem__(self, key, value):
            db.set_setting(key, value)

    return Settings()


@pytest.fixture(autouse=True)
def app(tmp_path, journal, monkeypatch):
    for var in list(__import__("os").environ):
        if var.startswith("TESTAPP_"):
            monkeypatch.delenv(var)
    stand_in = host.Host(
        id="testapp",
        name="Test App",
        env_prefix="TESTAPP_",
        data_dir=lambda: tmp_path,
        journal=lambda: journal["current"],
        prompts=lambda: PROMPTS,
        voices=lambda: VOICES,
    )
    host.configure(stand_in)
    db._migrated_paths.clear()
    yield stand_in
    host.configure(None)  # type: ignore[arg-type]
