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
| `python/fortunes_platform/` | The server half (package `fortunes-platform`): sign-in (proxy header and OIDC), users, sessions, encryption at rest, the YAML config file, spending limits, the voice providers, and Server-Sent Events framing |
| `styles/suite.css` | The suite's look: colour tokens, page defaults, and the small classes every page repeats (`.dim`, `.error`, `.danger`, `.locktag`, disabled buttons) |
| `svelte/app.ts` | Where an app hands itself in to the web half: its name, its backend handle, a few words about its subject |
| `svelte/types.ts` | The wire types every app shares (who is signed in, people, settings, limits, voices, usage) and `PlatformApi`, the methods the shared components call |
| `svelte/*.svelte` | Shared components: the audio button and its loader, the waiting indicator, the sharing control, the first-admin page, and the settings cards (AI, written interpretations, voices, limits, usage, people) |
| `testing/viewport.ts` | Playwright checks for phone and desktop layouts: on screen, reachable by finger, nothing poking out sideways |
| `testing/static-server.mjs` | A stand-in static host for testing a single-page app's static build |
| `docs/design.md` | Why this exists, what belongs here, and the order things move in |

## Using it from an app

Both halves are versioned together and installed from a release tag's source
archive, so neither needs git or a package registry at build time.

Server (`backend/pyproject.toml`):

```toml
dependencies = [
    "fortunes-platform @ https://github.com/jhaycr/fortunes-platform/archive/refs/tags/v0.4.0.tar.gz#subdirectory=python",
]

[tool.hatch.metadata]
allow-direct-references = true
```

Web pages (`frontend/package.json`):

```json
"devDependencies": {
	"fortunes-platform": "https://github.com/jhaycr/fortunes-platform/archive/refs/tags/v0.4.0.tar.gz"
}
```

The server modules need to know which app they are serving: its name, the
prefix of its environment variables, its data directory and its database. The
app hands those in once, from its package's `__init__.py`, before anything
else runs (see `fortunes_platform/host.py`):

```python
from fortunes_platform import host

host.configure(host.Host(
    id="myapp",                    # the session cookie is myapp_session
    name="My App",
    env_prefix="MYAPP_",           # MYAPP_SECRET_KEY, MYAPP_AUTH_MODE, ...
    data_dir=lambda: paths.data_dir(),
    db=lambda: importlib.import_module("myapp.db"),
))
```

The app's database module provides the tables the shared code reads (users,
sessions, settings and the spending ledger); see `Host.db` for the functions
called.

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
