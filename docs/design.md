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
   buttons).
4. **Components that differ only by name**, once both apps read their name
   from one place.
5. **What has drifted.** Merged one piece at a time, and every fix that only
   one app has goes to both as part of the merge. The database core gets its
   own migration numbering, separate from each app's, with a one-time mapping
   for existing databases.

## Rules

- Nothing personal and nothing about where an app is hosted goes in this
  repository: no hostnames, no accounts, no paths from anyone's machine.
- A change here comes with its test here. An app's own suite then proves it in
  place before the app moves to the new version.
- Both halves move together: one version number, one tag.
