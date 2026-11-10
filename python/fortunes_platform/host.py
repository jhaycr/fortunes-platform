"""The app this code is running in, handed in once at startup.

The shared modules (sign-in, users, sessions, config, limits, encryption) are
the same in every Little Fortunes app but for a few names: the app's own name,
the prefix of its environment variables, its data directory, and its database.
The app hands those in with `configure`, from its package's `__init__.py`, so
they are set before any request is served:

    from fortunes_platform import host

    host.configure(host.Host(
        id="iching",                 # the session cookie is `iching_session`
        name="Little Fortunes · I Ching",
        env_prefix="ICHING_",        # ICHING_SECRET_KEY, ICHING_AUTH_MODE, ...
        data_dir=paths.data_dir,     # called on each use, so tests can move it
        db=lambda: importlib.import_module("iching.db"),
    ))

Nothing here reads the environment or the database when it is imported. Every
module asks at the moment it needs a value, so an app's tests can change the
environment, and `configure` can run before the app's database module exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


@dataclass(frozen=True)
class Host:
    """What the shared modules need to know about the app they serve."""

    id: str
    """Lower-case identifier: names the session cookie (`<id>_session`) and
    marks the app's lines in the server log."""

    name: str
    """The app's display name, for anything a person reads."""

    env_prefix: str
    """Prefix of the app's environment variables, with its trailing underscore
    (`ICHING_`), so `env("SECRET_KEY")` reads `ICHING_SECRET_KEY`."""

    data_dir: Callable[[], Path]
    """Returns the directory the app keeps its data in (its config.yaml, its
    secret key, its database)."""

    db: Callable[[], ModuleType]
    """Returns the app's database module. The shared modules call its
    `connect()` and `get_setting()`, and the limits module its spending
    ledger: `reading_charge_exists`, `readings_charged`, `try_charge_reading`,
    `llm_tokens_since` and `tts_bytes_since`; users read
    `display_name_overrides`."""


_current: Host | None = None


def configure(app: Host) -> None:
    """Hand in the app. Called once, from the app's package `__init__.py`; a
    second call replaces the first (tests may configure a stand-in)."""
    global _current
    _current = app


def current() -> Host:
    if _current is None:
        raise RuntimeError(
            "fortunes_platform.host.configure() has not been called: the app's "
            "package __init__.py must hand itself in before the shared modules are used"
        )
    return _current


def env(key: str, default: str = "") -> str:
    """The app's environment variable `<env_prefix><key>`, or `default`."""
    return os.environ.get(current().env_prefix + key, default)


def env_name(key: str) -> str:
    """The full name of the app's variable `key`, for messages and docs."""
    return current().env_prefix + key


def data_dir() -> Path:
    return current().data_dir()


class _Database:
    """The app's database module, looked up on each use: `db.connect()` here
    is the app's `connect()`, and a test's monkeypatch of the app's module
    applies here too."""

    def __getattr__(self, name: str) -> Any:
        return getattr(current().db(), name)


db = _Database()
