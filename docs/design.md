# Design: one platform under the Little Fortunes apps

## Why

Little Fortunes is a small suite of self-hosted apps: Tarotarium (tarot) and
I Ching. I Ching began as a copy of Tarotarium with the tarot taken out, so
underneath their own subjects the two are the same app: sign-in, users and
sessions, settings, an AI reader with spending limits, voices, a journal with
sharing, and a public demo that runs in the browser alone.

Since the copy, every change to that common part has had to be made twice, by
hand, and several have been made only once. Each app has fixes the other
lacks. This package holds the common part once, so a fix is made once and
every app takes it by moving to the new version.

## What belongs here

Code that more than one app uses **the same way**. Where the apps differ only
by name, the difference is handed in: the app's name, its settings prefix, its
voices and personas, the words it speaks before a reading.

What stays in each app: its subject (cards, decks, spreads and guidebooks; or
hexagrams, casting and texts), its prompts, the shape of its saved readings,
and its pages built around those.

| Area | Server | Web pages | Tests |
|---|---|---|---|
| Already identical in both apps | SSE framing, the voice-provider base | the settings card shell and its status type; the audio button and waiting indicator (once their loader is shared) | the phone and desktop layout checks, the static-host stand-in |
| Same but for the app's name | sign-in (header and OIDC), users, sessions, encryption at rest, config, spending limits, the voice adapters | the sign-in setup page, the AI, key, voice and limits cards, the sharing control | the security-header and proxy-trust tests |
| Same idea, drifted apart | the database core and its migrations, text-to-speech and its cache, the AI call, the routes both apps serve | the page shell and nav, colours and common styles, the settings and account pages, the browser-local journal, saved preferences | |

## How apps take it

One repository, two halves, one version. The Python half is `python/` (package
`fortunes-platform`); the JavaScript half is the repository root (package
`fortunes-platform`). A release is a tag, `vX.Y.Z`, and each app pins both
halves to that tag's source archive on GitHub. Nothing needs a token, git or a
package registry at build time, so a container build anywhere and someone
building an app from source get the same thing.

Svelte components and TypeScript types ship as source and are compiled by the
app's own build. The Playwright helpers ship built (`viewport.js` and its
types), because Playwright does not compile TypeScript inside `node_modules`.

## Order of work

Each step leaves both apps' full test suites passing, and is its own release.

1. **What is already identical.** Moves with no change in behaviour, and
   proves the packaging end to end.
2. **What differs only by name.** The app's name, settings prefix and data
   directory are handed in. *Done in v0.2.0 for the server half:* sign-in,
   users, sessions, encryption at rest, config, spending limits and the voice
   adapters, with the app handing in its names through `host.configure`. The
   app also hands in its database module, so these move ahead of the
   database core (step 5). The web pages on that row move with step 4.
3. **Shared styles.** The colour tokens and the small classes every page
   repeats (dim text, errors, danger buttons, the Self-hosted tag, disabled
   buttons). *Done in v0.3.0:* `styles/suite.css`, which
   each app imports first from its own global stylesheet. A page's scoped
   rule for one of these classes now adds to it (a margin, a size) instead of
   restating it. The merge brought the disabled-button look, which only I Ching
   had, to Tarotarium, and the dim text the account pages asked for but never
   got.
4. **Components that differ only by name**, once both apps read their name
   from one place. *Done in v0.4.0:* the app hands its name, its backend
   handle and a few words about its subject to `configure` (`svelte/app.ts`),
   and the shared components read them from there: the audio button, its
   loader and the waiting indicator; the sharing control; the first-admin
   page; and the settings cards for AI, written interpretations, voices,
   limits, usage and people. The wire types those use are in `svelte/types.ts`.
   The key card (the public demo's own key) and the personal settings panel
   have drifted with the browser-local backend, and move with it in step 5.
5. **What has drifted.** Merged one piece at a time, and every fix that only
   one app has goes to both as part of the merge. The database core gets its
   own migration numbering, separate from each app's, with a one-time mapping
   for existing databases. *Done in v0.5.0, on the server:* the journal
   database (`db.py`), with the core's migrations 1 to 9 (the steps both apps
   already shared) and each app's own from 1; an old database's single number
   maps once (I Ching's step 10 is its step 1) and is left as it was, so an
   older build can still open the file. Speech (`tts.py`) with its cache, and
   the AI call (`ai.py`), read the app's voices and prompts as handed in. Fixes
   one app had went to both: a damaged list of a reading's books reads as none,
   recording no books writes nothing, and changing a reading's sharing answers
   with the whole reading, as reading it does. *Done in v0.6.0:* the routes
   both apps serve (`api.py`, with their middleware and the static pages'
   fallback; the app hands in its own settings, its people tools and the last
   shaping of a reading), and on the web the page shell, the settings page
   with its You panel, the account page and saved preferences. Fixes one app
   had went to both: an unknown /api/ path is a 404, never the app's page;
   on a phone the account sits beside the name instead of on a row of its
   own. *Done in v0.7.0:* the browser-local journal behind the public demo
   (`local.ts` and `localjournal.ts`, with the visitor's key in `keystore.ts`
   and the AI call from the browser in `llm.ts`), the server build's shared
   API methods (`http.ts`), and the key card. What stays in each app is its
   subject: its own reading fields, its readers' voices, its prompts, and the
   pages built around them. *Done in v0.8.0:* what was left of the drift. The
   journal's part of handing a person's data on or erasing an account
   (`people.py`; the app hands in its files' part); the offline service
   worker (`serviceworker.ts`; the app hands in its cache names, and
   Tarotarium its card-art cache); and the demo's readings read aloud
   (`demovoice.ts`: splitting, the request, joining the pieces, the browser's
   cache and the price; the app hands in its voices). Cache names and cache
   keys are the ones each app used, so what a visitor has cached stays
   cached. Fixes one app had went to both: a price between one cent and a
   cent and a half reads "about a cent", a price per thousand characters
   under a dime keeps two significant figures, and a voice billed by the
   audio token is priced per character.

## Rules

- Nothing personal and nothing about where an app is hosted goes in this
  repository: no hostnames, no accounts, no paths from anyone's machine.
- A change here comes with its test here. An app's own suite then proves it in
  place before the app moves to the new version.
- Both halves move together: one version number, one tag.
