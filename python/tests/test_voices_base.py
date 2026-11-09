from fortunes_platform.voices.base import BOOL, NUMBER, SLIDER, TEXT, VoiceAdapter, VoiceField


class Plain(VoiceAdapter):
    fields = [
        VoiceField("voice", "Voice", TEXT, default="alloy", required=True),
        VoiceField("speed", "Speed", SLIDER, default=1.0, min=0.5, max=2.0),
        VoiceField("seed", "Seed", NUMBER),
        VoiceField("stream", "Stream", BOOL, default=False),
    ]


def test_a_stored_block_is_coerced_clamped_and_defaulted_never_raising():
    block = Plain().normalize({"speed": "9", "seed": "x", "stream": "yes", "unknown": 1})
    assert block == {"voice": "alloy", "speed": 2.0, "seed": None, "stream": True}


def test_a_missing_required_field_is_reported_by_key():
    class NoDefault(VoiceAdapter):
        fields = [VoiceField("voice", "Voice", TEXT, required=True)]

    assert NoDefault().missing_required({"voice": "  "}) == ["voice"]
    assert NoDefault().missing_required({"voice": "nova"}) == []
