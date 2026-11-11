"""The AI call and speech read the app's prompts and voices as handed in, and
its settings under its own prefix."""

from fortunes_platform import ai, db, tts


def test_readers_come_from_the_app_prompts():
    assert set(ai.personas()) == {"sage", "imp"}
    assert ai.personas()["imp"]["prompt"] == "You are playful."
    assert ai.default_persona() == "sage"
    assert ai.default_max_tokens() == 700


def test_the_config_file_can_choose_another_reader_but_not_an_unknown_one(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("interpretation:\n  default_persona: imp\n")
    assert ai.default_persona() == "imp"
    (tmp_path / "config.yaml").write_text("interpretation:\n  default_persona: nobody\n")
    assert ai.default_persona() == "sage"


def test_the_connection_resolves_file_then_database_then_environment(tmp_path, monkeypatch):
    assert ai.config() is None, "no base URL: the feature is off"
    monkeypatch.setenv("TESTAPP_LLM_BASE_URL", "http://env.test/v1/")
    monkeypatch.setenv("TESTAPP_LLM_MODEL", "env-model")
    cfg = ai.config()
    assert cfg["base_url"] == "http://env.test/v1" and cfg["model"] == "env-model"
    assert cfg["max_tokens"] == 700, "the app's default cap"
    db.set_setting("llm_base_url", "http://db.test/v1")
    assert ai.config()["base_url"] == "http://db.test/v1"
    (tmp_path / "config.yaml").write_text("llm:\n  base_url: http://file.test/v1\n  max_tokens: 50\n")
    assert ai.config()["base_url"] == "http://file.test/v1" and ai.config()["max_tokens"] == 50


def test_the_shipped_voices_are_the_app_ones():
    assert set(tts.VOICE_DEFAULTS) == {"sage", "imp"}
    assert tts.VOICE_DEFAULTS["imp"]["voice"] == "nova"
    assert tts.resolve_voice("imp", "sam")["voice"] == "nova"
    assert tts.resolve_voice("nobody", "sam")["voice"] == "onyx", "an unknown reader gets the default one's"
    assert tts.persona_description("imp") == "Quick and bright."


def test_speech_settings_use_the_app_prefix(monkeypatch):
    monkeypatch.setenv("TESTAPP_TTS_PROVIDER", "elevenlabs")
    assert tts.provider_name() == "elevenlabs"
    monkeypatch.setenv("TESTAPP_TTS_MODEL_ELEVENLABS", "eleven-x")
    assert tts.env_setting("model", "elevenlabs") == "eleven-x"


def test_the_audio_cache_lives_in_the_app_data_directory(tmp_path):
    assert tts.cache_dir() == tmp_path / "tts-cache"
