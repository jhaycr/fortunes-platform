"""Reading an uploaded PDF: the lines of a text layer and what they say about a
book's structure, the lane a PDF needs, a scan transcribed by a vision model
(mocked here), and the child process an import runs in."""

import json
from pathlib import Path

import pytest

from fortunes_platform import db, limits, pdfimport as P
from fortunes_platform.testing import pdfgen as G

WORDS = "Words on the page, and plenty of them: enough to count as a text layer."


def _open(pdf: bytes):
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(pdf)


def _book(tmp_path: Path, pdf: bytes) -> Path:
    dest = tmp_path / "book"
    dest.mkdir(exist_ok=True)
    (dest / "source.pdf").write_bytes(pdf)
    return dest


# --- the text lane --------------------------------------------------------------

def test_lines_carry_their_size_height_and_paragraphs():
    doc = _open(G.text_pdf([[(G.TITLE, "The Title"), (0, ""), (G.BODY, "First line of a paragraph,"),
                             (G.BODY, "and its second line."), (0, ""), (G.BODY, "A new paragraph.")]]))
    [lines] = P.page_lines(doc)
    assert [ln.text for ln in lines] == ["The Title", "First line of a paragraph,", "and its second line.",
                                         "A new paragraph."]
    assert lines[0].size == pytest.approx(G.TITLE) and lines[1].size == pytest.approx(G.BODY)
    assert lines[0].height > lines[1].height > 0
    assert [ln.para for ln in lines] == [True, True, False, True]
    assert all(ln.page == 0 for ln in lines)


def test_a_hyphen_where_a_word_breaks_across_lines_is_a_hyphen():
    # pdfium marks it with a control code (2, or U+FFFE in its text ranges)
    doc = _open(G.text_pdf([[(G.BODY, "It keeps their out-of-"), (G.BODY, "the-ordinary quality.")]]))
    first, second = P.page_lines(doc)[0]
    assert first.text == "It keeps their out-of-" and second.text == "the-ordinary quality."
    assert not P.heading_shaped(first.text)  # a wrapped sentence, not a heading


def test_an_app_can_keep_more_on_each_line():
    from dataclasses import dataclass

    @dataclass
    class Marked(P.Line):
        mark: str | None = None

    doc = _open(G.text_pdf([[(G.BODY, "One line.")]]))
    assert isinstance(P.page_lines(doc, line=Marked)[0][0], Marked)


def test_what_looks_like_a_heading():
    assert P.heading_shaped("THE JUDGMENT") and P.heading_shaped("3. Difficulty")
    assert not P.heading_shaped("righting a wrong")  # lower case: a list item set a little large
    assert not P.heading_shaped("• JUSTICE")  # a bullet
    assert not P.heading_shaped("A sentence that wraps,")


def test_the_lane_a_pdf_needs():
    assert P.has_text_layer(_open(G.text_pdf([[(G.BODY, WORDS)]] * 3)))
    assert not P.has_text_layer(_open(G.image_pdf(2)))


def test_the_outline():
    doc = _open(G.text_pdf([[(G.BODY, "front")], [(G.BODY, "one")], [(G.BODY, "two")]],
                           outline=[("Chapter One", 1), ("Chapter Two", 2)]))
    assert P.outline(doc) == [("Chapter One", 1), ("Chapter Two", 2)]


def test_a_printed_contents_page_finds_sections_by_their_printed_numbers():
    names = {"Alpha": "a", "Beta": "b", "Gamma": "g"}

    def classify(title):
        key = names.get(title)
        return (key, 1.0) if key else None

    contents = [(G.TITLE, "Contents")] + [(G.BODY, f"{n} .......... {p}") for n, p in
                                          (("Alpha", 3), ("Beta", 5), ("Gamma", 7))]
    # printed folios: the book's page 1 is the PDF's page 2
    pages = [[(G.BODY, "A title page.")], [(G.BODY, "Front matter.")], contents]
    pages += [[(G.BODY, "Body text."), (G.BODY, str(n))] for n in range(1, 10)]
    doc = _open(G.text_pdf(pages))
    lines = P.page_lines(doc)
    found = P.contents_anchors(lines, classify, min_confidence=0.9)
    assert found == {5: ("a", 0.9, "Alpha", "contents"), 7: ("b", 0.9, "Beta", "contents"),
                     9: ("g", 0.9, "Gamma", "contents")}
    assert P.contents_anchors(lines, lambda t: None, min_confidence=0.9) == {}


# --- reading a whole PDF ----------------------------------------------------------

