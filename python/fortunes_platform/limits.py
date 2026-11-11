"""Per-user daily spend limits: readings, LLM tokens, TTS audio minutes.

Each cap is independent and disabled when unset/<=0. Values resolve
config file (`limits:` in /data/config.yaml) > admin UI (DB) > off.
Admins are exempt. The day boundary is server-local midnight — set the
container's TZ or "midnight" is UTC.

Enforcement is cheap: readings count comes from the reading_charges table
(idempotent fingerprint charges, so resumes/retries never double-bill);
token and minute sums come from the ai_usage ledger that TTS/LLM calls
already write. Checks run before a provider call, so one final call may
overshoot a cap — bounded by a single call's size, accepted at family scale.
"""

import time
from datetime import datetime

from fortunes_platform import config as cfgfile

# ~128 kbps MP3 (what OpenAI returns) — the same estimate the usage page shows
BYTES_PER_AUDIO_MINUTE = 983040

MESSAGES = {
    "readings": "Daily reading limit reached — resets at midnight.",
    "tokens": "Daily AI budget used up — resets at midnight.",
    "minutes": "Daily voice budget used up — resets at midnight.",
}

KEYS = ("readings_per_day", "llm_tokens_per_day", "tts_minutes_per_day")


class LimitExceeded(Exception):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(MESSAGES[kind])


def _value(key: str) -> float | None:
    from fortunes_platform import db

    raw = cfgfile.get("limits", key)
    if raw is None:
        raw = db.get_setting(f"limit_{key}") or None
    try:
        v = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return v if v and v > 0 else None


def config() -> dict:
    return {k: _value(k) for k in KEYS}


def enabled() -> bool:
    return any(v is not None for v in config().values())


def day_key(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")


def midnight_ts(now: float | None = None) -> int:
    dt = datetime.fromtimestamp(now or time.time())
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _exempt(user: str) -> bool:
    from fortunes_platform.users import is_admin

    return is_admin(user)


def status(user: str) -> dict:
    """Where the user stands, for /api/me and the admin view."""
    from fortunes_platform import db

    cfg = config()
    since = midnight_ts()
    return {
        "enabled": enabled(),
        "exempt": _exempt(user),
        "readings": {
            "used": db.readings_charged(user, day_key()),
            "limit": cfg["readings_per_day"],
        },
        "tokens": {
            "used": db.llm_tokens_since(user, since),
            "limit": cfg["llm_tokens_per_day"],
        },
        "minutes": {
            "used": round(db.tts_bytes_since(user, since) / BYTES_PER_AUDIO_MINUTE, 1),
            "limit": cfg["tts_minutes_per_day"],
        },
    }


def charge_reading(user: str, fingerprint: str, legacy: str | None = None) -> None:
    """Charge one reading against today's cap, idempotently by fingerprint.

    A fingerprint that was already charged today always passes (resume,
    re-interpret of the same draw). `legacy` is an alternate spelling of the
    same draw (the pre-normalization hash) honored on lookup only — never
    written. Raises LimitExceeded when the cap is reached and the
    fingerprint is new.
    """
    from fortunes_platform import db

    if _exempt(user):
        return
    if legacy and db.reading_charge_exists(user, legacy, day=day_key()):
        return
    limit = config()["readings_per_day"]
    if not db.try_charge_reading(user, day_key(), fingerprint, limit):
        raise LimitExceeded("readings")


def precheck_reading(user: str, fingerprint: str, legacy: str | None = None) -> None:
    """Raise if a NEW charge of this fingerprint would be blocked today.

    Charges nothing — for callers that must gate the cap up front but only
    charge after the provider call succeeds (a failed call must not consume
    a slot). A fingerprint already charged today always passes (retry).
    """
    from fortunes_platform import db

    if _exempt(user):
        return
    limit = config()["readings_per_day"]
    if limit is None:
        return
    day = day_key()
    if db.reading_charge_exists(user, fingerprint, day=day):
        return
    if legacy and db.reading_charge_exists(user, legacy, day=day):
        return
    if db.readings_charged(user, day) >= limit:
        raise LimitExceeded("readings")


def charge_reading_once(user: str, fingerprint: str, legacy: str | None = None) -> None:
    """Charge unless this fingerprint was ever charged, on any day.

    The finish-what-you-started rule for readings that outlive their day:
    a reading charged at creation streams free forever, even across
    midnight, while a never-charged reading pays when interpretation starts.
    """
    from fortunes_platform import db

    if _exempt(user):
        return
    if db.reading_charge_exists(user, fingerprint):
        return
    if legacy and db.reading_charge_exists(user, legacy):
        return
    charge_reading(user, fingerprint, legacy=legacy)


def check_tokens(user: str) -> None:
    from fortunes_platform import db

    if _exempt(user):
        return
    limit = config()["llm_tokens_per_day"]
    if limit is not None and db.llm_tokens_since(user, midnight_ts()) >= limit:
        raise LimitExceeded("tokens")


def check_minutes(user: str) -> None:
    from fortunes_platform import db

    if _exempt(user):
        return
    limit = config()["tts_minutes_per_day"]
    if limit is not None and (
        db.tts_bytes_since(user, midnight_ts()) / BYTES_PER_AUDIO_MINUTE
    ) >= limit:
        raise LimitExceeded("minutes")
