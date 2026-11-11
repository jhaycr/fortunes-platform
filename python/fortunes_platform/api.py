"""The HTTP side every Little Fortunes app shares: the security headers and
the cross-origin guard, sign-in resolution and first-admin setup, who is
signed in, people and the admin's tools, the AI, voice and limit settings,
usage, the journal's reading list with sharing and deletion, speech, and the
built web pages with their single-page fallback.

An app builds its FastAPI app with `create_app`, adds its own routes to it,
then calls `serve_frontend` last (the static files catch every other path):

    app = api.create_app(my_settings=get_my_settings, people=reassign)
    @app.get("/api/hexagrams") ...
    api.serve_frontend(app, Path(__file__).parents[3] / "frontend" / "build")

The app's own parts are handed to create_app: the person's settings (part of
/api/me), the last shaping of a reading for the API (`shape_reading`), and
the module with the app's part of handing a person's data on or erasing it
(`people`, with `move_files(src, dst)` and `erase_files(username)`; see
fortunes_platform.people).
"""

import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from fortunes_platform import ai, auth, config as cfgfile, crypto, db, host, limits, people as people_core
from fortunes_platform import sessions, sse, tts, users
from fortunes_platform import oidc as oidc_mod, voices as voices_mod
from fortunes_platform.auth import current_user, display_name, is_authenticated, logout_url
from fortunes_platform.users import is_admin

router = APIRouter()


@dataclass(frozen=True)
class _AppHooks:
    my_settings: Callable[[str], dict]
    people: ModuleType
    shape_reading: Callable[[dict, str], dict]


_current: _AppHooks | None = None


def _hooks() -> _AppHooks:
    if _current is None:
        raise RuntimeError("fortunes_platform.api.create_app() has not been called")
    return _current


def create_app(*, my_settings: Callable[[str], dict], people: ModuleType,
               shape_reading: Callable[[dict, str], dict] = lambda out, user: out) -> FastAPI:
    """The app's FastAPI app, with the shared routes and their middleware."""
    global _current
    _current = _AppHooks(my_settings=my_settings, people=people, shape_reading=shape_reading)
    app = FastAPI(title=host.current().name, docs_url="/api/docs", openapi_url="/api/openapi.json")
    # OIDC handshake state (state/nonce/next) only: a per-boot secret is fine
    # because the cookie lives minutes; a restart mid-login just means retrying.
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(
        SessionMiddleware, secret_key=secrets.token_hex(32),
        session_cookie=f"{host.current().id}_oauth", max_age=600, same_site="lax",
    )
    app.middleware("http")(security_headers)
    app.middleware("http")(csrf_guard)
    app.on_event("startup")(_admin_bootstrap)
    app.on_event("startup")(_reap_sessions)
    app.exception_handler(limits.LimitExceeded)(_limit_exceeded)
    app.include_router(oidc_mod.router)
    app.include_router(router)
    return app


def serve_frontend(app: FastAPI, default: Path) -> None:
    """Serve the built web pages (`<PREFIX>STATIC_DIR`, else `default`), if
    they are there: present in the container, absent in API-only development.
    Called last, after the app's own routes."""
    static = Path(host.env("STATIC_DIR") or default)
    if static.is_dir():
        app.mount("/", SpaStaticFiles(directory=static, html=True), name="frontend")


# Headers a <meta> CSP cannot carry. The page policy itself (script-src
# with its boot-script hash, connect-src, ...) ships in index.html from the
# frontend build; this header policy only ADDS the header-only directives,
# and policies intersect, so it never loosens the page's. It also covers the
# API docs page, which loads its UI from a CDN and so gets no script rule here.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


async def csrf_guard(request: Request, call_next):
    """Cross-origin write protection for cookie-auth'd requests (design
    §4.7). SameSite=Lax alone is insufficient — sibling subdomains are
    "same-site". Browsers always send Sec-Fetch-Site; when it's absent we
    fall back to Origin; requests carrying neither (curl, scripts, the
    TestClient) aren't browsers and can't be CSRF'd."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        site = request.headers.get("sec-fetch-site")
        if site is not None:
            if site not in ("same-origin", "none"):
                return JSONResponse(status_code=403,
                                    content={"detail": "cross-origin request refused"})
        else:
            origin = request.headers.get("origin")
            if origin:
                expected = f"{request.url.scheme}://{request.url.netloc}"
                if origin.rstrip("/") != expected:
                    return JSONResponse(status_code=403,
                                        content={"detail": "cross-origin request refused"})
    return await call_next(request)


def _admin_bootstrap() -> None:
    oidc_mod.ensure_setup_token()


def version() -> str:
    """The build's version stamp (`<PREFIX>VERSION`), "dev" when unset."""
    return host.env("VERSION", "dev")


