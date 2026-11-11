"""Persona-voiced audio for readings, through a pluggable voice provider.

Mirrors interpret.py's split: config resolution (file > admin UI/DB > env)
separate from the API call. Generated audio is cached under
/data/tts-cache/<sha256>.mp3 with an LRU index in SQLite (see db.tts_cache_*),
bounded by `tts.cache_max_mb` (default 256) — evicted pieces regenerate on
demand, so the cache is never authoritative state.

The *shape* of the provider call lives in fortunes_platform/voices/<provider>.py; this
module owns everything provider-agnostic — config, voice resolution,
chunking, caching, eviction and the usage ledger. One provider is active
per instance:

    tts:
      provider: openai          # or elevenlabs; default openai
      base_url: https://api.openai.com/v1   # optional; adapter default
      model: gpt-4o-mini-tts
      api_key: "..."            # or api_key_env: <PREFIX>TTS_API_KEY
      cache_max_mb: 256
      voices:
        <persona>:
          openai:     {voice: sage, speed: 1.0, instructions: "..."}
          elevenlabs: {voice_id: "...", stability: 0.4}

A voice block written flat (no provider key) is read as the `openai`
block — the only provider that existed before — so existing config files
and stored settings keep working untouched.

Each app hands in its readers' shipped voices (`host.Host.voices`); the
spoken lead-in before a piece is the app's own, as its readings are.
"""

import asyncio
from collections.abc import Mapping
import hashlib
import json
import re

import httpx

from fortunes_platform import config as cfgfile, voices as voices_mod
from fortunes_platform import host
from fortunes_platform.host import data_dir

DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_CACHE_MB = 256
TIMEOUT = 60.0
# Fallback only. The real cap comes from the active adapter, since it is a
# property of the provider's model (3600 suits gpt-4o-mini-tts's ~2000-token
# input cap; ElevenLabs allows 10k-40k depending on model).
MAX_CHUNK_CHARS = 3600

# Shipped per-persona defaults, per provider. Only `openai` can have them:
# an ElevenLabs voice_id is account-specific, so a persona there is unset
# until an admin configures it (and has no audio until then, by design —
# substituting some premade voice would silently give a reader a stranger's).
class _VoiceDefaults(Mapping):
    """The app's shipped voice per reader (`host.Host.voices`), looked up on
    each use, read like the dict it is."""

    def _d(self) -> dict:
        return host.current().voices()

    def __getitem__(self, persona):
        return self._d()[persona]

    def __iter__(self):
        return iter(self._d())

    def __len__(self):
        return len(self._d())


VOICE_DEFAULTS = _VoiceDefaults()


def cache_dir():
    return data_dir() / "tts-cache"


def provider_name() -> str:
    """Active voice provider, resolved file > DB > env. Unknown names fall
    back to the default; the settings page reports the bad value rather
    than every config() caller 500ing (/api/me is one of them)."""
    name = str(
        cfgfile.get("tts", "provider")
        or db_setting("tts_provider")
        or host.env("TTS_PROVIDER")
    ).strip().lower()
    # `name` must be non-empty AND registered — an unset value is not
    # "known" merely because the lookup would fall back for us.
    return name if (name and voices_mod.known(name)) else voices_mod.DEFAULT_PROVIDER


def db_setting(key: str) -> str:
    """db.get_setting without importing db at module scope."""
    from fortunes_platform import db

    return db.get_setting(key) or ""


def env_setting(name: str, provider: str | None = None) -> str:
    """A connection setting from the environment, per provider.

    Same rule as setting_key: `<PREFIX>TTS_<NAME>_<PROVIDER>` for any provider,
    with the bare `<PREFIX>TTS_<NAME>` belonging to the DEFAULT provider only.
    Without that scoping, deploying both providers means OpenAI's base_url
    (or key) silently applies to ElevenLabs as well.
    """
    provider = provider or provider_name()
    scoped = host.env(f"TTS_{name.upper()}_{provider.upper()}")
    if scoped:
        return scoped
    if provider == voices_mod.DEFAULT_PROVIDER:
        return host.env(f"TTS_{name.upper()}")
    return ""


def setting_key(name: str, provider: str | None = None) -> str:
    """Storage key for a provider-specific connection setting.

    base_url and model belong to a provider, not to the instance: pointing
    ElevenLabs at `https://api.openai.com/v1` (or handing it
    `gpt-4o-mini-tts`) is nonsense, and leaving the previous provider's
    values behind on a switch is how you get
    `https://api.openai.com/v1/v1/text-to-speech/...`.

    The default provider keeps the historic bare key so existing rows and
    env vars keep working untouched.
    """
    provider = provider or provider_name()
    if provider == voices_mod.DEFAULT_PROVIDER:
        return f"tts_{name}"
    return f"tts_{name}_{provider}"


