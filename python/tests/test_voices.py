"""The voice registry and the OpenRouter adapter's request shape."""

from fortunes_platform import voices


def test_openrouter_is_registered_with_elevenlabs_tuning_fields():
    a = voices.get_adapter("openrouter")
    assert a.name == "openrouter" and voices.known("openrouter")
    assert a.default_base_url == "https://openrouter.ai/api/v1"
    assert a.default_model == "elevenlabs/eleven-multilingual-v2"
    keys = [f.key for f in a.fields]
    assert keys == ["voice", "stability", "similarity_boost", "style", "speed", "use_speaker_boost"]
    assert a.supports_seed and a.supports_continuity
    assert a.max_chunk_chars("elevenlabs/eleven-multilingual-v2") == 10000
    assert a.max_chunk_chars("elevenlabs/eleven-flash-v2.5") >= 5000


def test_openrouter_request_carries_voice_settings_seed_and_continuity():
    a = voices.get_adapter("openrouter")
    voice = a.normalize({"voice": "jessica", "stability": 0.35, "style": 0.35})
    req = a.build_request(
        script="The Star.", voice=voice,
        cfg={"base_url": "https://openrouter.ai/api/v1", "model": "elevenlabs/eleven-multilingual-v2", "api_key": "k"},
        previous="Before.", next="After.", seed=7,
    )
    assert req.url == "https://openrouter.ai/api/v1/audio/speech"
    assert req.headers["Authorization"] == "Bearer k"
    assert req.json["voice"] == "jessica" and req.json["input"] == "The Star."
    assert "speed" not in req.json
    opts = req.json["provider"]["options"]["elevenlabs"]
    assert opts["seed"] == 7 and opts["previous_text"] == "Before." and opts["next_text"] == "After."


def test_an_unknown_provider_falls_back_to_the_default():
    assert voices.get_adapter("nonsense").name == voices.DEFAULT_PROVIDER
    assert not voices.known("") and not voices.known(None)