def _read(dest, **kw):
    kw.setdefault("vision", P.Vision(system="Transcribe.", page="Page {page} of the test book."))
    return P.read_pdf(dest / "source.pdf", dest, "robin",
                      text_lane=kw.pop("text_lane", lambda doc: "texted"),
                      vision_lane=kw.pop("vision_lane", lambda pages: pages), **kw)


def test_a_text_pdf_is_read_on_the_server(tmp_path):
    dest = _book(tmp_path, G.text_pdf([[(G.BODY, WORDS)]] * 2))
    seen = []
    read = _read(dest, on_progress=lambda *a: seen.append(a))
    assert (read.lane, read.pages, read.failed, read.result) == ("text", 2, [], "texted")
    assert seen == [("read", 0, 2)] and not (dest / "pages").exists()


def test_pages_rendered_for_an_app_that_shows_them(tmp_path):
    dest = _book(tmp_path, G.text_pdf([[(G.BODY, WORDS)]] * 2))
    _read(dest, render_all=True)
    from PIL import Image

    renders = sorted((dest / "pages").glob("*.webp"))
    assert [p.name for p in renders] == ["000.webp", "001.webp"]
    # 2x, never more than RENDER_MAX_SIDE on the longer side
    assert Image.open(renders[0]).size == (G.PAGE_W * 2, G.PAGE_H * 2)


def test_a_huge_page_is_rendered_within_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "RENDER_MAX_SIDE", 300)
    dest = _book(tmp_path, G.text_pdf([[(G.BODY, WORDS)]]))
    _read(dest, render_all=True)
    from PIL import Image

    assert max(Image.open(dest / "pages" / "000.webp").size) <= 300


def test_what_no_import_tries(tmp_path, monkeypatch):
    dest = _book(tmp_path, b"%PDF-1.7\n" + b"\x00garbage" * 100)
    with pytest.raises(P.ImportFailed, match="could not be read as a PDF"):
        _read(dest)
    monkeypatch.setattr(P, "MAX_PAGES", 3)
    dest = _book(tmp_path, G.text_pdf([[(G.BODY, "x")]] * 4))
    with pytest.raises(P.ImportFailed, match="up to 3"):
        _read(dest)


SCANNED = [[{"heading": "One", "text": "The first page."}], [{"heading": None, "text": "The second page."}]]


@pytest.fixture
def vision(monkeypatch, settings):
    """A vision model that reads the scanned pages above, its calls kept."""
    calls = []

    def fake_chat(cfg, messages, owner, usage, max_tokens=4000):
        assert cfg["model"] == "the-vision-model" and usage == "library_import"
        assert messages[0]["content"] == "Transcribe."
        words = messages[1]["content"][0]["text"]
        page = int(words.split()[1]) - 1
        assert words == f"Page {page + 1} of the test book."
        assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/webp;base64,")
        calls.append(page)
        return "```json\n" + json.dumps({"segments": SCANNED[page]}) + "\n```"

    from fortunes_platform import ai

    monkeypatch.setattr(ai, "config", lambda: {"base_url": "http://llm.test", "model": "reader", "api_key": ""})
    settings["library_vision_model"] = "the-vision-model"
    monkeypatch.setattr(P, "chat", fake_chat)
    return calls


def test_a_scan_waits_for_leave_and_nothing_is_sent(tmp_path, vision):
    dest = _book(tmp_path, G.image_pdf(2))
    with pytest.raises(P.NeedsVision):
        _read(dest)
    assert vision == []


def test_a_scan_read_by_the_vision_model_and_never_paid_for_twice(tmp_path, vision):
    dest = _book(tmp_path, G.image_pdf(2))
    seen = []
    read = _read(dest, allow_vision=True, on_progress=lambda *a: seen.append(a))
    assert (read.lane, read.pages, read.failed) == ("vision", 2, [])
    assert read.result == SCANNED and vision == [0, 1]
    assert ("transcribe", 2, 2) in seen and ("render", 2, 2) in seen
    vision.clear()
    assert _read(dest, allow_vision=True).result == SCANNED and vision == []
    # a new prompt is a new transcript
    _read(dest, allow_vision=True, vision=P.Vision(system="Transcribe.", page="Page {page} of the test book.",
                                                  version=2))
    assert vision == [0, 1]


def test_a_page_the_model_cannot_read_is_reported_and_tried_again(tmp_path, vision, monkeypatch):
    real = P.chat
    sorry = {1}

    def flaky(cfg, messages, owner, usage, max_tokens=4000):
        page = int(messages[1]["content"][0]["text"].split()[1]) - 1
        if page in sorry:
            vision.append(page)
            return "Sorry, I cannot read this page."
        return real(cfg, messages, owner, usage, max_tokens)

    monkeypatch.setattr(P, "chat", flaky)
    dest = _book(tmp_path, G.image_pdf(2))
    read = _read(dest, allow_vision=True)
    assert read.failed == [1] and read.result == [SCANNED[0], []]
    vision.clear()
    sorry.clear()
    read = _read(dest, allow_vision=True)
    assert read.failed == [] and read.result == SCANNED and vision == [1]  # only the page it lacked


