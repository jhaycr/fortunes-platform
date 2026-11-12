# fortunes-platform

The code the Little Fortunes apps share, in one place: a Python package for
their servers and Svelte components and Playwright helpers for their web
pages. A fix made here reaches every app that pins the new version, instead of
being copied from one app to the next by hand.

> **Personal project, no support.** Built largely with AI code-generation tools
> to scratch my own itch. Shared in case it's useful to you too. Issues and
> feature requests are welcome and I read them all, but responses and fixes
> happen on hobby-project time. Review and test before relying on it.

This is a library, not an app: there is nothing here to run on its own. The
apps that use it each have their own README and container image.

## What is in it

| Path | What it is |
|---|---|
| `python/fortunes_platform/` | The server half (package `fortunes-platform`): the journal database and its migrations, sign-in (proxy header and OIDC), users, sessions, encryption at rest, the YAML config file, spending limits, the AI call, speech with its voice providers and audio cache, the HTTP routes every app serves (`api.py`), handing a person's data on or erasing an account (`people.py`), and Server-Sent Events framing |
| `styles/suite.css` | The suite's look: colour tokens, page defaults, and the small classes every page repeats (`.dim`, `.error`, `.danger`, `.locktag`, disabled buttons) |
| `svelte/app.ts` | Where an app hands itself in to the web half: its name, its backend handle, a few words about its subject |
| `svelte/types.ts` | The wire types every app shares (who is signed in, people, settings, limits, voices, usage) and `PlatformApi`, the methods the shared components call |
| `svelte/*.svelte` | Shared pages and components: the page shell, the settings page with its You panel, the account page, the first-admin page; the audio button and its loader, the waiting indicator, the sharing control, and the settings cards (AI, written interpretations, voices, limits, usage, people) |
| `svelte/prefs.svelte.ts` | Saved preferences: kept on the device, saved to the account, synced once per visit |
| `svelte/http.ts` | The server build's backend handle: request helpers and the methods every app's API has (`commonHttp`) |
| `svelte/local.ts`, `localjournal.ts`, `keystore.ts`, `llm.ts`, `demovoice.ts` | The public demo's backend in the browser: the methods every demo has (`commonLocal`), the journal in IndexedDB, the visitor's own AI key, the AI call from the browser, and readings read aloud in the visitor's key (the app hands in its readers' voices) |
| `svelte/serviceworker.ts` | The offline service worker: the app's `src/service-worker.ts` hands in what SvelteKit gives it, its cache names and any cache of its own that outlasts a release |
| `testing/viewport.ts` | Playwright checks for phone and desktop layouts: on screen, reachable by finger, nothing poking out sideways |
| `testing/static-server.mjs` | A stand-in static host for testing a single-page app's static build |
| `docs/design.md` | Why this exists, what belongs here, and the order things move in |

## Using it from an app

Both halves are versioned together and installed from a release tag's source
archive, so neither needs git or a package registry at build time.

Server (`backend/pyproject.toml`):

```toml
dependencies = [
    "fortunes-platform @ https://github.com/jhaycr/fortunes-platform/archive/refs/tags/v0.9.0.tar.gz#subdirectory=python",
]

[tool.hatch.metadata]
allow-direct-references = true
```

Web pages (`frontend/package.json`):

```json
"devDependencies": {
	"fortunes-platform": "https://github.com/jhaycr/fortunes-platform/archive/refs/tags/v0.9.0.tar.gz"
}
```

The server modules need to know which app they are serving: its name, the
prefix of its environment variables, its data directory, its readings' own
columns, its prompts and its readers' voices. The app hands those in once,
from its package's `__init__.py`, before anything else runs (see
`fortunes_platform/host.py`):

```python
from fortunes_platform import host

host.configure(host.Host(
    id="myapp",                    # the session cookie is myapp_session
    name="My App",
    env_prefix="MYAPP_",           # MYAPP_SECRET_KEY, MYAPP_AUTH_MODE, ...
    data_dir=lambda: paths.data_dir(),
    journal=lambda: importlib.import_module("myapp.db").JOURNAL,
    prompts=lambda: importlib.import_module("myapp.interpret").PROMPTS,
    voices=lambda: importlib.import_module("myapp.tts").VOICES,
))
```

