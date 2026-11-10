"""OpenAI-compatible /audio/speech — the shape the app has always used.

Behavior here is deliberately byte-identical to the pre-adapter code: the
same URL, the same Bearer header, the same body keys in the same order of
significance. Existing cached audio is addressed by a hash that includes
the request's inputs, so any change here would silently orphan the cache
and re-render every piece (which happened once in v0.11.2 and is audible).

Compatible servers that aren't OpenAI (Kokoro's kokoro-fastapi, LocalAI)
ignore `instructions`, which is why it is sent only when non-empty.
"""

from __future__ import annotations

from fortunes_platform.voices.base import (
    LONGTEXT,
    NUMBER,
    TEXT,
    VoiceAdapter,
    VoiceField,
    VoiceRequest,
)


class OpenAIAdapter(VoiceAdapter):
    name = "openai"
    label = "OpenAI-compatible"
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4o-mini-tts"

    # Stays 3600: gpt-4o-mini-tts caps input at ~2000 tokens (~4 chars/token).
    default_chunk_chars = 3600

    fields = [
        VoiceField(
            key="voice", label="Voice", kind=TEXT, default="", required=True,
            help="Voice name offered by the endpoint, e.g. marin, sage, shimmer.",
        ),
        VoiceField(
            key="speed", label="Speed", kind=NUMBER, default=1.0,
            min=0.25, max=4.0, step=0.05,
            help="1.0 is the voice's natural pace.",
        ),
        VoiceField(
            key="instructions", label="Delivery instructions", kind=LONGTEXT,
            default="",
            help="Free-text steering (OpenAI only; compatible servers ignore it).",
        ),
    ]

    def build_request(self, *, script, voice, cfg,
                      previous=None, next=None, seed=None) -> VoiceRequest:
        headers = {"Content-Type": "application/json"}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        body = {
            "model": cfg["model"],
            "voice": voice["voice"],
            "input": script,
            "speed": voice["speed"],
            "response_format": "mp3",
        }
        # Only when set — an empty string changes some servers' behavior.
        if voice.get("instructions"):
            body["instructions"] = voice["instructions"]
        return VoiceRequest(
            url=f"{cfg['base_url']}/audio/speech", headers=headers, json=body,
        )
