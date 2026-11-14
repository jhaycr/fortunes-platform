"""A library's books on disk: a person's own and the shared ones, sharing and
deleting, handing them on, the import jobs that read them, and taking an
upload."""

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from fortunes_platform import books as B
from fortunes_platform import pdfimport


@dataclass
class Book:
    slug: str
    name: str
    owner: str | None
    shared_by: str | None


def _load(path: Path, owner: str | None, m: dict) -> Book:
    return Book(path.name, str(m.get("name") or path.name), owner, m.get("shared_by") if owner is None else None)


@pytest.fixture
def shelf(app):
    return B.Shelf("library", _load)


def finished(shelf, user, slug, name="A Book", **manifest):
    dest = shelf.root(user) / slug
    dest.mkdir(parents=True)
    (dest / "source.pdf").write_bytes(b"%PDF-1.4\n")
    (dest / "manifest.yaml").write_text(yaml.safe_dump({"name": name, **manifest}))
    return dest


def test_where_books_live(shelf, tmp_path):
    assert shelf.root() == tmp_path / "library"
    assert shelf.root("robin") == tmp_path / "users" / "robin" / "library"
    for bad in ("", ".", "..", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            shelf.root(bad)


def test_slugs():
    assert B.slugify("Viéville's Tarot (Paris, c. 1650)") == "vieville-s-tarot-paris-c-1650"
    assert len(B.slugify("word " * 40)) <= B.NEW_SLUG_LENGTH and not B.slugify("word " * 40).endswith("-")
    assert B.slugify("!!!") == ""
    for good in ("a", "the-fool", "x" * 120):
        assert B.valid_slug(good)
    for bad in ("", "..", ".", "a/b", "-a", "a-", "A", "a_b", "x" * 121, "a b"):
        assert not B.valid_slug(bad)


def test_a_person_sees_the_shared_books_and_their_own(shelf):
    finished(shelf, None, "shared-one", "Shared", shared_by="ann")
    finished(shelf, None, "both", "The shared one")
    finished(shelf, "robin", "both", "Robin's own")
    finished(shelf, "ann", "anns", "Ann's own")
    (shelf.root("robin") / "unfinished").mkdir()  # no manifest yet
    seen = shelf.discover("robin")
    assert sorted(seen) == ["both", "shared-one"]
    assert seen["both"].name == "Robin's own" and seen["both"].owner == "robin"
    assert seen["shared-one"].owner is None and seen["shared-one"].shared_by == "ann"
    assert sorted(shelf.discover(None)) == ["both", "shared-one"]


def test_a_manifest_gone_wrong_hides_its_book(shelf):
    dest = finished(shelf, "robin", "broken")
    (dest / "manifest.yaml").write_text("name: [unclosed\n")
    assert shelf.discover("robin") == {}


def test_sharing_and_taking_back(shelf):
    finished(shelf, "robin", "mine")
    shared = shelf.share("robin", "mine", 1000)
    assert shared.owner is None and shared.shared_by == "robin"
    m = yaml.safe_load((shelf.root() / "mine" / "manifest.yaml").read_text())
    assert m["shared_by"] == "robin" and m["shared_at"] == 1000 and m["name"] == "A Book"
    assert not (shelf.root("robin") / "mine").exists()
    with pytest.raises(B.Forbidden):
        shelf.unshare("ann", "mine", is_admin=False)
    back = shelf.unshare("robin", "mine", is_admin=False)
    assert back.owner == "robin" and "shared_by" not in yaml.safe_load(
        (shelf.root("robin") / "mine" / "manifest.yaml").read_text())
    assert shelf.share("robin", "nothing", 1) is None and shelf.unshare("robin", "nothing", False) is None


def test_a_slug_taken_where_the_book_would_go(shelf):
    finished(shelf, "robin", "same")
    finished(shelf, None, "same")
    with pytest.raises(B.Conflict):
        shelf.share("robin", "same", 1)


def test_a_former_members_book_goes_to_the_admin_who_takes_it_back(shelf):
    finished(shelf, None, "left-behind", shared_by=B.FORMER_MEMBER)
    with pytest.raises(B.Forbidden):
        shelf.unshare("robin", "left-behind", is_admin=False)
    assert shelf.unshare("ada", "left-behind", is_admin=True).owner == "ada"


def test_the_apps_own_words_for_sharing(app):
    shelf = B.Shelf("books", _load, sharer="published_by", shared_at="published_at")
    finished(shelf, "robin", "guide")
    shelf.share("robin", "guide", 7)
    m = yaml.safe_load((shelf.root() / "guide" / "manifest.yaml").read_text())
    assert m["published_by"] == "robin" and m["published_at"] == 7 and "shared_by" not in m
    assert shelf.unshare("robin", "guide", False).owner == "robin"


def test_deleting_stays_inside_the_callers_own_books(shelf, tmp_path):
    finished(shelf, "robin", "mine")
    finished(shelf, None, "shared")
    (shelf.root("robin") / "half-read").mkdir()  # a failed import: only source.pdf, or nothing
    assert shelf.delete("robin", "half-read") and shelf.delete("robin", "mine")
    assert not shelf.delete("robin", "shared")  # shared: take it back first
    assert (shelf.root() / "shared").is_dir()
    for bad in ("..", ".", "", "../../users"):
        assert not shelf.delete("robin", bad)
    assert (tmp_path / "users" / "robin").is_dir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (shelf.root("robin") / "link").symlink_to(outside)
    assert not shelf.delete("robin", "link") and outside.is_dir()


def test_handing_on_and_leaving(shelf):
    finished(shelf, "ann", "one")
    finished(shelf, "ann", "clash")
    finished(shelf, "ben", "clash")
    finished(shelf, None, "anns-shared", shared_by="ann")
    assert shelf.move_private("ann", "ben") == (["one"], ["clash"])
    assert sorted(shelf.discover("ben")) == ["anns-shared", "clash", "one"]
    assert shelf.restamp_shared("ann", "ben") == ["anns-shared"]
    assert shelf.discover(None)["anns-shared"].shared_by == "ben"
    assert shelf.restamp_shared("ben", B.FORMER_MEMBER) == ["anns-shared"]


def test_updating_a_manifest_keeps_the_rest(shelf):
    dest = finished(shelf, "robin", "b", "Name", author="Someone", kind="guide")
    B.update_manifest(dest, name="New Name", author=None)
    assert yaml.safe_load((dest / "manifest.yaml").read_text()) == {"name": "New Name", "kind": "guide"}


def test_who_may_edit():
    assert B.may_edit("robin", None, "robin", False) and not B.may_edit("robin", None, "ann", True)
    assert B.may_edit(None, "robin", "robin", False) and B.may_edit(None, "robin", "ada", True)
    assert not B.may_edit(None, "robin", "ann", False)


# --- jobs ----------------------------------------------------------------------

def _wait(jobs, job_id, user="robin"):
    for _ in range(200):
        v = jobs.view(job_id, user)
        if v["done"]:
            return v
        time.sleep(0.01)
    raise AssertionError("the job never finished")


def test_a_job_runs_and_reports(app):
    jobs, done = B.Jobs(), []

    def work(progress):
        progress("read", 3, 10)
        return {"found": 4}

    v = _wait(jobs, jobs.start("robin", "b", "A Book", work, on_done=lambda: done.append(1)))
    assert v["result"] == {"found": 4} and v["error"] is None and v["stage"] == "done" and v["page"] == 3
    assert "owner" not in v and done == [1]
    job_id, _ = jobs.latest("robin", "b")
    assert jobs.view(job_id, "ann") is None and not jobs.running("robin", "b")


def test_failures_become_the_jobs_words(app):
    jobs = B.Jobs()

    def needs(progress):
        raise pdfimport.NeedsVision("Ask first.")

    def fails(progress):
        raise pdfimport.ImportFailed("Too many pages.")

    def breaks(progress):
        raise KeyError("x")

    v = _wait(jobs, jobs.start("robin", "a", "A", needs))
    assert v["error"] == "Ask first." and v["needs_vision"]
    v = _wait(jobs, jobs.start("robin", "b", "B", fails))
    assert v["error"] == "Too many pages." and not v["needs_vision"]
    v = _wait(jobs, jobs.start("robin", "c", "C", breaks))
    assert v["error"] == "The import failed (KeyError)."


def test_one_import_at_a_time_each(app):
    jobs, gate = B.Jobs(), threading.Event()
    first = jobs.start("robin", "a", "A", lambda progress: gate.wait(5) and {})
    assert jobs.running("robin", "a")
    with pytest.raises(B.Busy):
        jobs.start("robin", "b", "B", lambda progress: {})
    other = jobs.start("ann", "c", "C", lambda progress: {})  # someone else's goes ahead
    gate.set()
    _wait(jobs, first)
    _wait(jobs, other, "ann")
    jobs.start("robin", "b", "B", lambda progress: {})


def test_unfinished_imports(shelf):
    jobs, gate = B.Jobs(), threading.Event()
    for slug in ("failed", "running"):
        dest = shelf.root("robin") / slug
        dest.mkdir(parents=True)
        B.write_upload(dest, slug.title(), "Someone", "robin")
    finished(shelf, "robin", "done")
    _wait(jobs, jobs.start("robin", "failed", "Failed", lambda p: (_ for _ in ()).throw(
        pdfimport.NeedsVision("A scan."))))
    running = jobs.start("robin", "running", "Running", lambda p: gate.wait(5) and {})
    rows = {u["slug"]: u for u in shelf.unfinished("robin", jobs)}
    assert sorted(rows) == ["failed", "running"]
    assert rows["failed"] == {"slug": "failed", "name": "Failed", "author": "Someone", "status": "unfinished",
                              "job": None, "error": "A scan.", "needs_vision": True}
    assert rows["running"]["status"] == "importing" and rows["running"]["job"] == running
    gate.set()
    _wait(jobs, running)


# --- taking an upload -------------------------------------------------------------

@pytest.fixture
def client(shelf):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    api = FastAPI()
    limits = {"max_bytes": 64 * 1024, "max_books": 3}

    @api.post("/upload")
    async def upload(request: Request):
        u = await B.receive(request, "robin", shelf, noun="book", **limits)
        return {"slug": u.slug, "name": u.name, "author": u.author, "allow_vision": u.allow_vision,
                "dest": str(u.dest)}

    with TestClient(api) as c:
        c.limits = limits
        yield c


PDF = b"%PDF-1.4\n" + b"0" * 100


def _send(client, data=PDF, filename="book.pdf", **fields):
    return client.post("/upload", files={"file": (filename, data, "application/pdf")}, data=fields)


def test_an_upload_is_written_with_what_was_said_about_it(client, shelf):
    r = _send(client, name="  The   Wilhelm  Translation ", author="Richard Wilhelm", allow_vision="on")
    assert r.status_code == 200, r.text
    assert r.json() == {"slug": "the-wilhelm-translation", "name": "The Wilhelm Translation",
                        "author": "Richard Wilhelm", "allow_vision": True,
                        "dest": str(shelf.root("robin") / "the-wilhelm-translation")}
    dest = shelf.root("robin") / "the-wilhelm-translation"
    assert (dest / "source.pdf").read_bytes() == PDF
    assert B.read_upload(dest)["name"] == "The Wilhelm Translation"
    assert _send(client, filename="Commentary.pdf").json()["slug"] == "commentary"  # named for its file


def test_what_an_upload_must_be(client):
    assert _send(client, name="Same").status_code == 200
    r = _send(client, name="Same")
    assert r.status_code == 409 and "already have a book called 'same'" in r.json()["detail"]
    assert _send(client, data=b"not a pdf", name="x").status_code == 400
    assert _send(client, name="!!!").status_code == 400
    assert _send(client, data=PDF + b"0" * client.limits["max_bytes"], name="big").status_code == 413
    assert client.post("/upload", data={"name": "no file"}).status_code == 400
    _send(client, name="Two")
    _send(client, name="Three")
    r = _send(client, name="Four")
    assert r.status_code == 409 and r.json()["detail"] == "You have 3 books already. Delete one first."


def test_an_upload_too_big_by_its_own_account_is_refused_unread(client):
    r = client.post("/upload", content=b"x", headers={"content-type": "multipart/form-data; boundary=b",
                                                       "content-length": str(10 * 1024 * 1024)})
    assert r.status_code == 413


def test_the_largest_upload_a_library_takes(app, tmp_path, monkeypatch):
    from fortunes_platform import config as cfgfile

    assert B.max_upload_bytes("library") == 100 * 1024 * 1024
    monkeypatch.setattr(cfgfile, "get", lambda section, key, default=None: "5" if section == "books" else None)
    assert B.max_upload_bytes("books") == 5 * 1024 * 1024 and B.max_upload_bytes("library") == 100 * 1024 * 1024