The journal (`fortunes_platform/db.py`) is one SQLite file with the readings,
their sharing and interpretations, users, sessions, settings and the usage
ledger. The app's own part is a `Journal`: how its readings' columns read
back, what a share list shows, and its own migrations, numbered apart from the
core's. An app's db module re-exports the journal and adds its own:

```python
from fortunes_platform.db import *  # noqa: F401,F403
from fortunes_platform.db import Journal, _reading_dict, connect

JOURNAL = Journal(reading=..., pieces=..., share_columns="deck, spread")
```

The HTTP routes every app serves (`fortunes_platform/api.py`: sign-in and
first-admin setup, who is signed in, people, the AI, voice and limit
settings, usage, the journal's list with sharing and deletion, speech) come
with their middleware from `create_app`. The app adds its own routes, then
serves its built pages last:

```python
app = api.create_app(my_settings=get_my_settings, people=reassign)

@app.get("/api/cards")
def cards(): ...

api.serve_frontend(app, Path(__file__).parents[3] / "frontend" / "build")
```

`people` is the app's module with its part of handing a person's data on
or erasing an account: `move_files(src, dst)` and `erase_files(username)`,
for the files it keeps per person. Each returns what it did, which is added
to the report; the journal's part is `fortunes_platform.people`.

App code and its tests reach the AI call through `fortunes_platform.ai`
(`ai.config()`, `ai.interpret_stream(...)`), so a test's stand-in for it
reaches the shared routes too.

The app's global stylesheet starts with the suite's, and adds only what is
its own after it:

```css
@import 'fortunes-platform/styles.css';
```

The shared components need the same of the web half: the app's name, its
backend handle (`api`, which has at least the methods of `PlatformApi`), and a
few words about its subject. The app hands them in once, from a module its
root layout imports before anything renders (see `svelte/app.ts`):

```ts
// src/lib/platform.ts, imported first by src/routes/+layout.svelte
import { configure } from 'fortunes-platform/app';
import { api } from '$lib/api';

configure({
	id: 'myapp',
	name: 'Little Fortunes · My App',
	short: 'My App',
	api,
	words: { withoutAi: 'Shuffling and the journal', practice: 'shuffling' }
});
```

The root layout renders the shared shell around the app's pages:

```svelte
<Shell {links} {favicon} path={page.url.pathname} onme={(m) => syncPrefs(m.settings)}>
	{#snippet mark()}<Mark />{/snippet}
	{@render children()}
</Shell>
```

An app's two backend adapters spread in the methods every app has, beside
its own, with its own types for a reading, its settings and its account:

```ts
// src/lib/backend/http.ts
export const api = {
	...commonHttp<SavedReading, UserSettings, Account>(),
	capabilities,
	hexagrams: () => get<Hexagram[]>('/api/hexagrams')
};

// src/lib/backend/static.svelte.ts (the public demo)
export const api: Backend = {
	...commonLocal<SavedReading, UserSettings, Account>({ journal, data, settings }),
	capabilities,
	hexagrams: async () => (await data()).hexagrams
};
```

```ts
import AudioButton from 'fortunes-platform/AudioButton.svelte';
import AiPanel from 'fortunes-platform/AiPanel.svelte';
import type { Me, UserSettingsBase } from 'fortunes-platform/types';
import { expectOnScreen } from 'fortunes-platform/testing/viewport';
```

## Changing it

1. Make the change here, with a test.
2. `npm test` (type check, the stylesheet and static-host tests, and the
   component tests under `svelte/`) and `pytest python/tests` (from a venv with
   `pip install -e "python[dev]"`).
3. Raise the version in **both** `package.json` and `python/pyproject.toml`
   (a test fails if they differ), commit, and tag `vX.Y.Z`.
4. In each app, point both dependencies at the new tag, reinstall, and run the
   app's own tests.

`testing/viewport.js` and `viewport.d.ts` are built from `viewport.ts` by
`npm run build`; `npm test` fails if they are out of date.

## Licence

MIT, see [LICENSE](LICENSE).
