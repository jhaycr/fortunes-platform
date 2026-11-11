/// <reference lib="webworker" />

// The offline service worker every app installs. The app's own
// src/service-worker.ts hands in what SvelteKit gives it and its cache names:
//
//     import { build, files, version } from '$service-worker';
//     import { serve } from 'fortunes-platform/serviceworker';
//     serve(self as unknown as ServiceWorkerGlobalScope, { prefix: 'iching', build, files, version });
//
// What it does with each request:
// - sign-in (/auth/) and anything not GET or not this site: left alone
// - a lasting cache the app names (card art): cache first
// - other /api/ calls: network only
// - the static demo's data (/data/*.json): network first, the cached copy offline
// - the app shell and pages: the cached copy, else the network, kept for offline

export interface Lasting {
	/** the cache is `<prefix>-v2-<name>` */
	name: string;
	/** Requests it serves. Their URLs must change when their content does
	 * (a ?v= on the URL): this cache outlives releases. */
	match: (url: URL) => boolean;
}

export interface ServiceWorkerOptions {
	/** The start of every cache name (the app's storage prefix). Any other
	 * cache starting with it is an old one and is cleared. */
	prefix: string;
	/** from $service-worker */
	build: string[];
	files: string[];
	version: string;
	/** older cache-name prefixes of the app's, also cleared */
	alsoClear?: string[];
	lasting?: Lasting[];
}

/** The static demo's data JSON: small, versioned by the build. */
function isDemoData(url: URL): boolean {
	return /^\/data\/[\w-]+\.json$/.test(url.pathname);
}

/** The cache names in use: the app shell's, the runtime one's and the lasting ones. */
export function cacheNames(o: ServiceWorkerOptions): { app: string; runtime: string; lasting: string[] } {
	return {
		app: `${o.prefix}-v2-app-${o.version}`,
		// Versioned: it holds cached pages, so an unversioned one could serve an
		// old app shell pointing at build files that no longer exist.
		runtime: `${o.prefix}-v2-runtime-${o.version}`,
		lasting: (o.lasting ?? []).map((l) => `${o.prefix}-v2-${l.name}`)
	};
}

/** The caches `activate` clears: the app's, other than the ones in use. */
export function stale(keys: string[], o: ServiceWorkerOptions): string[] {
	const names = cacheNames(o);
	const keep = new Set([names.app, names.runtime, ...names.lasting]);
	const ours = [`${o.prefix}-`, ...(o.alsoClear ?? [])];
	return keys.filter((k) => ours.some((p) => k.startsWith(p)) && !keep.has(k));
}

export function serve(sw: ServiceWorkerGlobalScope, o: ServiceWorkerOptions): void {
	const names = cacheNames(o);
	const assets = [...o.build, ...o.files];
	const lasting = (o.lasting ?? []).map((l, i) => ({ ...l, cache: names.lasting[i] }));

	sw.addEventListener('install', (event) => {
		event.waitUntil(
			caches
				.open(names.app)
				.then((cache) => cache.addAll(assets))
				.then(() => sw.skipWaiting())
		);
	});

	sw.addEventListener('activate', (event) => {
		event.waitUntil(
			caches
				.keys()
				.then((keys) => Promise.all(stale(keys, o).map((k) => caches.delete(k))))
				.then(() => sw.clients.claim())
		);
	});

	sw.addEventListener('fetch', (event) => {
		if (event.request.method !== 'GET') return;
		const url = new URL(event.request.url);
		if (url.origin !== sw.location.origin) return;

		// the sign-in handshake must never be intercepted or cached: redirects,
		// cookies and one-time state all live here
		if (url.pathname.startsWith('/auth/')) return;

		const keep = lasting.find((l) => l.match(url));
		if (keep) {
			event.respondWith(
				caches.open(keep.cache).then(async (cache) => {
					const hit = await cache.match(event.request);
					if (hit) return hit;
					const resp = await fetch(event.request);
					if (resp.ok && !resp.redirected) cache.put(event.request, resp.clone());
					return resp;
				})
			);
			return;
		}

		// other API calls: network only
		if (url.pathname.startsWith('/api/')) return;

		// demo data: network first (a new deploy's data wins), the cached copy
		// when offline
		if (isDemoData(url)) {
			event.respondWith(
				caches.open(names.runtime).then(async (cache) => {
					try {
						const resp = await fetch(event.request);
						if (resp.ok) cache.put(event.request, resp.clone());
						return resp;
					} catch (err) {
						const hit = await cache.match(event.request);
						if (hit) return hit;
						throw err;
					}
				})
			);
			return;
		}

		// app shell and pages: what is cached is served from the cache; the rest
		// comes from the network, and build files and pages are kept for
		// offline. Offline, a page falls back to the cached app shell. Never
		// cache a redirected or failed answer: a cached sign-in redirect would
		// wedge the app shell.
		event.respondWith(
			caches.match(event.request).then(async (hit) => {
				if (hit) return hit;
				try {
					const resp = await fetch(event.request);
					if (
						resp.ok &&
						!resp.redirected &&
						(event.request.mode === 'navigate' || assets.includes(url.pathname))
					) {
						const cache = await caches.open(names.runtime);
						cache.put(event.request, resp.clone());
					}
					return resp;
				} catch (err) {
					if (event.request.mode === 'navigate') {
						const fallback = await caches.match('/');
						if (fallback) return fallback;
					}
					throw err;
				}
			})
		);
	});
}