def test_no_vision_model_is_a_clear_error(tmp_path, monkeypatch):
    from fortunes_platform import ai

    monkeypatch.setattr(ai, "config", lambda: None)
    dest = _book(tmp_path, G.image_pdf(1))
    with pytest.raises(P.ImportFailed, match="no AI reader"):
        _read(dest, allow_vision=True)


def test_the_daily_budget_holds_page_by_page(tmp_path, vision, monkeypatch):
    def spent(owner):
        if vision:  # after the first page
            raise limits.LimitExceeded("tokens")

    monkeypatch.setattr(limits, "check_tokens", spent)
    dest = _book(tmp_path, G.image_pdf(2))
    with pytest.raises(P.ImportFailed, match="pages read so far are kept"):
        _read(dest, allow_vision=True)
    assert vision == [0]
    monkeypatch.setattr(limits, "check_tokens", lambda owner: None)
    vision.clear()
    _read(dest, allow_vision=True)
    assert vision == [1]


def test_a_vision_call_is_recorded_as_the_apps_usage(app, monkeypatch):
    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{\"segments\": []}"}}],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 30}}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: Reply())
    text = P.chat({"base_url": "http://llm.test", "model": "v", "api_key": ""}, [], "robin", "book_import")
    assert P.json_block(text) == {"segments": []}
    with db.connect() as con:
        row = con.execute("SELECT owner, kind, model, prompt_tokens FROM ai_usage").fetchone()
    assert tuple(row) == ("robin", "book_import", "v", 1200)


def test_a_reply_with_its_json_somewhere_in_it():
    assert P.json_block('Here you are:\n```json\n{"a": [1, 2]}\n```\nAnything else?') == {"a": [1, 2]}
    assert P.json_block('Sure. {"a": 1} Hope that helps!') == {"a": 1}
    assert P.json_block("No JSON at all.") is None


# --- the child process --------------------------------------------------------------

def test_the_child_process_reads_and_reports_progress(tmp_path):
    dest = _book(tmp_path, G.text_pdf([[(G.BODY, WORDS)]] * 2))
    seen = []
    result = P.run_isolated("reader_fixture:import_book", dest / "source.pdf", dest, "robin", "A test book",
                            "An Author", on_progress=lambda *a: seen.append(a))
    assert result["lane"] == "text" and result["pages"] == 2 and result["lines"] == 2
    assert result["name"] == "A test book" and result["author"] == "An Author"
    assert seen == [("read", 0, 2)] and (dest / "manifest.yaml").is_file()


def test_nothing_in_the_books_folder_is_ever_imported(tmp_path):
    dest = _book(tmp_path, G.text_pdf([[(G.BODY, WORDS)]]))
    (dest / "reader_fixture.py").write_text("raise SystemExit('planted')\n")
    (dest / "json.py").write_text("raise SystemExit('planted')\n")
    result = P.run_isolated("reader_fixture:import_book", dest / "source.pdf", dest, "robin", "x")
    assert Path(result["from"]).parent != dest.resolve() and Path(result["cwd"]) == dest.resolve()
    assert not any(Path(p).resolve() == dest.resolve() for p in result["path"] if p)


def test_the_child_process_turns_failures_into_messages(tmp_path):
    dest = _book(tmp_path, b"%PDF-1.4\nnot really")
    with pytest.raises(P.ImportFailed, match="could not be read as a PDF"):
        P.run_isolated("reader_fixture:import_book", dest / "source.pdf", dest, "robin", "x")
    (dest / "source.pdf").write_bytes(G.image_pdf(1))
    with pytest.raises(P.NeedsVision):
        P.run_isolated("reader_fixture:import_book", dest / "source.pdf", dest, "robin", "x")
    with pytest.raises(P.ImportFailed, match="No thank you"):
        P.run_isolated("reader_fixture:refuses", dest / "source.pdf", dest, "robin", "x")
    with pytest.raises(P.ImportFailed, match="gave no answer"):
        P.run_isolated("reader_fixture:dies", dest / "source.pdf", dest, "robin", "x")
    with pytest.raises(P.ImportFailed, match="took too long"):
        P.run_isolated("reader_fixture:never_answers", dest / "source.pdf", dest, "robin", "x", timeout=0.5)
    with pytest.raises(P.ImportFailed, match="ModuleNotFoundError"):
        P.run_isolated("no_such_module:import_book", dest / "source.pdf", dest, "robin", "x")