def config() -> dict | None:
    """TTS connection, resolved config file > admin UI (DB) > environment.

    None when there is no base_url and the provider supplies no default —
    the feature is off and all audio UI hides.
    """
    from fortunes_platform import crypto
    from fortunes_platform import db

    provider = provider_name()
    adapter = voices_mod.get_adapter(provider)

    file_url = cfgfile.get("tts", "base_url")
    base_url = str(
        file_url
        or db.get_setting(setting_key("base_url", provider))
        or env_setting("base_url", provider)
    ).rstrip("/")
    if not base_url:
        # A provider with a canonical endpoint (ElevenLabs) needs only a key,
        # so an explicit base_url is optional there. The OpenAI-compatible
        # adapter keeps requiring one: "compatible" means "could be anywhere",
        # and defaulting it to OpenAI would silently enable the feature and
        # start spending against a key meant for a local server.
        base_url = adapter.default_base_url if adapter.name != "openai" else ""
    if not base_url:
        return None

    file_key = cfgfile.tts_api_key()
    if file_key is None:
        # Keyed per provider: different backends mean different accounts, and
        # handing ElevenLabs an OpenAI key (or the reverse) on a switch would
        # fail in a confusing way.
        stored = db.get_setting(setting_key("api_key", provider))
        api_key = crypto.decrypt(stored) if stored else env_setting("api_key", provider)
    else:
        api_key = file_key

    # A malformed cache_max_mb must degrade to the default, not 500 every
    # config() caller (/api/me among them).
    try:
        cache_mb = float(cfgfile.get("tts", "cache_max_mb", DEFAULT_CACHE_MB))
        if cache_mb <= 0:
            cache_mb = DEFAULT_CACHE_MB
    except (TypeError, ValueError):
        cache_mb = DEFAULT_CACHE_MB

    return {
        "provider": provider,
        "base_url": base_url,
        "model": str(
            cfgfile.get("tts", "model")
            or db.get_setting(setting_key("model", provider))
            or env_setting("model", provider)
            or adapter.default_model
            or DEFAULT_MODEL
        ),
        "api_key": api_key,
        "cache_max_bytes": int(cache_mb * 1024 * 1024),
    }


def _normalize(block: dict | None, base: dict) -> dict:
    """A complete voice block, unknown keys dropped, missing keys from `base`."""
    block = block if isinstance(block, dict) else {}
    out = {}
    out["voice"] = str(block.get("voice") or base["voice"])
    try:
        out["speed"] = float(block.get("speed") or base["speed"])
    except (TypeError, ValueError):
        out["speed"] = base["speed"]
    instructions = block.get("instructions")
    out["instructions"] = str(instructions).strip() if instructions is not None else base["instructions"]
    return out


def _for_provider(block: dict | None, provider: str) -> dict | None:
    """Pull one provider's block out of a stored/configured voice entry.

    Entries are `{provider: {...}}`, but everything written before this
    feature is a FLAT block and means openai — the only provider that
    existed. Detected by shape rather than by a version marker, so no
    stored data has to be rewritten.
    """
    if not isinstance(block, dict):
        return None
    if any(isinstance(v, dict) for v in block.values()):
        nested = block.get(provider)
        return nested if isinstance(nested, dict) else None
    return block if provider == voices_mod.DEFAULT_PROVIDER else None


def resolve_voice(persona: str | None, owner: str,
                  provider: str | None = None) -> dict | None:
    """The voice block for a persona under a provider (the active one unless
    `provider` is given): file > DB > shipped defaults.

    Returns None when the provider needs a value we don't have (an
    ElevenLabs voice_id, say) — callers surface that as "this persona has
    no voice configured" rather than substituting someone else's.

    Unknown/absent persona (including the retired "custom") falls back to
    the instance default persona.
    """
    from fortunes_platform import db
    from fortunes_platform.ai import default_persona

    if persona not in VOICE_DEFAULTS:
        persona = default_persona()

    provider = provider or provider_name()
    adapter = voices_mod.get_adapter(provider)

    # Shipped defaults exist for openai only (see VOICE_DEFAULTS).
    block: dict = dict(VOICE_DEFAULTS[persona]) if provider == voices_mod.DEFAULT_PROVIDER else {}

    stored = db.get_setting(f"tts_voice_{persona}")
    if stored:
        try:
            found = _for_provider(json.loads(stored), provider)
        except ValueError:
            found = None
        if found:
            block = {**block, **found}

    file_voices = cfgfile.get("tts", "voices")
    if isinstance(file_voices, dict):
        found = _for_provider(file_voices.get(persona), provider)
        if found:
            block = {**block, **found}

    if adapter.missing_required(block):
        return None
    return adapter.normalize(block)


