"""ElevenLabs text-to-speech.

Contract verified against the live docs 2026-08-15 (see
planning/voicing/research/elevenlabs-api.md):

    POST {base_url}/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128
    xi-api-key: <key>
    {"text": ..., "model_id": ..., "voice_settings": {...},
     "seed": ..., "previous_text": ..., "next_text": ...}

Differences from the OpenAI shape that the adapter boundary exists for:
the voice is a path segment, auth is a custom header, the text field is
`text`, the model field is `model_id`, `speed` lives inside
`voice_settings`, and the output format is a query parameter.

Two capabilities OpenAI has no equivalent for and we use:

* `previous_text`/`next_text` — a chunk can see its neighbours, so prosody
  doesn't reset at every seam. Long-form narration is exactly the case.
* `seed` — deterministic sampling, so a re-render of an evicted cache
  piece reproduces the previous audio instead of drifting audibly.

Per-request character caps are model-dependent and much larger than
OpenAI's; chunking to 3600 here would create seams for no reason.
"""

from __future__ import annotations

from fortunes_platform.voices.base import (
    BOOL,
    NUMBER,
    SLIDER,
    TEXT,
    DesignedVoice,
    Voice,
    VoiceAdapter,
    VoiceField,
    VoiceRequest,
)

# Voice design (text -> voice). Separate from the TTS model.
DESIGN_MODEL = "eleven_multilingual_ttv_v2"

# Published per-request limits, by model family. The floor applies to any
# model we don't recognize — guessing high would fail whole requests.
_MODEL_CAPS = (
    ("eleven_flash_v2_5", 40000),
    ("eleven_flash_v2", 30000),
    ("eleven_turbo_v2_5", 40000),
    ("eleven_turbo_v2", 30000),
    ("eleven_multilingual_v2", 10000),
    ("eleven_v3", 5000),
)
CAP_FLOOR = 5000

# Pinned so concatenated segments share codec parameters — the same
# property tts.py's concatenation already depends on.
OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsAdapter(VoiceAdapter):
    name = "elevenlabs"
    label = "ElevenLabs"
    default_base_url = "https://api.elevenlabs.io"
    default_model = "eleven_multilingual_v2"

    supports_listing = True
    supports_continuity = True
    supports_seed = True
    supports_design = True

    default_chunk_chars = CAP_FLOOR

    fields = [
        VoiceField(
            key="voice_id", label="Voice", kind=TEXT, default="", required=True,
            help="ElevenLabs voice id. Account-specific, so there is no default.",
        ),
        VoiceField(
            key="stability", label="Stability", kind=SLIDER, default=0.5,
            min=0.0, max=1.0, step=0.05,
            help="Lower is more expressive and variable; higher is steadier.",
        ),
        VoiceField(
            key="similarity_boost", label="Similarity", kind=SLIDER, default=0.75,
            min=0.0, max=1.0, step=0.05,
            help="How closely to adhere to the original voice.",
        ),
        VoiceField(
            key="style", label="Style", kind=SLIDER, default=0.0,
            min=0.0, max=1.0, step=0.05,
            help="Style exaggeration. Costs latency; 0 is neutral.",
        ),
        VoiceField(
            key="speed", label="Speed", kind=NUMBER, default=1.0,
            min=0.5, max=2.0, step=0.05,
        ),
        VoiceField(
            key="use_speaker_boost", label="Speaker boost", kind=BOOL, default=True,
        ),
    ]

    def max_chunk_chars(self, model: str) -> int:
        name = (model or "").strip().lower()
        for prefix, cap in _MODEL_CAPS:
            if name.startswith(prefix):
                return cap
        return CAP_FLOOR

    def build_request(self, *, script, voice, cfg,
                      previous=None, next=None, seed=None) -> VoiceRequest:
        headers = {"Content-Type": "application/json"}
        if cfg.get("api_key"):
            headers["xi-api-key"] = cfg["api_key"]
        body = {
            "text": script,
            "model_id": cfg["model"],
            "voice_settings": {
                "stability": voice["stability"],
                "similarity_boost": voice["similarity_boost"],
                "style": voice["style"],
                "speed": voice["speed"],
                "use_speaker_boost": bool(voice["use_speaker_boost"]),
            },
        }
        if seed is not None:
            body["seed"] = seed
        if previous:
            body["previous_text"] = previous
        if next:
            body["next_text"] = next
        return VoiceRequest(
            url=f"{cfg['base_url']}/v1/text-to-speech/{voice['voice_id']}",
            headers=headers,
            json=body,
            params={"output_format": OUTPUT_FORMAT},
        )

    async def list_voices(self, cfg: dict) -> list[Voice]:
        """The account's voices, for the settings picker. Paginates until
        exhausted so a large library isn't silently truncated."""
        import httpx

        headers = {"xi-api-key": cfg.get("api_key", "")}
        out: list[Voice] = []
        token: str | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(20):  # bounded: 20 * 100 voices is plenty
                params = {"page_size": "100"}
                if token:
                    params["next_page_token"] = token
                resp = await client.get(f"{cfg['base_url']}/v2/voices",
                                        headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()
                for v in data.get("voices", []):
                    out.append(Voice(
                        id=v.get("voice_id", ""),
                        name=v.get("name") or v.get("voice_id", ""),
                        category=v.get("category"),
                        description=v.get("description"),
                        preview_url=v.get("preview_url"),
                        labels=v.get("labels") or {},
                        settings=v.get("settings") or {},
                    ))
                if not data.get("has_more"):
                    break
                token = data.get("next_page_token")
                if not token:
                    break
        return [v for v in out if v.id]

    async def design_voices(self, cfg: dict, description: str,
                            preview_text: str | None = None) -> list[DesignedVoice]:
        """Candidate voices from a written description.

        The personas already carry one — the `instructions` text written for
        OpenAI's steering — so a persona's character survives the move to a
        provider with no equivalent field, just applied at design time
        instead of per request.
        """
        import httpx

        body: dict = {"voice_description": description,
                      "model_id": DESIGN_MODEL}
        if preview_text:
            body["text"] = preview_text
        else:
            body["auto_generate_text"] = True
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{cfg['base_url']}/v1/text-to-voice/design",
                headers={"xi-api-key": cfg.get("api_key", ""),
                         "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        out = []
        for p in data.get("previews", []):
            gid = p.get("generated_voice_id")
            audio = p.get("audio_base_64") or p.get("audio_base64")
            if gid and audio:
                out.append(DesignedVoice(
                    generated_voice_id=gid, audio_base64=audio,
                    media_type=p.get("media_type") or "audio/mpeg",
                    duration_secs=p.get("duration_secs"),
                ))
        return out

    async def keep_designed_voice(self, cfg, generated_voice_id, name,
                                  description, rejected=None) -> str:
        import httpx

        body = {
            "voice_name": name,
            "voice_description": description,
            "generated_voice_id": generated_voice_id,
        }
        if rejected:
            # Fed back for reinforcement learning; harmless to include.
            body["played_not_selected_voice_ids"] = rejected
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{cfg['base_url']}/v1/text-to-voice",
                headers={"xi-api-key": cfg.get("api_key", ""),
                         "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            return resp.json().get("voice_id", "")
