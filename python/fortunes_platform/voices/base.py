"""The voice-provider boundary.

`tts.py` used to hard-code one API shape: POST {base_url}/audio/speech with
{model, voice, input, speed, response_format} and a Bearer header. Every
one of those is provider-specific — ElevenLabs puts the voice in the URL
path, authenticates with `xi-api-key`, calls the text `text`, and hides
`speed` inside a `voice_settings` object.

An adapter owns exactly that: how to turn (script, voice block, config)
into one HTTP request, how long a chunk may be, and which optional
features it supports. Everything else — caching, LRU eviction, the usage
ledger, the routes — stays in tts.py and is provider-agnostic.

`fields` is deliberately data rather than code: it declares a provider's
voice-block schema once, and that single declaration drives block
validation, the shipped defaults, and the rendered settings form. A new
adapter therefore needs no frontend work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Field kinds the settings UI knows how to render.
TEXT = "text"
LONGTEXT = "longtext"
NUMBER = "number"
SLIDER = "slider"
BOOL = "bool"


@dataclass(frozen=True)
class VoiceField:
    """One tunable in a provider's voice block."""

    key: str
    label: str
    kind: str
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str | None = None

    def coerce(self, value: Any) -> Any:
        """Value in this field's type, or the default when unusable.

        Never raises: a malformed stored block must degrade to something
        playable rather than break audio for every persona.
        """
        if value is None:
            return self.default
        if self.kind == BOOL:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if self.kind in (NUMBER, SLIDER):
            try:
                num = float(value)
            except (TypeError, ValueError):
                return self.default
            if self.min is not None:
                num = max(self.min, num)
            if self.max is not None:
                num = min(self.max, num)
            return num
        text = str(value).strip()
        return text if text else self.default


@dataclass(frozen=True)
class Voice:
    """One selectable voice, as offered by a provider that can list them."""

    id: str
    name: str
    category: str | None = None
    description: str | None = None
    preview_url: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DesignedVoice:
    """One candidate from a voice-design pass, before it is kept.

    The audio is returned inline (base64 mp3) rather than as a URL: these
    previews are ephemeral and belong to no account until saved.
    """

    generated_voice_id: str
    audio_base64: str
    media_type: str = "audio/mpeg"
    duration_secs: float | None = None


@dataclass(frozen=True)
class VoiceRequest:
    """One provider call, expressed so tts.py can issue it without knowing
    whose API it is."""

    url: str
    headers: dict[str, str]
    json: dict[str, Any]
    params: dict[str, str] = field(default_factory=dict)


class VoiceAdapter:
    """Base class; subclasses fill in the parts that differ."""

    name: str = "base"
    label: str = "Base"
    default_base_url: str = ""
    default_model: str = ""
    fields: list[VoiceField] = []

    supports_listing: bool = False
    supports_continuity: bool = False  # previous/next text for prosody
    supports_seed: bool = False
    # Can build a voice from a written description — which is exactly what
    # the personas' `instructions` text already is.
    supports_design: bool = False

    # Conservative floor for a provider that doesn't publish per-model caps.
    default_chunk_chars: int = 3600

    def max_chunk_chars(self, model: str) -> int:
        return self.default_chunk_chars

    def normalize(self, block: dict | None) -> dict:
        """A complete voice block: known keys coerced, unknown keys dropped,
        missing keys defaulted."""
        block = block if isinstance(block, dict) else {}
        return {f.key: f.coerce(block.get(f.key)) for f in self.fields}

    def missing_required(self, block: dict | None) -> list[str]:
        """Required fields with no usable value — the reason a persona may
        have no audio after a provider switch."""
        block = self.normalize(block)
        return [f.key for f in self.fields if f.required and not block.get(f.key)]

    def build_request(self, *, script: str, voice: dict, cfg: dict,
                      previous: str | None = None, next: str | None = None,
                      seed: int | None = None) -> VoiceRequest:
        raise NotImplementedError

    async def list_voices(self, cfg: dict) -> list[Voice]:
        raise NotImplementedError

    async def design_voices(self, cfg: dict, description: str,
                            preview_text: str | None = None) -> list[DesignedVoice]:
        """Candidate voices matching a written description."""
        raise NotImplementedError

    async def keep_designed_voice(self, cfg: dict, generated_voice_id: str,
                                  name: str, description: str,
                                  rejected: list[str] | None = None) -> str:
        """Persist a candidate to the account; returns the permanent voice id."""
        raise NotImplementedError
