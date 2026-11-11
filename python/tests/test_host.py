import pytest

from fortunes_platform import host


def test_the_app_hands_in_its_prefix_and_data_directory(app, tmp_path, monkeypatch):
    monkeypatch.setenv("TESTAPP_SECRET_KEY", "s")
    assert host.env("SECRET_KEY") == "s"
    assert host.env("MISSING", "fallback") == "fallback"
    assert host.env_name("AUTH_MODE") == "TESTAPP_AUTH_MODE"
    assert host.data_dir() == tmp_path


def test_the_journal_is_looked_up_on_each_use(app, journal):
    first = host.current().journal()
    journal["current"] = "another"
    assert host.current().journal() == "another"
    assert first is not None


def test_using_the_shared_code_before_the_app_hands_itself_in_says_so():
    host.configure(None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="configure"):
        host.env("ANYTHING")
