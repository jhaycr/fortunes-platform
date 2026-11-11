"""The AI call every Little Fortunes app makes: a reading's text from any
OpenAI-compatible chat endpoint, streamed, with each call written to the usage
ledger.

Configure in the app's config.yaml (see config.py, authoritative), in the
admin UI, or with environment variables (the app's prefix, e.g. ICHING_):
    <PREFIX>LLM_BASE_URL        e.g. http://ollama:11434/v1 or https://api.anthropic.com/v1
    <PREFIX>LLM_MODEL           e.g. llama3.1 or claude-haiku-4-5-20251001
    <PREFIX>LLM_API_KEY         optional bearer token

Settings resolve config file > admin UI (DB) > environment. Unset base URL
disables the feature (the UI hides the button).

What a reading says (its prompts, its readers) is the app's: it hands in its
prompts file (`host.Host.prompts`), whose `personas`, `default_persona` and
`max_tokens.default` are read here, and builds its messages itself.
"""

import json
import re

import httpx

from fortunes_platform import config as cfgfile, host


def prompts() -> dict:
    """The app's prompts file, as handed in."""
    return host.current().prompts()


def personas() -> dict[str, dict]:
    """{slug: {name, description, prompt}}: the app's built-in readers."""
    return {
        p["slug"]: {"name": p["name"], "description": p["description"], "prompt": p["prompt"]}
        for p in prompts()["personas"]
    }


def builtin_default_persona() -> str:
    return prompts()["default_persona"]


def default_max_tokens() -> int:
    return int(prompts()["max_tokens"]["default"])


def default_persona() -> str:
    """The reader a reading gets when none is chosen: the config file's
    `interpretation.default_persona` if it names one, else the app's."""
    builtin = builtin_default_persona()
    name = cfgfile.get("interpretation", "default_persona", builtin)
    return str(name) if str(name) in personas() else builtin


def config() -> dict | None:
    """LLM connection, resolved config file > admin UI (DB) > environment.

    The file wins so an Ansible-managed instance can't be quietly overridden
    from the UI. The DB-stored key is encrypted at rest; a file-supplied one is
    used as given.
    """
    from fortunes_platform import crypto
    from fortunes_platform import db

    file_url = cfgfile.get("llm", "base_url")
    base_url = str(
        file_url or db.get_setting("llm_base_url") or host.env("LLM_BASE_URL")
    ).rstrip("/")
    if not base_url:
        return None

    file_key = cfgfile.llm_api_key()
    if file_key is None:
        stored = db.get_setting("llm_api_key")
        api_key = crypto.decrypt(stored) if stored else host.env("LLM_API_KEY")
    else:
        api_key = file_key

    return {
        "base_url": base_url,
        "model": str(
            cfgfile.get("llm", "model")
            or db.get_setting("llm_model")
            or host.env("LLM_MODEL")
        ),
        "api_key": api_key,
        "max_tokens": int(cfgfile.get("llm", "max_tokens", default_max_tokens())),
    }


def _fill(template: str, **values: object) -> str:
    """Substitute {name} tokens. Values go in verbatim — a querent's braces
    are data, never a template."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]), template)


def _chat_body(system_prompt: str, user_content: str, cfg: dict, stream: bool,
               max_tokens: int | None = None, include_usage: bool = False) -> dict:
    return {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens or cfg["max_tokens"],
        **({"stream": True} if stream else {}),
        # ask the provider to append a usage chunk before [DONE]
        **({"stream_options": {"include_usage": True}} if stream and include_usage else {}),
    }


def _record_usage(usage_meta: dict | None, cfg: dict, usage: dict | None,
                  output_chars: int) -> None:
    """Best-effort usage ledger write; never let accounting break a reading."""
    if not usage_meta:
        return
    from fortunes_platform import db

    usage = usage or {}
    try:
        db.record_usage(
            owner=usage_meta["owner"],
            component="llm",
            kind=usage_meta.get("kind", "single"),
            model=cfg["model"],
            reading_id=usage_meta.get("reading_id"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            characters=output_chars,
        )
    except Exception:
        pass


def _auth_headers(cfg: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    return headers


async def interpret_stream(system_prompt: str, user_content: str, max_tokens: int | None = None,
                           usage_meta: dict | None = None):
    """Yield text deltas from the LLM as they arrive (OpenAI-compatible SSE).

    `max_tokens` overrides the configured cap for this call (the comprehensive
    reading of a whole spread or cast needs more room than one piece's).
    Raises RuntimeError if unconfigured. `raise_for_status()` runs before the
    first yield, so a handshake failure (bad key/model) propagates synchronously
    and the route can still turn it into a real HTTP status.

    With `usage_meta` ({owner, kind, reading_id?}), asks the provider for a
    usage chunk (stream_options.include_usage) and writes the ledger row in a
    finally — an aborted stream records the chars-streamed-so-far fallback
    (the provider bills the partial completion, so the token cap must see
    it). Providers that reject stream_options get one retry without.
    """
    cfg = config()
    if not cfg:
        raise RuntimeError("LLM interpretation is not configured")
    usage: dict | None = None
    output_chars = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            for include_usage in ([True, False] if usage_meta else [False]):
                async with client.stream(
                    "POST",
                    f"{cfg['base_url']}/chat/completions",
                    headers=_auth_headers(cfg),
                    json=_chat_body(system_prompt, user_content, cfg, stream=True,
                                    max_tokens=max_tokens, include_usage=include_usage),
                ) as resp:
                    if include_usage and resp.status_code in (400, 422):
                        continue  # provider rejected stream_options — retry without
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):  # blank or SSE keepalive comment
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        text = delta.get("content")
                        if text:
                            output_chars += len(text)
                            yield text
                    break  # completed (or ran without usage support) — no retry
    finally:
        # Runs on natural completion, disconnect (aclose -> GeneratorExit at the
        # yield) and mid-stream errors alike. Awaiting here is legal during
        # aclose(); only yielding is not. Skip when nothing streamed (handshake
        # failure) so failed calls are never billed.
        if usage_meta and (usage or output_chars):
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(_record_usage, usage_meta, cfg, usage, output_chars)
