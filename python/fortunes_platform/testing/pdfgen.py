"""Small PDFs for tests of a library's import, written by hand: a text layer in
Helvetica (WinAnsi, so accented letters such as ê and ü survive), with an
outline if asked, or pages that are only pictures, as a scanned book is.
Nothing here is anyone's book: the passages a test lays out are its own."""
from __future__ import annotations

import io
import zlib
from pathlib import Path

PAGE_W, PAGE_H = 432, 648  # a 6 x 9 inch trade paperback
MARGIN = 54

# A page is a list of (size, text) lines, laid out top to bottom; a size of 0 is
# a blank gap the height of a body line.
Page = list[tuple[float, str]]


def _escape(s: str) -> bytes:
    raw = s.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def text_pdf(pages: list[Page], outline: list[tuple[str, int]] | None = None) -> bytes:
    """A PDF whose text layer is `pages`. `outline` adds bookmarks (title, page)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in below
    pages_id = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for lines in pages:
        y = PAGE_H - MARGIN
        ops = []
        for size, text in lines:
            if not size:
                y -= 14
                continue
            y -= size * 1.35
            face = b"/F2" if size >= 13 else b"/F1"
            ops.append(b"BT " + face + b" %.1f Tf %d %.1f Td (" % (size, MARGIN, y) + _escape(text) + b") Tj ET")
        stream = zlib.compress(b"\n".join(ops))
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                        b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                        % (pages_id, PAGE_W, PAGE_H, content, font, bold)))
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    extra = b""
    if outline:
        root = add(b"")
        items = []
        for title, page in outline:
            items.append(add(b""))
        for i, (title, page) in enumerate(outline):
            links = b""
            if i > 0:
                links += b" /Prev %d 0 R" % items[i - 1]
            if i < len(items) - 1:
                links += b" /Next %d 0 R" % items[i + 1]
            objects[items[i] - 1] = (b"<< /Title (" + _escape(title) + b") /Parent %d 0 R /Dest [%d 0 R /Fit]%s >>"
                                     % (root, kids[page], links))
        objects[root - 1] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (
            items[0], items[-1], len(items))
        extra = b" /Outlines %d 0 R" % root
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R%s >>" % (pages_id, extra)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref))
    return out.getvalue()


def image_pdf(n_pages: int) -> bytes:
    """A scanned book: `n_pages` pages that are pictures, with no text layer."""
    from PIL import Image, ImageDraw

    images = []
    for i in range(n_pages):
        im = Image.new("RGB", (PAGE_W, PAGE_H), "white")
        d = ImageDraw.Draw(im)
        for row in range(12):  # grey bars where the print would be
            d.rectangle([MARGIN, 80 + row * 40, PAGE_W - MARGIN - (i * 7 + row * 13) % 90, 92 + row * 40], fill="#555")
        images.append(im)
    buf = io.BytesIO()
    images[0].save(buf, "PDF", save_all=True, append_images=images[1:], resolution=72)
    return buf.getvalue()


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# --- sizes a test book is set in -----------------------------------------------

BODY = 10.5
TITLE = 18
PART = 12.5
