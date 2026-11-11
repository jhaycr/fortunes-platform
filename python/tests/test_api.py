"""The shared HTTP side, served for a stand-in app: its headers and guard,
who is signed in, the hooks the app hands in, and the web pages' fallback."""

import json
import types

import pytest
from fastapi.testclient import TestClient

from fortunes_platform import api, db


@pytest.fixture
def client(app, tmp_path, monkeypatch):
    monkeypatch.setenv("TESTAPP_ADMIN_USERS", "local")
    monkeypatch.setenv("TESTAPP_VERSION", "1.2.3")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<p>the app</p>")
    people = types.SimpleNamespace(
        reassign_user_data=lambda src, dst: {"from": src, "to": dst, "readings": 0, "settings": 0,
                                             "usage_rows": 0, "deactivated": True},
        delete_user=lambda u: {"deleted": u},
    )
    a = api.create_app(
        my_settings=lambda user: {"auto_read_audio": False, "persona": None, "has_profile": False, "mine": user},
        people=people,
        shape_reading=lambda out, user: {**out, "shaped_for": user},
    )

    @a.get("/api/own")
    def own():
        return {"own": True}

    api.serve_frontend(a, site)
    with TestClient(a) as c:
        yield c


def test_every_answer_carries_the_security_headers(client):
    r = client.get("/api/health")
    assert r.json() == {"ok": True, "version": "1.2.3"}
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_a_cross_site_write_is_refused(client):
    r = client.patch("/api/me", json={"display_name": "X"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert client.patch("/api/me", json={"display_name": "Sam"}, headers={"sec-fetch-site": "same-origin"}).status_code == 200


def test_me_carries_the_app_settings(client):
    me = client.get("/api/me").json()
    assert me["user"] == "local" and me["is_admin"] is True and me["version"] == "1.2.3"
    assert me["settings"]["mine"] == "local"
    assert me["interpretation"] is False and me["tts"] is False


def test_the_readers_are_the_app_ones(client):
    r = client.get("/api/personas").json()
    assert {p["slug"] for p in r["personas"]} == {"sage", "imp"} and r["default"] == "sage"


def test_a_reading_is_shaped_by_the_app_and_shared_by_its_owner(client):
    with db.connect() as con:
        rid = con.execute(
            "INSERT INTO readings (owner, created_at, question, deck, spread, cards) VALUES ('local', 1, 'q', '', 'one', ?)",
            (json.dumps(["a"]),),
        ).lastrowid
    r = client.get(f"/api/readings/{rid}").json()
    assert r["shaped_for"] == "local" and r["yours"] is True and r["tiles"] == ["a"]
    r = client.put(f"/api/readings/{rid}/sharing", json={"visibility": "everyone", "grantees": []}).json()
    assert r["visibility"] == "everyone" and r["shaped_for"] == "local"
    assert client.get("/api/readings").json()[0]["id"] == rid
    assert client.delete(f"/api/readings/{rid}").status_code == 200
    assert client.get(f"/api/readings/{rid}").status_code == 404


def test_the_admin_tools_use_the_app_people_module(client):
    from fortunes_platform import users

    users.touch_sync("robin")
    users.touch_sync("kit")
    r = client.post("/api/admin/users/robin/reassign", json={"to": "kit"})
    assert r.status_code == 200 and r.json()["to"] == "kit"
    assert client.post("/api/admin/users/robin/reassign", json={"to": "local"}).status_code == 400, \
        "the system user takes nobody's data"


def test_the_app_routes_and_the_web_pages_sit_beside_the_shared_ones(client):
    assert client.get("/api/own").json() == {"own": True}
    assert client.get("/journal/7").text == "<p>the app</p>", "a page path gets the app"
    r = client.get("/api/nothing-here")
    assert r.status_code == 404 and "the app" not in r.text, "an unknown API path is a 404, never the page"