def persona_description(persona: str | None) -> str:
    """The persona's written character — its OpenAI `instructions` text,
    wherever configured (file > DB > shipped).

    Kept provider-independent on purpose: it is the app's description of how
    this reader sounds, and providers that design a voice from prose can use
    it directly rather than having it sit inert.
    """
    from fortunes_platform.ai import default_persona

    if persona not in VOICE_DEFAULTS:
        persona = default_persona()
    text = VOICE_DEFAULTS[persona].get("instructions", "")
    stored = db_setting(f"tts_voice_{persona}")
    if stored:
        try:
            block = _for_provider(json.loads(stored), voices_mod.DEFAULT_PROVIDER)
        except ValueError:
            block = None
        if block and block.get("instructions"):
            text = str(block["instructions"])
    file_voices = cfgfile.get("tts", "voices")
    if isinstance(file_voices, dict):
        block = _for_provider(file_voices.get(persona), voices_mod.DEFAULT_PROVIDER)
        if block and block.get("instructions"):
            text = str(block["instructions"])
    return text


def voice_gap(persona: str | None, provider: str | None = None) -> list[str]:
    """Required fields a provider is missing for this persona — what to tell
    an admin after a provider switch."""
    from fortunes_platform.ai import default_persona

    if persona not in VOICE_DEFAULTS:
        persona = default_persona()
    provider = provider or provider_name()
    adapter = voices_mod.get_adapter(provider)
    block: dict = dict(VOICE_DEFAULTS[persona]) if provider == voices_mod.DEFAULT_PROVIDER else {}
    stored = db_setting(f"tts_voice_{persona}")
    if stored:
        try:
            found = _for_provider(json.loads(stored), provider)
        except ValueError:
            found = None
        if found:
            block = {**block, **found}
    file_voices = cfgfile.get("tts", "voices")
    if isinstance(file_voices, dict):
        found = _for_provider(file_voices.get(persona), provider)
        if found:
            block = {**block, **found}
    return adapter.missing_required(block)


_MD_PATTERNS = [
    (re.compile(r"```.*?```", re.S), " "),        # fenced code
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),   # images
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),  # links -> text
    (re.compile(r"^#{1,6}\s*", re.M), ""),        # headings
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),      # list markers
    (re.compile(r"[*_]{1,3}([^*_]+)[*_]{1,3}"), r"\1"),  # emphasis
]


