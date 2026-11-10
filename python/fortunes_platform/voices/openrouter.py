"""ElevenLabs voices through OpenRouter's /audio/speech.

OpenRouter serves ElevenLabs' models behind its OpenAI-shaped speech
endpoint, billed to an OpenRouter key (the one the readings already use)
instead of an ElevenLabs subscription:

    POST {base_url}/audio/speech
    Authorization: Bearer <key>
    {"model": "elevenlabs/eleven-multilingual-v2", "voice": "jessica",
     "input": ..., "response_format": "mp3",
     "provider": {"options": {"elevenlabs": {"voice_settings": {...},
                                             "seed": ..., ...}}}}

Voices are ElevenLabs' premade set, addressed BY NAME ("jessica", "lily"),
not by id. OpenRouter forwards `provider.options.elevenlabs` to
ElevenLabs as-is: verified live 2026-10-14 — without a seed two identical
requests came back different lengths, with the same seed and settings
they matched, and other settings or another seed changed the audio. So
stability/style/similarity, the seed (reproducible re-renders) and
previous/next text (prosody across chunk seams) all carry over from the
direct ElevenLabs adapter.

Speed travels inside voice_settings, as ElevenLabs takes it; OpenRouter's
top-level `speed` is honored only by some models and 400s on others.
"""

from __future__ import annotations

from fortunes_platform.voices.base import TEXT, Voice, VoiceAdapter, VoiceField, VoiceRequest
from fortunes_platform.voices.elevenlabs import ElevenLabsAdapter

_ELEVEN = ElevenLabsAdapter()


class OpenRouterAdapter(VoiceAdapter):
    name = "openrouter"
    label = "OpenRouter (ElevenLabs voices)"
    default_base_url = "https://openrouter.ai/api/v1"
    default_model = "elevenlabs/eleven-multilingual-v2"

    supports_listing = True
    supports_continuity = True
    supports_seed = True

    fields = [
        VoiceField(
            key="voice", label="Voice", kind=TEXT, default="", required=True,
            help="ElevenLabs premade voice by name, e.g. jessica, lily, alice.",
        ),
        *[f for f in _ELEVEN.fields if f.key != "voice_id"],
    ]

    def max_chunk_chars(self, model: str) -> int:
        # "elevenlabs/eleven-multilingual-v2" -> ElevenLabs' own cap table
        name = (model or "").strip().lower().removeprefix("elevenlabs/").replace("-", "_")
        return _ELEVEN.max_chunk_chars(name)

    def build_request(self, *, script, voice, cfg,
                      previous=None, next=None, seed=None) -> VoiceRequest:
        headers = {"Content-Type": "application/json"}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        options: dict = {
            "voice_settings": {
                "stability": voice["stability"],
                "similarity_boost": voice["similarity_boost"],
                "style": voice["style"],
                "speed": voice["speed"],
                "use_speaker_boost": bool(voice["use_speaker_boost"]),
            },
        }
        if seed is not None:
            options["seed"] = seed
        if previous:
            options["previous_text"] = previous
        if next:
            options["next_text"] = next
        body = {
            "model": cfg["model"],
            "voice": voice["voice"],
            "input": script,
            "response_format": "mp3",
            "provider": {"options": {"elevenlabs": options}},
        }
        return VoiceRequest(url=f"{cfg['base_url']}/audio/speech", headers=headers, json=body)

    async def list_voices(self, cfg: dict) -> list[Voice]:
        """The model's voice names, from OpenRouter's public model list."""
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{cfg['base_url']}/models",
                                    params={"output_modalities": "speech"})
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        model = next((m for m in rows if m.get("id") == cfg.get("model")), None)
        names = (model or {}).get("supported_voices") or []
        return [Voice(id=n, name=n.capitalize(), category="premade") for n in names if n]