def _reap_sessions() -> None:
    def loop() -> None:
        while True:
            try:
                sessions.reap()
            except Exception:
                pass  # bookkeeping; never a reason to crash
            time.sleep(86400)

    threading.Thread(target=loop, name="session-reaper", daemon=True).start()


def resolve_user(request: Request) -> str:
    """Identity for the request, registering the caller on first sight.

    Resolution order (design §3): valid session cookie → trusted proxy
    header (mode-dependent; in legacy mode the header path includes the
    anonymous `local` fallback) → 401 JSON with a login_url, never a
    redirect. Every route resolves identity through this one dependency;
    auth.py stays free of database imports, so the session lookup and the
    registry upsert both live here.
    """
    token = request.cookies.get(auth.cookie_name())
    if token:
        user = sessions.resolve(token, auth.session_days())
        if user:
            users.touch(user)
            return user
    if auth.header_trusted(request):
        user = current_user(request)
        if auth.mode() == auth.LEGACY or user != auth.fallback_user():
            users.touch(user, display_name(request))
            return user
    raise HTTPException(401, "sign in required")


User = Annotated[str, Depends(resolve_user)]


class SetupRequest(BaseModel):
    token: str


@router.get("/api/setup")
def setup_state(user: User):
    """Whether first-admin bootstrap is still open, for the /auth/setup page.

    Calling this prints the token when boot did not (a legacy install
    expecting `local` to become admin) or when an instance is left
    adminless long after boot (deactivation, deletion). ensure_setup_token
    only generates when there is no admin and no live token, so polling the
    page can't spam the log."""
    # users.touch is queued/best-effort, so on a first-ever sighting the
    # caller's row (and the is_admin seeded with it) may not have landed
    # yet — flush first or `required` and `is_admin` can contradict each
    # other inside one response.
    users.flush_touches()
    oidc_mod.ensure_setup_token(on_request=True)
    return {"required": not users.active_admin_exists(),
            "user": user, "is_admin": is_admin(user)}


@router.post("/api/setup")
def setup(req: SetupRequest, user: User):
    """Promote the currently signed-in user to admin with the one-time
    setup token printed to the container log (no-admin boots only)."""
    if not oidc_mod.consume_setup_token(req.token.strip(), user):
        raise HTTPException(403, "invalid or already-used setup token")
    return {"user": user, "is_admin": True}


async def _limit_exceeded(request: Request, exc: limits.LimitExceeded):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=429, content={"detail": str(exc)})


@router.get("/api/health")
def health():
    return {"ok": True, "version": version()}


@router.get("/api/me")
def me(request: Request, user: User):
    authenticated = is_authenticated(request)
    return {
        "user": user,
        "display_name": _my_display_name(request, user),
        "interpretation": ai.config() is not None,
        "tts": tts.config() is not None,
        "settings": _hooks().my_settings(user),
        "limits": limits.status(user) if limits.enabled() else {"enabled": False},
        "is_admin": is_admin(user),
        "authenticated": authenticated,
        # oidc sessions log out through the app (POST /auth/logout); the
        # legacy/header path keeps the proxy's sign-out URL.
        "logout_url": ("/auth/logout" if request.cookies.get(auth.cookie_name())
                       else logout_url()) if authenticated else None,
        "version": version(),
    }


@router.get("/api/users")
def users_list(user: User):
    """People who can be picked as share recipients."""
    names = users.display_names()
    return [
        {"username": u["username"], "display_name": names.get(u["username"], u["username"])}
        for u in users.list_people()
    ]


@router.get("/api/personas")
def list_personas(user: User):
    return {
        "personas": [
            {"slug": slug, "name": p["name"], "description": p["description"]}
            for slug, p in ai.personas().items()
        ],
        "default": ai.default_persona(),
    }


