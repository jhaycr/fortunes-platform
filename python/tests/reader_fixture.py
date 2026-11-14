"""Imports for run_isolated's tests to run in its child process. No app stands
behind them, so they use nothing that needs one configured."""

import os
import sys
import time
from pathlib import Path

from fortunes_platform import pdfimport


def import_book(pdf_path: Path, dest: Path, owner: str, name: str, author, allow_vision, on_progress=None):
    read = pdfimport.read_pdf(pdf_path, dest, owner,
                              text_lane=lambda doc: sum(len(p) for p in pdfimport.page_lines(doc)),
                              vision_lane=lambda pages: 0, vision=pdfimport.Vision(system="unused"),
                              allow_vision=allow_vision, on_progress=on_progress)
    (dest / "manifest.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return {"lane": read.lane, "pages": read.pages, "lines": read.result, "name": name, "author": author,
            "from": __file__, "cwd": os.getcwd(), "path": list(sys.path)}


def never_answers(pdf_path, dest, owner, name, author, allow_vision, on_progress=None):
    time.sleep(60)


def dies(pdf_path, dest, owner, name, author, allow_vision, on_progress=None):
    os._exit(3)


def refuses(pdf_path, dest, owner, name, author, allow_vision, on_progress=None):
    raise pdfimport.ImportFailed("No thank you.")