def strip_markdown(text: str) -> str:
    for pat, repl in _MD_PATTERNS:
        text = pat.sub(repl, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_script(intro: str, text: str) -> str:
    return f"{intro}\n\n{strip_markdown(text)}".strip()


def _chunks(script: str, max_chars: int | None = None) -> list[str]:
    """Split at paragraph, then sentence, then hard boundaries — never drops text.

    `max_chars` comes from the active adapter: chunking a 10k-capable
    provider at 3600 would insert prosody seams for nothing.
    """
    cap = max_chars or MAX_CHUNK_CHARS
    if len(script) <= cap:
        return [script]
    pieces: list[str] = []
    for para in re.split(r"\n\n+", script):
        if len(para) <= cap:
            pieces.append(para)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            # hard-split anything still over the cap (no sentence breaks)
            pieces.extend(
                sentence[i : i + cap] for i in range(0, len(sentence), cap)
            )
    parts: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > cap:
            parts.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current.strip():
        parts.append(current)
    return parts


def derive_seed(key: str, index: int) -> int:
    """A stable seed for one piece of one script.

    Providers that support seeded sampling then reproduce audio on
    re-render instead of drifting — which matters because the cache is
    not authoritative: evicted pieces regenerate, and generative TTS
    drift between renders is audible (seen after the v0.11.2 cache-key
    change).
    """
    digest = hashlib.sha256(f"{key}:{index}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


async def synthesize(script: str, voice: dict, cfg: dict,
                     usage_meta: dict | None = None) -> bytes:
    """Generate MP3 for the script, splitting over-cap text and concatenating
    the segments (same codec params -> a valid stream). With `usage_meta`
    ({owner, kind, reading_id?}), writes one ledger row per provider call —
    cache hits never reach here, so only real spend is recorded.

    The request shape belongs to the active adapter; chunk size, prosody
    continuity and seeding are all provider capabilities.
    """
    adapter = voices_mod.get_adapter(cfg.get("provider"))
    parts = _chunks(script, adapter.max_chunk_chars(cfg["model"]))
    key = cache_key(script, voice, cfg)
    out = b""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for i, chunk in enumerate(parts):
            req = adapter.build_request(
                script=chunk, voice=voice, cfg=cfg,
                # Neighbouring text keeps prosody continuous across seams.
                previous=(parts[i - 1] if i and adapter.supports_continuity else None),
                next=(parts[i + 1] if i + 1 < len(parts) and adapter.supports_continuity else None),
                seed=(derive_seed(key, i) if adapter.supports_seed else None),
            )
            resp = await client.post(req.url, json=req.json,
                                     headers=req.headers, params=req.params or None)
            resp.raise_for_status()
            out += resp.content
            if usage_meta:
                from starlette.concurrency import run_in_threadpool

                from fortunes_platform import db

                try:
                    await run_in_threadpool(
                        db.record_usage,
                        owner=usage_meta["owner"], component="tts",
                        kind=usage_meta.get("kind", "speak"),
                        # Provider-qualified so the usage page stays
                        # meaningful across a switch.
                        model=usage_model(cfg),
                        reading_id=usage_meta.get("reading_id"),
                        characters=len(chunk), audio_bytes=len(resp.content),
                    )
                except Exception:
                    pass  # accounting must never break playback
    return out


def usage_model(cfg: dict) -> str:
    """Ledger label. Unqualified for openai so historical rows keep matching."""
    provider = cfg.get("provider") or voices_mod.DEFAULT_PROVIDER
    return cfg["model"] if provider == voices_mod.DEFAULT_PROVIDER else f"{provider}:{cfg['model']}"


def cache_key(script: str, voice: dict, cfg: dict) -> str:
    """Content address of one audio piece. base_url is part of the identity:
    the same model name on a different provider is different audio.

    `provider` is omitted for openai ON PURPOSE, so hashes computed before
    pluggable providers stay bit-identical and the existing cache is not
    orphaned — v0.11.2 orphaned it once and every piece re-rendered on
    first play with audible drift. Any other provider has a different
    base_url anyway, so identity is still sound.
    """
    payload: dict = {
        "script": script, "voice": voice,
        "model": cfg["model"], "base_url": cfg["base_url"],
    }
    provider = cfg.get("provider") or voices_mod.DEFAULT_PROVIDER
    if provider != voices_mod.DEFAULT_PROVIDER:
        payload["provider"] = provider
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# One generation per piece even under concurrent requests; the dict itself is
# guarded so lock creation can't race.
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _lock_for(key: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault(key, asyncio.Lock())


ORPHAN_GRACE_SECS = 3600


def _evict(budget_bytes: int) -> None:
    """Drop least-recently-played entries until the cache fits the budget,
    and clean up any orphan files (crash leftovers) not in the index.

    Only files past ORPHAN_GRACE_SECS count as orphans: a concurrent
    get_or_generate writes the file before its index row, and the sweep
    must not eat that fresh write."""
    import time

    from fortunes_platform import db

    directory = cache_dir()
    known = db.tts_cache_all()
    indexed = {row["hash"] for row in known}
    cutoff = time.time() - ORPHAN_GRACE_SECS
    if directory.is_dir():
        for f in directory.glob("*.mp3"):
            if f.stem not in indexed:
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                except OSError:
                    pass
    total = sum(row["size_bytes"] for row in known)
    for row in known:  # ordered oldest-played first
        if total <= budget_bytes:
            break
        (directory / f"{row['hash']}.mp3").unlink(missing_ok=True)
        db.tts_cache_delete(row["hash"])
        total -= row["size_bytes"]


async def get_or_generate(script: str, voice: dict, cfg: dict,
                          usage_meta: dict | None = None):
    """Path to cached-or-fresh MP3 for this script+voice+model."""
    from fastapi.concurrency import run_in_threadpool

    from fortunes_platform import db

    key = cache_key(script, voice, cfg)
    path = cache_dir() / f"{key}.mp3"
    lock = await _lock_for(key)
    async with lock:
        if path.is_file():
            await run_in_threadpool(db.tts_cache_touch, key)
            return path
        db.tts_cache_delete(key)  # stale row without a file (evicted/crashed)
        audio = await synthesize(script, voice, cfg, usage_meta=usage_meta)

        def persist():
            cache_dir().mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(audio)
            tmp.replace(path)
            db.tts_cache_upsert(key, len(audio))
            _evict(cfg["cache_max_bytes"])

        await run_in_threadpool(persist)
        return path
