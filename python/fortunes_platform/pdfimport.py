"""Reading a book someone uploads as a PDF: the part of every app's library
that has nothing to do with its subject.

What an app's import knows is its own: which headings open a card's or a
hexagram's passage, and the shape a passage is kept in. Everything around
that is here, so a fix to it reaches every app:

- opening the file, and refusing what no import should try (no pages, too
  many pages);
- choosing the lane. A PDF with a text layer is read here, on the server:
  its lines with their sizes (`page_lines`), the book's own outline
  (`outline`) and a printed contents page (`contents_anchors`) as anchors. A
  scan has only pictures of its pages: they are rendered (`render_pages`) and
  a vision model the owner set up transcribes them (`transcribe`). That sends
  the pages to the AI provider, so it runs only with the owner's leave
  (`allow_vision`); without it the import stops with NeedsVision and nothing
  is sent;
- keeping the transcripts as they come (pages.jsonl), so an import that
  stops goes on later without paying twice, and checking the daily AI budget
  page by page;
- running the whole import in a child process that caps its own memory, CPU
  time and file size before it opens the PDF (`run_isolated`, with the child's
  side in `pdfworker`). A PDF is hostile input: a malformed one can at worst
  end its own import, never the server.

An app hands its two lanes to `read_pdf` and gets back what they made, with
the lane taken, the page count and the pages a vision model could not read.
Its import is one function, run by the child process:

    def import_book(pdf_path: Path, dest: Path, owner: str, name: str,
                    author: str | None, allow_vision: bool,
                    on_progress: Progress | None) -> dict: ...

It raises ImportFailed (or NeedsVision) with words for the owner, and writes
the book's manifest.yaml last: its presence means the import finished.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from base64 import b64encode
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_PAGES = 2000
TEXT_SAMPLE_PAGES = 20  # the lane is chosen from the first pages, judged by the pages sampled
TEXT_CHARS_PER_PAGE = 40  # fewer characters a page than this and the PDF is a scan
RENDER_SCALE = 2.0  # 144 dpi: legible to a reader and to a vision model
RENDER_MAX_SIDE = 1600  # px: and bounded, whatever page size a hostile file claims
PAGE_QUALITY = 80

# the child process's own limits (pdfworker)
WORKER_MEMORY = 4 * 1024 ** 3
WORKER_CPU_SECONDS = 1800
WORKER_FILE_SIZE = 1024 ** 3
WORKER_WALL_SECONDS = 6 * 3600  # a long scanned book, one model call a page

Progress = Callable[[str, int, int], None]  # (stage, done, total)


class ImportFailed(Exception):
    """An import that cannot go on, in words for the owner (an unreadable PDF,
    no vision model, a spent budget)."""


class NeedsVision(ImportFailed):
    """The PDF has no text layer, and reading it means sending its pages to the
    AI provider, which the owner has not allowed for this book."""


# --- the text lane ------------------------------------------------------------

@dataclass
class Line:
    """One line of a page. `size` is its font size (the median of its
    characters'), `height` the median height of its glyphs' boxes: an app
    tells headings from body text by whichever its books show best."""

    page: int
    text: str
    size: float = 0.0
    height: float = 0.0
    heading: bool = False  # set larger than the body, or a transcribed heading; the app decides
    para: bool = False  # starts a new paragraph


def heading_shaped(text: str) -> bool:
    """Does this line look like a heading, whatever its size? It starts with a
    capital or a numeral, is no list item, and does not end mid-clause. (A
    keyword list set a little taller than the body, or a bulleted name, would
    otherwise open a section of its own.)"""
    t = text.strip()
    if not t or t[:1].islower():
        return False
    if t[:1] in "•·‣▪●◦*–—-":
        return False
    return not t.endswith((",", ";", "-"))


def has_text_layer(doc) -> bool:
    """Whether the PDF's words are in it, judged from its first pages. The
    threshold scales with the pages sampled, not the book's length: scaling it
    by every page while sampling twenty sends every long text book to the
    vision model."""
    sample = min(len(doc), TEXT_SAMPLE_PAGES)
    chars = 0
    for i in range(sample):
        page = doc[i]
        tp = page.get_textpage()
        chars += tp.count_chars()
        tp.close()
        page.close()
    return sample > 0 and chars >= TEXT_CHARS_PER_PAGE * sample


def page_lines(doc, line: type[Line] = Line) -> list[list[Line]]:
    """Per page, its lines in content order, each with its size, its height and
    whether it starts a paragraph. Characters are grouped into lines by their
    vertical midpoint, walking the content stream: bucketing on the bottom edge
    would split any line with a descender (g, p and y sit below the baseline).
    `line` is the class made for each line, for an app that keeps more on it."""
    import pypdfium2.raw as pdfium_c

    pages: list[list[Line]] = []
    for pno in range(len(doc)):
        page = doc[pno]
        tp = page.get_textpage()
        raw: list[tuple[str, float, float, float, float]] = []  # text, size, height, top, bottom
        cur, cur_mid, cur_h, cur_sizes, cur_top, cur_bot = "", None, [], [], 0.0, 0.0

        def flush():
            nonlocal cur, cur_mid, cur_h, cur_sizes, cur_top, cur_bot
            if cur.strip() and cur_h:
                heights = sorted(cur_h)
                sizes = sorted(cur_sizes) or heights
                raw.append((" ".join(cur.split()), sizes[len(sizes) // 2], heights[len(heights) // 2],
                            cur_top, cur_bot))
            cur, cur_mid, cur_h, cur_sizes, cur_top, cur_bot = "", None, [], [], 0.0, 0.0

        for c in range(tp.count_chars()):
            u = pdfium_c.FPDFText_GetUnicode(tp.raw, c)
            # pdfium marks a hyphen where a word breaks across lines with a
            # control code (2; U+FFFE in its text ranges): it is a hyphen
            ch = "-" if u in (0x02, 0xFFFE) else chr(u) if 0 < u < 0x110000 else ""
            if ch in ("\r", "\n", ""):
                continue
            _, bottom, _, top = tp.get_charbox(c)
            h = top - bottom
            if ch.isspace() or h <= 0:
                cur += " "
                continue
            mid = (top + bottom) / 2
            size = pdfium_c.FPDFText_GetFontSize(tp.raw, c)
            if cur_mid is not None and abs(mid - cur_mid) <= 0.6 * max(h, *cur_h):
                cur += ch
                cur_h.append(h)
                cur_top, cur_bot = max(cur_top, top), min(cur_bot, bottom)
            else:
                flush()
                cur, cur_mid, cur_h, cur_top, cur_bot = ch, mid, [h], top, bottom
            if size > 0:
                cur_sizes.append(size)
        flush()
        tp.close()
        page.close()
        lines = []
        gaps = []
        for i, (text, size, height, top, bot) in enumerate(raw):
            lines.append(line(pno, text, size=size, height=height))
            # the baseline-to-baseline distance from the line before
            gaps.append(float(raw[i - 1][4] - bot) if i and raw[i - 1][4] > bot else 0.0)
        pages.append(list(zip(lines, gaps)))  # type: ignore[arg-type]
    _mark_paragraphs(pages)
    return pages


def _mark_paragraphs(pages: list) -> None:
    """A line starts a paragraph where the gap above it is wider than most body
    lines have (or it opens its page). Turns each page's (line, gap) pairs into
    lines, in place."""
    pitches = sorted(g for lines in pages for _, g in lines if g > 0)
    typical = pitches[len(pitches) // 4] if pitches else 0.0  # most body lines are this close
    for p, lines in enumerate(pages):
        out = []
        for i, (ln, gap) in enumerate(lines):
            ln.para = bool(i == 0 or (typical and gap > typical * 1.5) or gap <= 0)
            out.append(ln)
        pages[p] = out


def outline(doc, max_depth: int = 15, limit: int = 2000) -> list[tuple[str, int]]:
    """The PDF's bookmarks as (title, page), the strongest section signal a
    published book carries. Bookmarks built on GoTo actions have no direct
    destination and are resolved through the raw API. A book whose outline is
    junk (img001, img002...) simply has titles an app's names will not match."""
    try:
        entries = list(doc.get_toc(max_depth=max_depth))
    except Exception:
        return []
    out = []
    for e in entries[:limit]:
        try:
            title = e.get_title()
            dest = e.get_dest()
            page = dest.get_index() if dest else None
            if page is None:
                import pypdfium2.raw as pdfium_c

                action = pdfium_c.FPDFBookmark_GetAction(e.raw)
                if action:
                    raw_dest = pdfium_c.FPDFAction_GetDest(e.pdf.raw, action)
                    if raw_dest:
                        idx = pdfium_c.FPDFDest_GetDestPageIndex(e.pdf.raw, raw_dest)
                        page = idx if idx >= 0 else None
        except Exception:
            continue
        if page is not None and title:
            out.append((title, page))
    return out


# "The Chariot .... 132" | "Hanged Man, The, 78–81" | "4. Mêng … 9": a title,
# then the FIRST printed page of a number or a range.
CONTENTS_LINE = re.compile(r"^(.*?)(?:[\s.·…]{2,}|,\s+)(\d{1,4})(?:\s*[–—-]\s*\d{1,4})?$")

Classify = Callable[[str], "tuple[Any, float] | None"]


def contents_anchors(pages: list[list[Line]], classify: Classify, heading_pages: dict | None = None,
                     *, min_confidence: float) -> dict[int, tuple[Any, float, str, str]]:
    """{pdf page: (key, confidence, title, "contents")} from a printed contents
    page at the front or an index at the back. `classify` names what a title
    is about: (key, confidence), or None.

    Printed page numbers are not PDF indices. The offset is the commonest
    difference between a page's printed folio (a bare number as its first or
    last line) and its index, or between the contents and the headings an app
    has already found (`heading_pages`, {key: pdf page}), which covers an ebook
    with an index but no folios. Fewer than five votes for one offset and the
    printed numbers are not used. For each key the smallest printed page wins:
    an index cites a card on many pages, and its section starts at the first."""
    edge = max(6, len(pages) // 8)
    scan = list(pages[:edge]) + list(pages[-edge:])
    best: dict[Any, tuple[int, str]] = {}
    for lines in scan:
        for ln in lines:
            m = CONTENTS_LINE.match(ln.text.strip())
            if not m:
                continue
            title = m.group(1).strip(" ,")
            found = classify(title)
            if not found or found[0] is None or found[1] < min_confidence:
                continue
            key, num = found[0], int(m.group(2))
            if key not in best or num < best[key][0]:
                best[key] = (num, title)
    if not best:
        return {}
    offsets: Counter = Counter()
    for pno, lines in enumerate(pages):
        for ln in lines[:1] + lines[-1:]:
            t = ln.text.strip()
            if t.isdigit() and len(t) <= 4:
                offsets[pno - int(t)] += 1
    for key, (num, _) in best.items():
        if heading_pages and key in heading_pages:
            offsets[heading_pages[key] - num] += 1
    if not offsets:
        return {}
    offset, votes = offsets.most_common(1)[0]
    if votes < 5:
        return {}
    out: dict[int, tuple[Any, float, str, str]] = {}
    for key, (num, title) in best.items():
        page = num + offset
        if 0 <= page < len(pages) and page not in out:
            out[page] = (key, 0.9, title, "contents")
    return out


# --- the vision lane ----------------------------------------------------------

@dataclass(frozen=True)
class Vision:
    """What an app's vision lane says and where it is set up.

    `system` asks for each page as {"segments": [...]}, in the app's own shape.
    `page` goes with each page's image ({page} counts from 1). A change to
    `version` re-pays for the transcripts; a change to the app's segmenter
    does not. `setting` names where the model is chosen: the config file's
    `<setting>.vision_model`, then the admin's `<setting>_vision_model`, then
    the reader's own model, which must then read images. Each call is recorded
    in the usage ledger as `usage`."""

    system: str
    page: str = "Page {page} of the book."
    version: int = 1
    setting: str = "library"
    usage: str = "library_import"


def render_pages(doc, pages_dir: Path, on_progress: Progress | None = None) -> int:
    """Each page as pages/NNN.webp, at RENDER_SCALE but never more than
    RENDER_MAX_SIDE on its longer side. Renders are deterministic, so a page
    already there is kept."""
    pages_dir.mkdir(parents=True, exist_ok=True)
    n = len(doc)
    for i in range(n):
        out = pages_dir / f"{i:03d}.webp"
        if out.is_file():
            continue
        page = doc[i]
        try:
            w, h = page.get_size()
            scale = max(0.05, min(RENDER_SCALE, RENDER_MAX_SIDE / max(w, h, 1.0)))
            page.render(scale=scale).to_pil().save(out, quality=PAGE_QUALITY)
        finally:
            page.close()
        if on_progress:
            on_progress("render", i + 1, n)
    return n


def vision_config(vision: Vision) -> dict | None:
    """The reader's connection with the vision model in place of its own."""
    from fortunes_platform import ai, db
    from fortunes_platform import config as cfgfile

    cfg = ai.config()
    if not cfg:
        return None
    model = (cfgfile.get(vision.setting, "vision_model") or db.get_setting(f"{vision.setting}_vision_model")
             or cfg.get("model"))
    return {**cfg, "model": str(model)}


def chat(cfg: dict, messages: list[dict], owner: str, usage: str, max_tokens: int = 4000) -> str:
    """One call to the reader's model, recorded in the usage ledger as `usage`
    (accounting never breaks an import)."""
    import httpx

    from fortunes_platform import ai, db

    resp = httpx.post(f"{cfg['base_url']}/chat/completions", headers=ai._auth_headers(cfg),
                      json={"model": cfg["model"], "messages": messages, "max_tokens": max_tokens},
                      timeout=httpx.Timeout(180.0, connect=15.0))
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"] or ""
    used = data.get("usage") or {}
    try:
        db.record_usage(owner=owner, component="llm", kind=usage, model=cfg.get("model"),
                        prompt_tokens=used.get("prompt_tokens"), completion_tokens=used.get("completion_tokens"),
                        characters=len(text), audio_bytes=None)
    except Exception:
        pass
    return text


def json_block(text: str) -> dict | list | None:
    """The first JSON object or array in a model's reply, fenced or not."""
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    for stop in range(end + 1, start, -1):
        try:
            return json.loads(text[start:stop])
        except json.JSONDecodeError:
            continue
    return None


def transcribe(dest: Path, n: int, owner: str, vision: Vision,
               on_progress: Progress | None = None) -> tuple[list[list[dict]], list[int]]:
    """Each rendered page (dest/pages) as the segments the vision model returns,
    and the pages it could not read. A page read is kept in dest/pages.jsonl at
    once, so an import that stops goes on without paying for it again; a page
    that could not be read is not kept, so reading the book again tries it
    again. The daily AI budget is checked before each page."""
    import httpx

    from fortunes_platform import limits

    cfg = vision_config(vision)
    if not cfg:
        raise ImportFailed(
            "This PDF is scanned pages with no text, and no AI reader is set up to transcribe them. "
            f"Connect one in Settings (and set {vision.setting}.vision_model to a model that reads images).")
    cache_path = dest / "pages.jsonl"
    cached: dict[int, list[dict]] = {}
    if cache_path.is_file():
        for row in cache_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(row)
                if rec.get("v") == vision.version and isinstance(rec.get("segments"), list):
                    cached[int(rec["page"])] = rec["segments"]
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    per_page: list[list[dict]] = []
    failed: list[int] = []
    with cache_path.open("a", encoding="utf-8") as cache:
        for i in range(n):
            if i in cached:
                per_page.append(cached[i])
                continue
            try:
                limits.check_tokens(owner)
            except limits.LimitExceeded as e:
                raise ImportFailed(f"{e} The pages read so far are kept: read the book again to go on.")
            img = (dest / "pages" / f"{i:03d}.webp").read_bytes()
            url = "data:image/webp;base64," + b64encode(img).decode()
            segments = None
            for _ in range(2):
                try:
                    reply = chat(cfg, [
                        {"role": "system", "content": vision.system},
                        {"role": "user", "content": [
                            {"type": "text", "text": vision.page.format(page=i + 1)},
                            {"type": "image_url", "image_url": {"url": url}},
                        ]},
                    ], owner, vision.usage)
                except httpx.HTTPError as e:
                    if i == 0 and not cached:  # the first page failing: the model cannot read images
                        raise ImportFailed(
                            f"The vision model could not read the first page: is {vision.setting}.vision_model "
                            f"a model that reads images? ({e})")
                    continue
                parsed = json_block(reply)
                if isinstance(parsed, dict) and isinstance(parsed.get("segments"), list):
                    segments = parsed["segments"]
                    break
            if segments is None:
                failed.append(i)
                segments = []
            else:
                cache.write(json.dumps({"page": i, "v": vision.version, "segments": segments},
                                       ensure_ascii=False) + "\n")
                cache.flush()
            per_page.append(segments)
            if on_progress:
                on_progress("transcribe", i + 1, n)
    return per_page, failed


# --- the whole read -----------------------------------------------------------

@dataclass
class Read:
    """What `read_pdf` found: the lane's own result, and about the reading."""

    lane: str  # text | vision
    pages: int
    failed: list[int]
    result: Any


def read_pdf(pdf_path: Path, dest: Path, owner: str, *, text_lane: Callable[[Any], Any],
             vision_lane: Callable[[list[list[dict]]], Any], vision: Vision, allow_vision: bool = False,
             render_all: bool = False, on_progress: Progress | None = None) -> Read:
    """Open the PDF and read it by the lane it needs. `text_lane` is given the
    open pdfium document; `vision_lane` the transcribed pages. `render_all`
    renders every page whatever the lane, for an app that shows the pages."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        raise ImportFailed("This file could not be read as a PDF (it may be damaged or password-protected).")
    try:
        n = len(doc)
        if n == 0:
            raise ImportFailed("The PDF has no pages.")
        if n > MAX_PAGES:
            raise ImportFailed(f"The PDF has {n} pages; the library reads books of up to {MAX_PAGES}.")
        if render_all:
            render_pages(doc, dest / "pages", on_progress)
        failed: list[int] = []
        if has_text_layer(doc):
            if on_progress:
                on_progress("read", 0, n)
            result, lane = text_lane(doc), "text"
        else:
            if not allow_vision:
                raise NeedsVision(
                    "This PDF is scanned pages with no text layer. Reading it means sending its pages to your "
                    "AI provider to transcribe, once.")
            render_pages(doc, dest / "pages", on_progress)
            per_page, failed = transcribe(dest, n, owner, vision, on_progress)
            result, lane = vision_lane(per_page), "vision"
    finally:
        doc.close()
    return Read(lane, n, failed, result)


# --- the child process ----------------------------------------------------------

def run_isolated(target: str, pdf_path: Path, dest: Path, owner: str, name: str, author: str | None = None,
                 allow_vision: bool = False, on_progress: Progress | None = None,
                 timeout: float = WORKER_WALL_SECONDS) -> dict:
    """Run the app's import, `target` ("package.module:function"), in a child
    process that limits itself before it opens the PDF (pdfworker). Progress
    comes back as JSON lines. A crash, a limit reached or the timeout is an
    import that failed, never the server's.

    The child is started isolated (-I), from inside the book's folder, so
    nothing planted in an upload is ever imported; it imports from the
    directories this process does, less any inside the book's folder."""
    dest = Path(dest)
    inside = dest.resolve()
    path = [p for p in sys.path if p and os.path.isabs(p) and not Path(p).resolve().is_relative_to(inside)]
    args = json.dumps({"target": target, "path": path, "pdf": str(pdf_path), "dest": str(dest), "owner": owner,
                       "name": name, "author": author, "allow_vision": allow_vision})
    proc = subprocess.Popen([sys.executable, "-I", "-m", "fortunes_platform.pdfworker", args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=os.environ.copy(), cwd=str(dest))
    timer = threading.Timer(timeout, proc.kill)
    timer.daemon = True
    timer.start()
    errors: list[str] = []
    drain = threading.Thread(target=lambda: errors.append(proc.stderr.read().decode("utf-8", "replace")[-4000:]),
                             daemon=True)
    drain.start()
    result: dict | None = None
    failure: dict | None = None
    try:
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "progress" in msg and on_progress:
                stage, done, total = msg["progress"]
                on_progress(str(stage), int(done), int(total))
            elif "result" in msg:
                result = msg["result"]
            elif "error" in msg:
                failure = msg
        proc.wait()
    finally:
        timer.cancel()
        drain.join(timeout=5)
    if failure:
        if failure.get("needs_vision"):
            raise NeedsVision(failure["error"])
        raise ImportFailed(failure["error"])
    if result is None:
        rc = proc.returncode
        why = ("it took too long or needed too much memory" if rc == -9
               else f"the reader stopped on it (signal {-rc})" if rc and rc < 0 else "the reader gave no answer")
        raise ImportFailed(f"The PDF could not be read: {why}.")
    return result
