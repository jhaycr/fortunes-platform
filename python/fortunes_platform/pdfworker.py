"""The child process `pdfimport.run_isolated` starts: it limits its own memory,
CPU time and file size, then runs the app's import and reports back as JSON
lines on stdout (progress, then a result or an error). It is a module of its
own so the error classes it catches are the very ones the app's import
raises (run with -m, a module's own classes would be __main__'s copies)."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

from fortunes_platform import pdfimport


def _limit_self() -> None:
    try:
        import resource
    except ImportError:  # not on Linux: no limits to set
        return
    for which, cap in ((resource.RLIMIT_AS, pdfimport.WORKER_MEMORY),
                       (resource.RLIMIT_CPU, pdfimport.WORKER_CPU_SECONDS),
                       (resource.RLIMIT_FSIZE, pdfimport.WORKER_FILE_SIZE)):
        try:
            soft, hard = resource.getrlimit(which)
            value = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            pass
    try:
        os.nice(10)
    except OSError:
        pass


def main(argv: list[str]) -> int:
    _limit_self()
    out = sys.stdout
    sys.stdout = sys.stderr  # nothing but the protocol on the real stdout

    def send(msg: dict) -> None:
        out.write(json.dumps(msg, ensure_ascii=False) + "\n")
        out.flush()

    try:
        a = json.loads(argv[0])
        path = [p for p in a.get("path") or [] if isinstance(p, str)]
        sys.path[:] = path + [p for p in sys.path if p not in path]
        module, _, func = str(a["target"]).partition(":")
        import_book = getattr(importlib.import_module(module), func)
        result = import_book(Path(a["pdf"]), Path(a["dest"]), a["owner"], a["name"], a.get("author"),
                             bool(a.get("allow_vision")),
                             on_progress=lambda s, d, t: send({"progress": [s, d, t]}))
        send({"result": result})
        return 0
    except pdfimport.NeedsVision as e:
        send({"error": str(e), "needs_vision": True})
    except pdfimport.ImportFailed as e:
        send({"error": str(e)})
    except MemoryError:
        send({"error": "The PDF could not be read: it needed more memory than an import is allowed."})
    except Exception as e:  # noqa: BLE001 — a hostile file can raise anything
        send({"error": f"The PDF could not be read ({e.__class__.__name__})."})
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
