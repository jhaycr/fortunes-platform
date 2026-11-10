"""Encryption at rest and the declarative config file, in the app's data directory."""

import stat

from fortunes_platform import config as cfgfile
from fortunes_platform import crypto


def test_a_generated_key_is_kept_private_in_the_data_directory(tmp_path):
    token = crypto.encrypt("hello")
    assert token != "hello" and crypto.decrypt(token) == "hello"
    key = tmp_path / ".secret_key"
    assert key.exists() and stat.S_IMODE(key.stat().st_mode) == 0o600


def test_the_key_can_come_from_the_environment(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TESTAPP_SECRET_KEY", Fernet.generate_key().decode())
    assert crypto.decrypt(crypto.encrypt("x")) == "x"
    assert not (tmp_path / ".secret_key").exists()


def test_config_is_read_from_the_data_directory_or_the_override(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("llm:\n  model: m1\n")
    assert cfgfile.get("llm", "model") == "m1"
    other = tmp_path / "elsewhere.yaml"
    other.write_text("llm:\n  model: m2\n")
    monkeypatch.setenv("TESTAPP_CONFIG_FILE", str(other))
    assert cfgfile.get("llm", "model") == "m2"


def test_an_api_key_can_be_named_by_environment_variable(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("llm:\n  api_key_env: SOME_KEY\n")
    monkeypatch.setenv("SOME_KEY", "secret")
    assert cfgfile.llm_api_key() == "secret"
