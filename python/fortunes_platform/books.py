"""The books people upload to an app's library, kept on the server: each
person's own, and the ones shared with everyone there. A book's text is its
owner's, so it lives only under the data directory, never in an image or a
public demo.

- a person's own   <data>/users/<user>/<folder>/<slug>  -> only them
- shared           <data>/<folder>/<slug>               -> everyone on the server

A book's folder holds the uploaded source.pdf, what the app's import made of
it, and manifest.yaml, which the import writes LAST: a folder without one is an
import that never finished, invisible to everyone and deletable by its owner.
upload.yaml beside it keeps what the person said when they uploaded it, so such
a folder can still be named, read again or deleted.

Sharing MOVES the folder into the shared library (a rename on one filesystem)
and records who shared it; stopping sharing moves it back to them. The app
hands in its folder's name, the manifest keys that record the sharing, and how
it reads a finished book (`Shelf`); its import runs as a background job
(`Jobs`), and `receive` takes an upload. Each slug is checked before it is
joined to a path, so no name from a request can reach outside a library.
"""

from __future__ import annotations

import re
import secrets
import shutil
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import yaml

from fortunes_platform import host

FORMER_MEMBER = "former member"  # who shared a book, once their account is gone
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$")
NEW_SLUG_LENGTH = 60

T = TypeVar("T")


class Conflict(Exception):
    """The slug is taken where the book would go."""


class Forbidden(Exception):
    """The caller may not do this to this book."""


class Busy(Exception):
    """This person already has an import running."""


