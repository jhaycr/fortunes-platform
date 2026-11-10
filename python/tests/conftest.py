"""Every test runs inside a stand-in app: its own data directory, its own
environment prefix, and a database that answers settings from a dict."""

from types import SimpleNamespace

import pytest

from fortunes_platform import host


@pytest.fixture
def settings():
    """The stand-in database's app_settings table."""
    return {}


@pytest.fixture(autouse=True)
def app(tmp_path, settings, monkeypatch):
    for var in list(__import__("os").environ):
        if var.startswith("TESTAPP_"):
            monkeypatch.delenv(var)
    db = SimpleNamespace(get_setting=lambda key: settings.get(key, ""))
    stand_in = host.Host(
        id="testapp",
        name="Test App",
        env_prefix="TESTAPP_",
        data_dir=lambda: tmp_path,
        db=lambda: db,
    )
    host.configure(stand_in)
    yield stand_in
    host.configure(None)  # type: ignore[arg-type]