class LlmSettingsRequest(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # write-only; empty string clears the stored key


def require_admin(user: str) -> None:
    if not is_admin(user):
        raise HTTPException(403, "admin only")


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    active: bool | None = None
    is_admin: bool | None = None


@router.get("/api/admin/users")
def admin_users_list(user: User):
    """Everyone, named as they name themselves (the People list shows the
    username beside it)."""
    require_admin(user)
    names = users.display_names()
    return [{**u, "display_name": names.get(u["username"], u["display_name"])}
            for u in users.list_all()]


class ReassignRequest(BaseModel):
    to: str


@router.post("/api/admin/users/{username}/reassign")
def admin_reassign(username: str, req: ReassignRequest, user: User):
    """Hand every owner surface of `username` to another person and
    deactivate the source — the `local` retirement tool (auth Step 7),
    generalized to any user."""
    require_admin(user)
    dst = req.to.strip()
    src_row = users.get(username)
    dst_row = users.get(dst)
    if not src_row:
        raise HTTPException(404, f"user '{username}' not found")
    if not dst_row or not dst_row["active"]:
        raise HTTPException(400, f"'{dst}' is not an active user")
    if username == dst:
        raise HTTPException(400, "source and destination are the same user")
    if dst_row["kind"] != users.KIND_PERSON:
        raise HTTPException(400, "destination must be a person")
    if src_row["is_admin"] and src_row["active"] and not users.other_active_admin_exists(username):
        raise HTTPException(409, "cannot deactivate the last active admin")
    # sync route: FastAPI already runs this in the threadpool
    return people_core.reassign(username, dst, _hooks().people.move_files)


@router.delete("/api/admin/users/{username}")
def admin_user_delete(username: str, user: User):
    """Erase a user and their data (library publications tombstone to
    "former member"; the usage ledger stays). Admins must be demoted
    first — the two-step makes deleting a privileged account deliberate;
    ordinary users delete directly (the UI confirms)."""
    require_admin(user)
    target = users.get(username)
    if not target:
        raise HTTPException(404, f"user '{username}' not found")
    if username == user:
        raise HTTPException(400, "you cannot delete yourself")
    if target["is_admin"]:
        raise HTTPException(409, "remove admin from this user first — admins cannot be deleted")
    return people_core.erase(username, _hooks().people.erase_files)


@router.patch("/api/admin/users/{username}")
def admin_user_update(username: str, req: UpdateUserRequest, user: User):
    require_admin(user)
    target = users.get(username)
    if not target:
        raise HTTPException(404, f"user '{username}' not found")
    # Never orphan the instance: the last active admin can be neither
    # deactivated nor demoted.
    if (req.active is False or req.is_admin is False) and target["is_admin"] and target["active"]:
        if not users.other_active_admin_exists(username):
            raise HTTPException(409, "cannot remove the last active admin")
    updated = users.update(username, display_name=req.display_name, active=req.active)
    if req.is_admin is not None:
        updated = users.set_admin(username, req.is_admin) or updated
    if req.active is False:
        sessions.destroy_all(username)  # a deactivated account signs out everywhere
    return updated


class DeleteMeRequest(BaseModel):
    confirm: str


@router.post("/api/me/delete")
def delete_my_data(req: DeleteMeRequest, user: User):
    """Self-service erasure — same semantics as the admin delete (readings
    cascade, ledger stays, row dropped). A later sign-in starts a fresh account
    via JIT."""
    if req.confirm.strip() != user:
        raise HTTPException(400, "type your username exactly to confirm")
    if is_admin(user) and not users.other_active_admin_exists(user):
        raise HTTPException(409, "you are the last admin — promote someone else first")
    return people_core.erase(user, _hooks().people.erase_files)


def _managed(section: str, *keys: str) -> list[str]:
    """Settings the config file owns, so the UI can render them read-only."""
    return [k for k in keys if cfgfile.has(section, k)]


@router.get("/api/settings/llm")
def get_llm_settings(user: User):
    require_admin(user)
    managed = _managed("llm", "base_url", "model", "max_tokens")
    if cfgfile.llm_api_key() is not None:
        managed.append("api_key")
    return {
        "base_url": cfgfile.get("llm", "base_url")
        or db.get_setting("llm_base_url")
        or host.env("LLM_BASE_URL"),
        "model": cfgfile.get("llm", "model")
        or db.get_setting("llm_model")
        or host.env("LLM_MODEL"),
        "api_key_set": bool(
            cfgfile.llm_api_key()
            or db.get_setting("llm_api_key")
            or host.env("LLM_API_KEY")
        ),
        "from_env": not cfgfile.has("llm", "base_url")
        and not db.get_setting("llm_base_url")
        and bool(host.env("LLM_BASE_URL")),
        "managed": managed,
        "config_file": str(cfgfile.config_path()) if cfgfile.exists() else None,
        "config_error": cfgfile.error() or None,
    }


def _reject_managed(section: str, **fields) -> None:
    """The config file is authoritative; saving over it would look like it
    worked and then be ignored, so refuse instead."""
    clashes = [
        k
        for k, v in fields.items()
        if v is not None
        and (cfgfile.llm_api_key() is not None if k == "api_key" else cfgfile.has(section, k))
    ]
    if clashes:
        raise HTTPException(
            409,
            f"{', '.join(sorted(clashes))} managed by {cfgfile.config_path()} — edit the config file",
        )


@router.put("/api/settings/llm")
def set_llm_settings(req: LlmSettingsRequest, user: User):
    require_admin(user)
    _reject_managed("llm", base_url=req.base_url, model=req.model, api_key=req.api_key)
    if req.base_url is not None:
        # A stored key must never follow a new endpoint it was not entered
        # for: whoever can change base_url (an admin, or script running in an
        # admin's session) would otherwise receive the key on the next call.
        if req.base_url.strip() != db.get_setting("llm_base_url") and req.api_key is None:
            db.set_setting("llm_api_key", "")
        db.set_setting("llm_base_url", req.base_url.strip())
    if req.model is not None:
        db.set_setting("llm_model", req.model.strip())
    if req.api_key is not None:
        db.set_setting("llm_api_key", crypto.encrypt(req.api_key.strip()) if req.api_key.strip() else "")
    return get_llm_settings(user)


class LimitsSettingsRequest(BaseModel):
    # None = leave alone; 0 = disable (stored as unset)
    readings_per_day: float | None = Field(default=None, ge=0)
    llm_tokens_per_day: float | None = Field(default=None, ge=0)
    tts_minutes_per_day: float | None = Field(default=None, ge=0)


@router.get("/api/settings/limits")
def get_limits_settings(user: User):
    require_admin(user)
    return {
        **limits.config(),
        "managed": _managed("limits", *limits.KEYS),
        "config_file": str(cfgfile.config_path()) if cfgfile.exists() else None,
        "config_error": cfgfile.error() or None,
    }


@router.put("/api/settings/limits")
def set_limits_settings(req: LimitsSettingsRequest, user: User):
    require_admin(user)
    _reject_managed("limits", **req.model_dump())
    for key in limits.KEYS:
        value = getattr(req, key)
        if value is not None:
            db.set_setting(f"limit_{key}", "" if value == 0 else str(value))
    return get_limits_settings(user)


class MeRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)