def slugify(name: str) -> str:
    """A book's slug from its name: lower case, accents dropped, and each run of
    anything but a-z and 0-9 one dash, at most NEW_SLUG_LENGTH long."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", name.lower())
                     if not unicodedata.category(c).startswith("M"))
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:NEW_SLUG_LENGTH].strip("-")


def valid_slug(slug: str) -> bool:
    """Only ever [a-z0-9-], so ".." or a separator can never reach a path."""
    return bool(SLUG.match(slug or ""))


def user_segment(user: str) -> str:
    """A person's name as one path segment. Sign-in already refuses these; a
    reserved value would escape the per-person area, so refuse it here too."""
    if user in ("", ".", "..") or "/" in user or "\\" in user:
        raise ValueError(f"unsafe user identity: {user!r}")
    return user


def read_manifest(path: Path) -> dict | None:
    """A book's manifest, or None when it has none (an import that did not
    finish) or it cannot be read (a hand-edited one gone wrong hides the book
    rather than breaking the library)."""
    try:
        m = yaml.safe_load((path / "manifest.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if m is None:
        return {}
    return m if isinstance(m, dict) else None


def update_manifest(path: Path, **changes) -> None:
    """Set (or, with None, remove) manifest keys, keeping everything else. The
    new manifest replaces the old in one step."""
    mpath = path / "manifest.yaml"
    m = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    for k, v in changes.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    tmp = mpath.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(m, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(mpath)


def write_upload(dest: Path, name: str, author: str | None, owner: str) -> None:
    (dest / "upload.yaml").write_text(yaml.safe_dump(
        {"name": name, "author": author, "uploaded_by": owner, "uploaded_at": int(time.time())},
        sort_keys=False, allow_unicode=True), encoding="utf-8")


def read_upload(dest: Path) -> dict:
    try:
        u = yaml.safe_load((dest / "upload.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        u = {}
    return u if isinstance(u, dict) else {}


def may_edit(owner: str | None, shared_by: str | None, user: str, is_admin: bool) -> bool:
    """Who may change a book: its owner while it is theirs (`owner`), and once
    it is shared (`owner` None) the person who shared it, or an admin."""
    if owner is not None:
        return owner == user
    return shared_by == user or is_admin


# --- the shelf ----------------------------------------------------------------

class Shelf(Generic[T]):
    """One app's library on disk. `folder` names it under the data directory
    and each person's; `load(path, owner, manifest)` is the app's own view of a
    finished book (`owner` None for a shared one); `sharer` and `shared_at` are
    the manifest keys that record the sharing."""

    def __init__(self, folder: str, load: Callable[[Path, str | None, dict], T | None], *,
                 sharer: str = "shared_by", shared_at: str = "shared_at"):
        self.folder, self.load = folder, load
        self.sharer, self.shared_at = sharer, shared_at

    def root(self, user: str | None = None) -> Path:
        """The shared library (user None), or one person's own books."""
        if user is None:
            return host.data_dir() / self.folder
        return host.data_dir() / "users" / user_segment(user) / self.folder

    def place(self, user: str | None, slug: str) -> Path | None:
        """Where `slug` lives in the shared library or `user`'s own, or None
        for a slug that is not one."""
        return self.root(user) / slug if valid_slug(slug) else None

    def at(self, path: Path, owner: str | None) -> T | None:
        m = read_manifest(path)
        return None if m is None else self.load(path, owner, m)

    def discover(self, user: str | None) -> dict[str, T]:
        """The books `user` can see: the shared library and their own. Their own
        wins a slug both use."""
        found: dict[str, T] = {}
        roots = [(self.root(None), None)] + ([(self.root(user), user)] if user is not None else [])
        for root, owner in roots:
            if not root.is_dir():
                continue
            for p in sorted(root.iterdir()):
                if p.is_dir() and not p.is_symlink():
                    b = self.at(p, owner)
                    if b is not None:
                        found[p.name] = b
        return found

    def own_count(self, user: str) -> int:
        root = self.root(user)
        return sum(1 for p in root.iterdir() if p.is_dir()) if root.is_dir() else 0

    def unfinished(self, user: str, jobs: "Jobs") -> list[dict]:
        """`user`'s own folders whose import has not finished: running, failed,
        or waiting for leave to send a scan to the vision model."""
        root = self.root(user)
        out: list[dict] = []
        if not root.is_dir():
            return out
        for p in sorted(root.iterdir()):
            if not p.is_dir() or (p / "manifest.yaml").is_file():
                continue
            u = read_upload(p)
            job_id, job = jobs.latest(user, p.name)
            running = bool(job and not job["done"])
            out.append({"slug": p.name, "name": str(u.get("name") or p.name),
                        "author": str(u["author"]) if u.get("author") else None,
                        "status": "importing" if running else "unfinished",
                        "job": job_id if running else None,
                        "error": job["error"] if job else None,
                        "needs_vision": bool(job and job.get("needs_vision"))})
        return out

    def share(self, user: str, slug: str, now: int) -> T | None:
        """Move `user`'s own finished book into the shared library. None when
        they have no such book; Conflict when the shared library has the slug."""
        src, dest = self.place(user, slug), self.place(None, slug)
        if src is None or dest is None or not (src / "manifest.yaml").is_file():
            return None
        if dest.exists():
            raise Conflict(slug)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
        update_manifest(dest, **{self.sharer: user, self.shared_at: now})
        return self.at(dest, None)

    def unshare(self, user: str, slug: str, is_admin: bool) -> T | None:
        """Move a shared book back to the person who shared it (or, if they are
        gone, to the admin who unshares it). Only they or an admin may."""
        src = self.place(None, slug)
        m = read_manifest(src) if src is not None and (src / "manifest.yaml").is_file() else None
        if src is None or m is None:
            return None
        sharer = m.get(self.sharer)
        if not is_admin and sharer != user:
            raise Forbidden(slug)
        target = sharer if sharer and sharer != FORMER_MEMBER else user
        dest = self.root(target) / slug
        if dest.exists():
            raise Conflict(slug)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
        update_manifest(dest, **{self.sharer: None, self.shared_at: None})
        return self.at(dest, target)

    def delete(self, user: str, slug: str) -> bool:
        """Delete one of `user`'s own books, finished or not (a failed import's
        folder may hold only source.pdf). A shared book is unshared first, which
        keeps a delete inside the caller's own folder."""
        target = self.place(user, slug)
        if target is None or not target.is_dir() or target.is_symlink():
            return False
        shutil.rmtree(target)
        return True

    def move_private(self, src: str, dst: str) -> tuple[list[str], list[str]]:
        """Hand `src`'s own books to `dst`: (moved, left behind on a slug clash)."""
        moved, clashes = [], []
        root = self.root(src)
        if not root.is_dir():
            return moved, clashes
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            dest = self.root(dst) / p.name
            if dest.exists():
                clashes.append(p.name)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            p.rename(dest)
            moved.append(p.name)
        return moved, clashes

    def restamp_shared(self, src: str, dst: str) -> list[str]:
        """Credit the books `src` shared to `dst`, or to FORMER_MEMBER for an
        account that is gone."""
        out = []
        root = self.root(None)
        if not root.is_dir():
            return out
        for p in sorted(root.iterdir()):
            m = read_manifest(p) if p.is_dir() and (p / "manifest.yaml").is_file() else None
            if m is not None and m.get(self.sharer) == src:
                update_manifest(p, **{self.sharer: dst})
                out.append(p.name)
        return out


# --- importing ----------------------------------------------------------------

