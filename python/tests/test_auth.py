"""Sign-in policy, read through the app's own names."""

from types import SimpleNamespace

import pytest

from fortunes_platform import auth


def _request(peer: str, headers: dict | None = None, cookies: dict | None = None):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers or {}, cookies=cookies or {})


def test_names_come_from_the_app(monkeypatch):
    assert auth.cookie_name() == "testapp_session"
    assert auth.auth_header() == "x-authentik-username"
    assert auth.fallback_user() == "local"
    monkeypatch.setenv("TESTAPP_AUTH_HEADER", "x-forwarded-user")
    monkeypatch.setenv("TESTAPP_FALLBACK_USER", "home")
    assert auth.auth_header() == "x-forwarded-user"
    assert auth.fallback_user() == "home"


def test_env_admins_default_to_the_fallback_user(monkeypatch):
    assert auth.admin_users() == {"local"} and auth.env_is_admin("local")
    monkeypatch.setenv("TESTAPP_ADMIN_USERS", " Ada, ben ,")
    assert auth.admin_users() == {"ada", "ben"}
    assert not auth.env_is_admin("local")


def test_mode_reads_file_then_database_then_environment(settings, monkeypatch, tmp_path):
    assert auth.mode() == auth.LEGACY
    monkeypatch.setenv("TESTAPP_AUTH_MODE", "header")
    assert auth.mode() == auth.HEADER
    settings["auth_mode"] = "oidc"
    assert auth.mode() == auth.OIDC
    (tmp_path / "config.yaml").write_text("auth:\n  mode: header\n")
    assert auth.mode() == auth.HEADER


def test_header_trusted_refuses_a_non_proxy_peer(monkeypatch):
    monkeypatch.setattr(auth, "mode", lambda: auth.HEADER)
    monkeypatch.setattr(auth, "_trusted_header_cfg", lambda: {"enabled": True, "proxies": ["10.10.0.0/24"]})
    assert auth.header_trusted(_request("10.10.0.5")) is True
    assert auth.header_trusted(_request("203.0.113.9")) is False


def test_legacy_identity_falls_back_and_is_sanitized():
    assert auth.current_user(_request("1.2.3.4")) == "local"
    assert auth.current_user(_request("1.2.3.4", {"x-authentik-username": "Some.One@Example.com"})) == "some.one_example.com"
    assert auth.is_authenticated(_request("1.2.3.4", cookies={"testapp_session": "t"}))
    assert not auth.is_authenticated(_request("1.2.3.4", cookies={"other_session": "t"}))


@pytest.mark.parametrize("raw", ["..", ".", "", "...", "../.."])
def test_reserved_identities_do_not_escape(raw):
    out = auth.sanitize_identity(raw)
    assert out not in ("", ".", "..") and "/" not in out


def test_display_names_drop_an_email_domain():
    assert auth.display_name_from("someone@example.com") == "someone"
    assert auth.display_name_from("") == "local"