@router.patch("/api/me")
def patch_me(req: MeRequest, request: Request, user: User):
    """Self-service profile edits. A blank display_name clears the override,
    falling back to the identity-derived name (users.touch keeps rewriting
    the registry row from the header, so the override lives in
    user_settings where nothing clobbers it)."""
    if req.display_name is not None:
        db.set_user_setting(user, "display_name", req.display_name.strip())
    return {"display_name": _my_display_name(request, user)}


def _my_display_name(request: Request, user: str) -> str:
    """Self-chosen override → header-derived name → the registry row (the
    only source for cookie-session requests, which carry no header)."""
    override = db.get_user_setting(user, "display_name")
    if override:
        return override
    if auth._raw_identity(request):
        return display_name(request)
    row = users.get(user)
    return row["display_name"] if row else display_name(request)


@router.get("/api/admin/usage")
def admin_usage(user: User, days: int = 30):
    """Token/character/audio spend of the connected AI components, for the
    admin usage view. Ledger rows are written per provider call; cache hits
    and aborted streams cost nothing and record nothing."""
    require_admin(user)
    return db.usage_summary(max(1, min(days, 365)))


def reading_view(r: dict, user: str, names: dict[str, str] | None = None) -> dict:
    """Only the owner sees who a reading is shared with. `owner_name` is
    the owner as other people know them (`owner` stays the username). The
    app then shapes its own parts (`shape_reading`)."""
    if names is None:
        names = users.display_names()
    yours = r["owner"] == user
    out = {**r, "owner_name": names.get(r["owner"], r["owner"]), "yours": yours,
           "shared_with": r["shared_with"] if yours else []}
    return _hooks().shape_reading(out, user)


class SharingRequest(BaseModel):
    visibility: str
    grantees: list[str] = Field(default_factory=list)


@router.get("/api/readings")
def readings_list(user: User, limit: int | None = Query(None, ge=1, le=100)):
    names = users.display_names()
    return [reading_view(r, user, names) for r in db.list_readings(user, limit=limit)]


@router.get("/api/readings/{reading_id}")
def readings_get(reading_id: int, user: User):
    r = db.get_reading(reading_id, user)
    if not r:
        raise HTTPException(404, "reading not found")
    return reading_view(r, user)


