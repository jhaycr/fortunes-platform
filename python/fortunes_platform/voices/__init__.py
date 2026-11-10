"""Voice providers: the boundary every text-to-speech adapter implements
(base.py), the adapters, and the registry that picks one.

One provider is active per instance (`tts.provider`, default "openai").
Adding a backend means adding an adapter here — callers, caching, the
usage ledger and the routes are provider-agnostic.
"""

from __future__ import annotations

from fortunes_platform.voices.base import (  # noqa: F401 — re-exported for callers
    BOOL,
    LONGTEXT,
    NUMBER,
    SLIDER,
    TEXT,
    Voice,
    VoiceAdapter,
    VoiceField,
    VoiceRequest,
)
from fortunes_platform.voices.elevenlabs import ElevenLabsAdapter
from fortunes_platform.voices.openai import OpenAIAdapter
from fortunes_platform.voices.openrouter import OpenRouterAdapter

DEFAULT_PROVIDER = "openai"

_ADAPTERS: dict[str, VoiceAdapter] = {
    a.name: a for a in (OpenAIAdapter(), ElevenLabsAdapter(), OpenRouterAdapter())
}


def get_adapter(name: str | None) -> VoiceAdapter:
    """The named adapter, falling back to the default.

    An unknown name must not 500 every caller (/api/me reads tts config):
    it degrades to the default and the settings page reports the bad value.
    """
    return _ADAPTERS.get((name or DEFAULT_PROVIDER).strip().lower(),
                         _ADAPTERS[DEFAULT_PROVIDER])


def known(name: str | None) -> bool:
    """Is this a registered provider? Strict — an empty/None name is NOT
    known (it just means "unset"), so callers can tell "unset" from
    "explicitly chose openai"."""
    return bool(name) and name.strip().lower() in _ADAPTERS


def all_adapters() -> list[VoiceAdapter]:
    return list(_ADAPTERS.values())