class Jobs:
    """Imports running in the background: one at a time for each person, at
    most `running` on the server (the rest wait their turn), and a finished
    one remembered for `keep` seconds so its page can still ask how it went."""

    def __init__(self, running: int = 2, keep: float = 3600):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._running = threading.Semaphore(running)
        self.keep = keep

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _prune(self) -> None:
        cutoff = time.time() - self.keep
        for k in [k for k, j in self._jobs.items() if j["done"] and j.get("finished", 0) < cutoff]:
            self._jobs.pop(k, None)

    def start(self, owner: str, slug: str, name: str, work: Callable[[Callable[[str, int, int], None]], dict],
              on_done: Callable[[], None] | None = None) -> str:
        """Run `work(progress)` in the background; returns the job's id. Busy when
        `owner` has an import running already. Failures become the job's
        error: an ImportFailed in its own words (NeedsVision also marks the job
        as waiting for leave), anything else as a plain failure."""
        from fortunes_platform import pdfimport

        with self._lock:
            self._prune()
            if any(j["owner"] == owner and not j["done"] for j in self._jobs.values()):
                raise Busy(owner)
            job_id = secrets.token_hex(8)
            job = {"owner": owner, "slug": slug, "name": name, "stage": "queued", "page": 0, "pages": 0,
                   "done": False, "error": None, "needs_vision": False, "result": None, "started": time.time()}
            self._jobs[job_id] = job

        def progress(stage: str, done: int, total: int) -> None:
            job["stage"], job["page"], job["pages"] = stage, done, total

        def run() -> None:
            with self._running:
                job["stage"] = "starting"
                try:
                    job["result"] = work(progress)
                except pdfimport.NeedsVision as e:
                    job["error"], job["needs_vision"] = str(e), True
                except pdfimport.ImportFailed as e:
                    job["error"] = str(e)
                except Exception as e:  # noqa: BLE001
                    job["error"] = f"The import failed ({e.__class__.__name__})."
                finally:
                    if on_done:
                        try:
                            on_done()
                        except Exception:
                            pass
                    job["stage"] = "done"
                    job["done"] = True
                    job["finished"] = time.time()

        threading.Thread(target=run, name=f"book-import-{job_id}", daemon=True).start()
        return job_id

    def latest(self, user: str, slug: str) -> tuple[str | None, dict | None]:
        """The most recent import of `user`'s book `slug`, if this process ran one."""
        found = [(k, j) for k, j in self._jobs.items() if j["owner"] == user and j["slug"] == slug]
        if not found:
            return None, None
        return max(found, key=lambda kj: kj[1].get("started", 0))

    def running(self, user: str, slug: str) -> bool:
        _, job = self.latest(user, slug)
        return bool(job and not job["done"])

    def view(self, job_id: str, user: str) -> dict | None:
        """What `user` may see of one of their own jobs."""
        job = self._jobs.get(job_id)
        if not job or job["owner"] != user:
            return None
        return {k: v for k, v in job.items() if k not in ("owner", "finished", "started")}


# --- taking an upload -----------------------------------------------------------

@dataclass
class Upload:
    """A PDF taken in, written to `dest` (source.pdf and upload.yaml)."""

    dest: Path
    slug: str
    name: str
    author: str | None
    allow_vision: bool


def max_upload_bytes(setting: str, default_mb: float = 100) -> int:
    """The largest PDF a library takes: the config file's `<setting>.max_upload_mb`,
    between 1 MB and 1 GB."""
    from fortunes_platform import config as cfgfile

    try:
        mb = float(cfgfile.get(setting, "max_upload_mb") or default_mb)
    except (TypeError, ValueError):
        mb = default_mb
    return int(max(1, min(mb, 1024)) * 1024 * 1024)


async def receive(request, user: str, shelf: Shelf, *, max_bytes: int, max_books: int | None = 50,
                  noun: str = "text") -> Upload:
    """Take a PDF upload for `user`'s own books. The body is refused unread when
    it says it is too big; it is parsed with one file and a few short fields
    at most; the file must say it is a PDF; and its name becomes a slug that
    can only ever be one folder. Raises HTTPException with words for the
    person (`noun` is what the app calls a book)."""
    from fastapi import HTTPException
    from starlette.concurrency import run_in_threadpool
    from starlette.datastructures import UploadFile

    too_big = f"The PDF is too large: the library takes files of up to {max_bytes // (1024 * 1024)} MB."
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise HTTPException(411, "the upload must say how long it is")
    if int(length) > max_bytes + 64 * 1024:
        raise HTTPException(413, too_big)
    if max_books is not None and await run_in_threadpool(shelf.own_count, user) >= max_books:
        raise HTTPException(409, f"You have {max_books} {noun}s already. Delete one first.")
    form = await request.form(max_files=1, max_fields=6, max_part_size=4096)
    file = form.get("file")
    name = " ".join(str(form.get("name") or "").split())[:120]
    author = " ".join(str(form.get("author") or "").split())[:120] or None
    allow_vision = str(form.get("allow_vision") or "").lower() in ("1", "true", "on", "yes")
    if not isinstance(file, UploadFile):
        raise HTTPException(400, "choose a PDF to upload")
    if not name:
        name = Path(file.filename or "").stem[:120].strip() or "Untitled"
    slug = slugify(str(form.get("slug") or "") or name)
    if not valid_slug(slug):
        raise HTTPException(400, f"give the {noun} a name with some letters or numbers in it")
    data = await file.read(max_bytes + 1)
    await file.close()
    if len(data) > max_bytes:
        raise HTTPException(413, too_big)
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(400, "That file is not a PDF.")
    dest = shelf.root(user) / slug
    if dest.exists():
        raise HTTPException(409, f"You already have a {noun} called '{slug}'. Give this one another name.")

    def store() -> None:
        dest.mkdir(parents=True)
        (dest / "source.pdf").write_bytes(data)
        write_upload(dest, name, author, user)

    await run_in_threadpool(store)
    return Upload(dest, slug, name, author, allow_vision)