@router.put("/api/readings/{reading_id}/sharing")
def readings_set_sharing(reading_id: int, req: SharingRequest, user: User):
    if req.visibility not in db.VISIBILITIES:
        raise HTTPException(400, f"visibility must be one of {', '.join(db.VISIBILITIES)}")

    grantees: list[str] = []
    if req.visibility == db.SPECIFIC:
        for g in dict.fromkeys(req.grantees):  # de-dupe, keep order
            if g == user:
                continue  # sharing with yourself is a no-op, not an error
            if not users.is_grantable(g):
                raise HTTPException(400, f"cannot share with '{g}'")
            grantees.append(g)

    r = db.set_sharing(reading_id, user, req.visibility, grantees)
    if not r:
        raise HTTPException(404, "reading not found or not yours")
    return reading_view(r, user)


@router.delete("/api/readings/{reading_id}")
def readings_delete(reading_id: int, user: User):
    if not db.delete_reading(reading_id, user):
        raise HTTPException(404, "reading not found or not yours")
    return {"deleted": reading_id}


def _stream_and_persist(request: Request, user: str, persona: str | None,
                        system_prompt: str, user_content: str, persist,
                        max_tokens: int | None = None,
                        usage_meta: dict | None = None):
    """Shared SSE generator: stream deltas, then persist the full text at natural
    completion only (skipped on disconnect / mid-stream error), then `done`."""
    async def gen():
        parts: list[str] = []
        agen = ai.interpret_stream(system_prompt, user_content, max_tokens=max_tokens,
                                       usage_meta=usage_meta)
        try:
            async for delta in agen:
                if await request.is_disconnected():
                    # Client left — close the upstream stream now (don't wait for
                    # GC) so we stop paying the LLM; persist nothing.
                    await agen.aclose()
                    return
                parts.append(delta)
                yield sse.sse("token", {"text": delta})
        except httpx.HTTPError as e:
            yield sse.sse("error", {"message": f"LLM endpoint error: {e}"})
            return
        except Exception:
            yield sse.sse("error", {"message": "interpretation failed"})
            return
        full = "".join(parts).strip()
        if not full:
            # An empty completion is a failure, not a result: persisting
            # nothing while signalling `done` would strand the reading
            # in_progress with the client believing it finished.
            yield sse.sse("error", {"message": "the model returned an empty response — try again"})
            return
        await run_in_threadpool(persist, full)
        yield sse.sse("done", {"persona": persona or ai.default_persona()})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=sse.SSE_HEADERS)


AUDIO_CACHE = {"Cache-Control": "private, no-cache"}


def _audio_response(request: Request, path, key: str):
    etag = f'"{key}"'
    headers = {**AUDIO_CACHE, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="audio/mpeg", headers=headers)


def _tts_config_or_409() -> dict:
    cfg = tts.config()
    if cfg is None:
        # Only one thing can produce this: no base_url. Say so, and say which
        # provider needs it — "not configured" alone once sent the owner looking at their
        # API key when the provider was the problem.
        provider = tts.provider_name()
        adapter = voices_mod.get_adapter(provider)
        if not adapter.default_base_url or provider == voices_mod.DEFAULT_PROVIDER:
            raise HTTPException(
                409,
                f"TTS is not configured — the {adapter.label} provider needs a Base URL "
                f"(e.g. https://api.openai.com/v1). Set it in Settings, or switch provider.",
            )
        raise HTTPException(409, f"TTS is not configured for {adapter.label}")
    return cfg


def _voice_or_409(persona: str | None, owner: str) -> dict:
    """The persona's voice under the active provider.

    A provider switch can leave a persona unvoiced (an ElevenLabs voice_id
    is account-specific, so nothing can be shipped as a default). Say which
    persona and which field rather than substituting some other voice —
    hearing the Elder in a stranger's voice would be worse than an error."""
    voice = tts.resolve_voice(persona, owner)
    if voice is None:
        missing = ", ".join(tts.voice_gap(persona)) or "voice settings"
        raise HTTPException(
            409,
            f"No voice configured for '{persona or 'default'}' on the "
            f"{tts.provider_name()} provider — set {missing} in Settings.",
        )
    return voice


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    persona: str | None = None  # one of the app's readers; None = the default


