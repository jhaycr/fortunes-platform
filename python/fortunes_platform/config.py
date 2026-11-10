"""Declarative instance config from a YAML file (Ansible-managed).

Precedence for any setting is: **config file > admin UI (DB) > environment**.
The file wins deliberately — the point of managing it from Ansible is that the
deployed state is authoritative, so a stray click in the UI must not silently
override it. Endpoints refuse to write settings the file owns, and the UI shows
them as managed.

Default location config.yaml in the app's data directory, overridable with
`<prefix>CONFIG_FILE`. Absent
file = everything falls through to the old DB/env behaviour, so this is purely
additive.

    llm:
      base_url: https://openrouter.ai/api/v1
      model: minimax/minimax-m2
      api_key: "..."            # or api_key_env: <prefix>LLM_API_KEY
      max_tokens: 900
    limits:                     # per-user daily spend caps; omit/blank = no cap
      readings_per_day: 10
      llm_tokens_per_day: 150000
      tts_minutes_per_day: 30
    interpretation:
      default_persona: <a persona id>
      system_prompt: |          # replaces the default persona's prompt
        ...
    tts:
      provider: openai          # or elevenlabs; default openai
      base_url: https://api.openai.com/v1   # optional for elevenlabs
      model: gpt-4o-mini-tts
      api_key: "..."            # or api_key_env: <prefix>TTS_API_KEY
      cache_max_mb: 256
      voices:                   # per persona, per provider
        <persona id>:
          openai:     {voice: sage, speed: 1.0, instructions: "..."}
          elevenlabs: {voice_id: "...", stability: 0.4, style: 0.2}
      # A flat block (no provider key) is read as the openai block, so
      # pre-existing config keeps working untouched.

Each app adds sections of its own (I Ching's `library`, Tarotarium's `reading`
and `books`); this module reads any section by name.

A malformed file is reported rather than fatal: the app keeps serving on the
previous behaviour and surfaces the parse error through /api/settings/*, so a
bad template shows up in the UI instead of taking the instance down.
"""

import os
import threading
from pathlib import Path

import yaml

from fortunes_platform import host
from fortunes_platform.host import data_dir

_lock = threading.Lock()
_cache: dict = {}
_cache_key: tuple | None = None
_error: str = ""


def config_path() -> Path:
    override = host.env("CONFIG_FILE").strip()
    return Path(override) if override else data_dir() / "config.yaml"


def _load() -> dict:
    """Parse the file, re-reading when it changes on disk.

    Keyed on (mtime, size) so an Ansible re-template takes effect without a
    container restart.
    """
    global _cache, _cache_key, _error
    path = config_path()
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None

    with _lock:
        if key == _cache_key:
            return _cache
        if key is None:
            _cache, _cache_key, _error = {}, None, ""
            return _cache
        try:
            parsed = yaml.safe_load(path.read_text()) or {}
            if not isinstance(parsed, dict):
                raise ValueError("top level must be a mapping")
            _cache, _error = parsed, ""
        except (OSError, yaml.YAMLError, ValueError) as exc:
            # Keep serving; make the problem visible instead.
            _cache, _error = {}, f"{path}: {exc}"
        _cache_key = key
        return _cache


def section(name: str) -> dict:
    value = _load().get(name)
    return value if isinstance(value, dict) else {}


def get(name: str, key: str, default=None):
    """A config value, or `default` when unset. Empty string counts as unset so
    a blank template line doesn't override anything."""
    value = section(name).get(key, None)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    return value


def has(name: str, key: str) -> bool:
    """Whether the file owns this setting (and so the UI must not write it)."""
    return get(name, key) is not None


def error() -> str:
    """Parse error from the last load, or "" when the file is fine/absent."""
    _load()
    return _error


def exists() -> bool:
    return config_path().is_file()


def _section_api_key(section: str) -> str | None:
    """Inline value, or the named environment variable's value."""
    inline = get(section, "api_key")
    if inline:
        return str(inline)
    env_name = get(section, "api_key_env")
    if env_name:
        return os.environ.get(str(env_name), "")
    return None


def llm_api_key() -> str | None:
    return _section_api_key("llm")


def auth_client_secret() -> str | None:
    """OIDC client secret from the file: inline `client_secret` or the env
    var named by `client_secret_env`. None = file doesn't own it."""
    inline = get("auth", "client_secret")
    if inline:
        return str(inline)
    env_name = get("auth", "client_secret_env")
    if env_name:
        return os.environ.get(str(env_name), "")
    return None


def tts_api_key() -> str | None:
    return _section_api_key("tts")
