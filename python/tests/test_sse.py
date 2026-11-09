import json

from fortunes_platform.sse import SSE_HEADERS, sse


def test_a_dict_is_one_json_data_line_and_the_blank_line_that_ends_it():
    payload = json.dumps({"text": "a\nb"})
    assert sse("token", {"text": "a\nb"}) == "event: token\ndata: " + payload + "\n\n"


def test_a_multiline_string_is_one_data_line_per_line():
    assert sse("note", "one\ntwo") == "event: note\ndata: one\ndata: two\n\n"


def test_the_stream_is_never_cached_or_buffered():
    assert SSE_HEADERS["Cache-Control"] == "no-cache"
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"