@router.post("/api/tts")
async def tts_speak(req: TtsRequest, user: User):
    """Audio for ephemeral text — the quick reading's one-shot interpretation,
    which is never persisted. Cached like everything else, so replaying the
    same text is free."""
    cfg = _tts_config_or_409()
    persona = req.persona or ai.default_persona()
    voice = await run_in_threadpool(_voice_or_409, persona, user)
    script = tts.build_script("Your reading.", req.text)
    key = tts.cache_key(script, voice, cfg)
    if not (tts.cache_dir() / f"{key}.mp3").is_file():
        await run_in_threadpool(limits.check_minutes, user)
    try:
        path = await tts.get_or_generate(script, voice, cfg,
                                         usage_meta={"owner": user, "kind": "speak"})
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, _provider_error("Reading aloud", e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Reading aloud failed: could not reach the voice service ({e}).")
    return FileResponse(path, media_type="audio/mpeg",
                        headers={**AUDIO_CACHE, "ETag": f'"{key}"'})


class TtsSettingsRequest(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # write-only; empty string clears the stored key
    # Free-form per-persona blocks: the shape belongs to the active
    # provider's adapter (voice/speed/instructions for openai, voice_id and
    # dials for elevenlabs), so it's validated against adapter.fields rather
    # than a fixed model here.
    voices: dict[str, dict] | None = None


def _file_owns_voices() -> list[str]:
    """Personas whose block for the ACTIVE provider comes from config.yaml."""
    file_voices = cfgfile.get("tts", "voices")
    if not isinstance(file_voices, dict):
        return []
    provider = tts.provider_name()
    return [p for p in tts.VOICE_DEFAULTS
            if tts._for_provider(file_voices.get(p), provider) is not None]


def _provider_descriptor(adapter) -> dict:
    """Everything the settings UI needs to render a provider's voice form,
    so adding an adapter needs no frontend work."""
    return {
        "name": adapter.name,
        "label": adapter.label,
        "default_base_url": adapter.default_base_url,
        "default_model": adapter.default_model,
        "supports_listing": adapter.supports_listing,
        "supports_design": adapter.supports_design,
        "fields": [
            {"key": f.key, "label": f.label, "kind": f.kind, "default": f.default,
             "required": f.required, "min": f.min, "max": f.max, "step": f.step,
             "help": f.help}
            for f in adapter.fields
        ],
    }


@router.get("/api/settings/tts")
def get_tts_settings(user: User):
    require_admin(user)
    managed = _managed("tts", "base_url", "model", "provider")
    if cfgfile.tts_api_key() is not None:
        managed.append("api_key")
    managed += [f"voice_{p}" for p in _file_owns_voices()]
    provider = tts.provider_name()
    adapter = voices_mod.get_adapter(provider)
    # Per-provider connection settings, so switching provider in the UI
    # restores that provider's own values instead of carrying the previous
    # one's across (which would then be saved under the wrong provider).
    connections = {
        a.name: {
            "base_url": db.get_setting(tts.setting_key("base_url", a.name)) or "",
            "model": db.get_setting(tts.setting_key("model", a.name)) or "",
            "api_key_set": bool(db.get_setting(tts.setting_key("api_key", a.name))
                                or tts.env_setting("api_key", a.name)),
            # Every provider's voice blocks, so switching in the UI restores
            # them instead of showing empty fields over intact stored data.
            "voices": {p: tts.resolve_voice(p, user, a.name) for p in tts.VOICE_DEFAULTS},
            "gaps": {p: tts.voice_gap(p, a.name) for p in tts.VOICE_DEFAULTS},
            "defaults": {p: a.normalize(tts.VOICE_DEFAULTS[p]
                                        if a.name == voices_mod.DEFAULT_PROVIDER else {})
                         for p in tts.VOICE_DEFAULTS},
        }
        for a in voices_mod.all_adapters()
    }
    return {
        "provider": provider,
        "providers": [_provider_descriptor(a) for a in voices_mod.all_adapters()],
        "connections": connections,
        # base_url/model are stored per provider — see tts.setting_key.
        "base_url": cfgfile.get("tts", "base_url")
        or db.get_setting(tts.setting_key("base_url", provider))
        or tts.env_setting("base_url", provider),
        "model": cfgfile.get("tts", "model")
        or db.get_setting(tts.setting_key("model", provider))
        or tts.env_setting("model", provider)
        or adapter.default_model
        or tts.DEFAULT_MODEL,
        "api_key_set": bool(
            cfgfile.tts_api_key()
            or db.get_setting("tts_api_key")
            or tts.env_setting("api_key", provider)
        ),
        # Blocks for the ACTIVE provider; null where a persona isn't voiced
        # on it yet (with `gaps` naming what's missing).
        "voices": {p: tts.resolve_voice(p, user) for p in tts.VOICE_DEFAULTS},
        "gaps": {p: tts.voice_gap(p) for p in tts.VOICE_DEFAULTS},
        # Each persona's written character, from wherever it's configured for
        # the OpenAI provider. It seeds voice design on providers that build a
        # voice from a description — the same prose, applied at design time.
        "descriptions": {p: tts.persona_description(p) for p in tts.VOICE_DEFAULTS},
        "defaults": {p: adapter.normalize(tts.VOICE_DEFAULTS[p]
                                          if provider == voices_mod.DEFAULT_PROVIDER else {})
                     for p in tts.VOICE_DEFAULTS},
        "managed": managed,
        "config_file": str(cfgfile.config_path()) if cfgfile.exists() else None,
        "config_error": cfgfile.error() or None,
    }


@router.get("/api/settings/tts/voices")
async def list_tts_voices(user: User):
    """The active provider's selectable voices, for the settings picker.
    501 when the provider can't enumerate them — the UI falls back to a
    free-text field."""
    require_admin(user)
    cfg = _tts_config_or_409()
    adapter = voices_mod.get_adapter(cfg.get("provider"))
    if not adapter.supports_listing:
        raise HTTPException(501, f"{adapter.label} cannot list voices")
    try:
        found = await adapter.list_voices(cfg)
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, _provider_error("Listing voices", e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Listing voices failed: {e}")
    return [
        {"id": v.id, "name": v.name, "category": v.category,
         "description": v.description, "preview_url": v.preview_url,
         "labels": v.labels, "settings": v.settings}
        for v in found
    ]


class DesignVoiceRequest(BaseModel):
    description: str = Field(min_length=20, max_length=2000)
    preview_text: str | None = Field(default=None, max_length=1000)


class KeepVoiceRequest(BaseModel):
    generated_voice_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=20, max_length=2000)
    rejected: list[str] | None = None


def _provider_error(what: str, e: httpx.HTTPStatusError) -> str:
    """A readable message from a provider error body.

    Providers nest the useful part: ElevenLabs returns
    {"detail": {"type": ..., "message": ...}}, so naively formatting `detail`
    prints a raw dict at the user. Some failures are also about the account
    rather than the request, and deserve the way around them.
    """
    body: object = None
    try:
        body = e.response.json()
    except Exception:
        return f"{what} failed: {e.response.text[:200] or e}"
    detail = body.get("detail", body) if isinstance(body, dict) else body
    # OpenAI and OpenRouter nest theirs as {"error": {"message": ..., "code": ...}}
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        detail = detail["error"]
    code = message = ""
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("status") or "")
        message = str(detail.get("message") or "")
    else:
        message = str(detail)
    if code in ("402", "insufficient_credits") or e.response.status_code == 402:
        return f"{what} failed: the provider account is out of credit. {message}"
    if code in ("feature_not_available", "feature_unavailable"):
        return (
            f"{what} needs a paid provider plan. {message} "
            "You can still pick any voice from your library: add voices on the "
            "provider's site (its Voice Library is free to browse), then choose "
            "them from the dropdown here."
        )
    if code in ("quota_exceeded", "too_many_requests"):
        return f"{what} failed: the voice service's credit is used up. {message}"
    # Scoped API keys fail with a plain 401, which reads as "wrong key" and
    # sends you to re-paste a key that was fine. The provider names the
    # missing scope — surface it and say where it's changed.
    if str(detail.get("status") if isinstance(detail, dict) else "") == "missing_permissions":
        return (
            f"{what} failed: your API key lacks a required permission. {message} "
            "API keys are scoped: edit the key on the provider's site and enable "
            "that permission (voices_read to list voices, text_to_speech to "
            "generate audio)."
        )
    return f"{what} failed: {message or detail}"


def _design_adapter(cfg: dict):
    adapter = voices_mod.get_adapter(cfg.get("provider"))
    if not adapter.supports_design:
        raise HTTPException(501, f"{adapter.label} cannot design voices from a description")
    return adapter


@router.post("/api/settings/tts/design")
async def design_voice(req: DesignVoiceRequest, user: User):
    """Candidate voices built from a written description — normally a
    persona's own `instructions` prose, so its character carries over to a
    provider that has no per-request steering field."""
    require_admin(user)
    cfg = _tts_config_or_409()
    adapter = _design_adapter(cfg)
    try:
        found = await adapter.design_voices(cfg, req.description.strip(),
                                            (req.preview_text or "").strip() or None)
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, _provider_error("Voice design", e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Voice design failed: {e}")
    # Previews are real generated audio, so they cost credits — record them.
    await run_in_threadpool(
        db.record_usage, owner=user, component="tts", kind="voice_design",
        model=tts.usage_model(cfg), characters=len(req.description),
        audio_bytes=sum(len(v.audio_base64) * 3 // 4 for v in found),
    )
    return [
        {"generated_voice_id": v.generated_voice_id,
         "audio": f"data:{v.media_type};base64,{v.audio_base64}",
         "duration_secs": v.duration_secs}
        for v in found
    ]


@router.post("/api/settings/tts/design/keep")
async def keep_designed_voice(req: KeepVoiceRequest, user: User):
    """Save a designed candidate to the account, returning its permanent id."""
    require_admin(user)
    cfg = _tts_config_or_409()
    adapter = _design_adapter(cfg)
    try:
        voice_id = await adapter.keep_designed_voice(
            cfg, req.generated_voice_id.strip(), req.name.strip(),
            req.description.strip(), req.rejected or [])
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, _provider_error("Saving the voice", e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Saving the voice failed: {e}")
    if not voice_id:
        raise HTTPException(502, "provider returned no voice id")
    return {"voice_id": voice_id}


@router.put("/api/settings/tts")
def set_tts_settings(req: TtsSettingsRequest, user: User):
    require_admin(user)
    _reject_managed("tts", base_url=req.base_url, model=req.model,
                    provider=req.provider)
    if req.provider is not None and not voices_mod.known(req.provider):
        known = ", ".join(a.name for a in voices_mod.all_adapters())
        raise HTTPException(400, f"unknown provider '{req.provider}' — known: {known}")
    if req.api_key is not None and cfgfile.tts_api_key() is not None:
        raise HTTPException(
            409, f"api_key managed by {cfgfile.config_path()} — edit the config file"
        )
    if req.voices:
        file_owned = set(_file_owns_voices())
        clashes = sorted(set(req.voices) & file_owned)
        if clashes:
            raise HTTPException(
                409,
                f"voices ({', '.join(clashes)}) managed by {cfgfile.config_path()} — edit the config file",
            )
    if req.provider is not None:
        db.set_setting("tts_provider", req.provider.strip().lower())
    # Connection settings and voice blocks are all stored per provider, so
    # editing one provider's setup never disturbs the other's — switching
    # back restores exactly what was there.
    provider = req.provider.strip().lower() if req.provider is not None else tts.provider_name()
    if req.base_url is not None:
        # same rule as the LLM: a new endpoint without a re-entered key drops the key
        if req.base_url.strip() != db.get_setting(tts.setting_key("base_url", provider)) and req.api_key is None:
            db.set_setting(tts.setting_key("api_key", provider), "")
        db.set_setting(tts.setting_key("base_url", provider), req.base_url.strip())
    if req.model is not None:
        db.set_setting(tts.setting_key("model", provider), req.model.strip())
    if req.api_key is not None:
        db.set_setting(tts.setting_key("api_key", provider),
                       crypto.encrypt(req.api_key.strip()) if req.api_key.strip() else "")

    adapter = voices_mod.get_adapter(provider)
    valid = {f.key for f in adapter.fields}
    for persona, block in (req.voices or {}).items():
        if persona not in tts.VOICE_DEFAULTS:
            raise HTTPException(400, f"unknown persona '{persona}'")
        unknown = sorted(set(block) - valid)
        if unknown:
            raise HTTPException(
                400,
                f"{adapter.label} has no voice setting(s) {', '.join(unknown)} "
                f"— expected: {', '.join(sorted(valid))}",
            )
        stored = db.get_setting(f"tts_voice_{persona}")
        try:
            existing = json.loads(stored) if stored else {}
        except ValueError:
            existing = {}
        # A legacy FLAT block means openai; lift it into the nested shape
        # before merging so nothing written pre-provider is lost.
        if existing and not any(isinstance(v, dict) for v in existing.values()):
            existing = {voices_mod.DEFAULT_PROVIDER: existing}
        values = {k: v for k, v in block.items() if v is not None}
        if values:
            existing[provider] = values
        else:
            existing.pop(provider, None)
        db.set_setting(f"tts_voice_{persona}", json.dumps(existing) if existing else "")
    return get_tts_settings(user)


class SpaStaticFiles(StaticFiles):
    """Static files with SPA fallback: unknown paths serve index.html, except
    under api/, where an unknown path is a 404 and never the app's HTML."""

    async def get_response(self, path, scope):
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            path, resp = "index.html", await super().get_response("index.html", scope)

        # A bare "/" arrives as "." (normpath), which html=True resolves to
        # index.html.
        name = path.rsplit("/", 1)[-1]
        if name in ("", "."):
            name = "index.html"
        if name in ("service-worker.js", "index.html"):
            # MUST NOT be cached by any intermediary. A stale service worker is
            # unrecoverable from the client: the browser's update check is
            # answered from the edge cache, so it never learns a new one exists
            # and keeps serving an app shell whose hashed assets are long gone.
            # (Cloudflare edge-cached this one for 3 days and blanked the app
            # for external users after v0.8.0 — 2026-07-24.)
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
        elif "/immutable/" in path:
            # Content-hashed by the build; safe to cache hard.
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
