import { afterEach, describe, expect, it, vi } from 'vitest';
import { cacheNames, serve, stale, type ServiceWorkerOptions } from './serviceworker';

const tarot: ServiceWorkerOptions = {
	prefix: 'tarotarium',
	build: ['/_app/a.js'],
	files: ['/favicon.png'],
	version: '42',
	lasting: [{ name: 'cards', match: (u) => /^\/api\/decks\/[^/]+\/cards\/\d+$/.test(u.pathname) }]
};
const iching: ServiceWorkerOptions = { prefix: 'iching', build: [], files: [], version: '7', alsoClear: ['trigrams-'] };

describe('cache names', () => {
	it('keep the names the apps used before, so a release keeps what visitors have cached', () => {
		expect(cacheNames(tarot)).toEqual({
			app: 'tarotarium-v2-app-42',
			runtime: 'tarotarium-v2-runtime-42',
			lasting: ['tarotarium-v2-cards']
		});
		expect(cacheNames(iching).app).toBe('iching-v2-app-7');
	});

	it('clear the app’s old caches and nobody else’s', () => {
		const keys = [
			'tarotarium-v2-app-41',
			'tarotarium-v2-app-42',
			'tarotarium-v2-runtime-41',
			'tarotarium-v2-cards',
			'tarotarium-cards',
			'demo-audio-v1',
			'iching-v2-app-6'
		];
		expect(stale(keys, tarot)).toEqual(['tarotarium-v2-app-41', 'tarotarium-v2-runtime-41', 'tarotarium-cards']);
		expect(stale(['iching-v2-app-6', 'iching-v2-app-7', 'trigrams-app', 'demo-audio-v2'], iching)).toEqual([
			'iching-v2-app-6',
			'trigrams-app'
		]);
	});
});

/** A stand-in worker: listeners by type, and a fetch that records what it answered. */
function worker() {
	const on: Record<string, (e: unknown) => void> = {};
	const sw = {
		location: { origin: 'https://app.test' },
		addEventListener: (type: string, fn: (e: unknown) => void) => (on[type] = fn),
		skipWaiting: vi.fn(),
		clients: { claim: vi.fn() }
	};
	const fetchEvent = (path: string, init: { method?: string; mode?: string } = {}) => {
		const request = { url: new URL(path, 'https://app.test').href, method: init.method ?? 'GET', mode: init.mode ?? 'cors' };
		const event = { request, respondWith: vi.fn() };
		on.fetch(event);
		return event;
	};
	return { sw: sw as unknown as ServiceWorkerGlobalScope, fetchEvent };
}

function memoryCaches() {
	const stores = new Map<string, Map<string, Response>>();
	const open = async (name: string) => {
		if (!stores.has(name)) stores.set(name, new Map());
		const s = stores.get(name)!;
		return {
			match: async (r: { url: string }) => s.get(r.url),
			put: async (r: { url: string }, resp: Response) => void s.set(r.url, resp),
			addAll: async () => {}
		};
	};
	return { stores, caches: { open, match: async () => undefined, keys: async () => [...stores.keys()] } };
}

afterEach(() => vi.unstubAllGlobals());

describe('serve', () => {
	it('leaves sign-in, writes, other sites and the API alone', () => {
		const { sw, fetchEvent } = worker();
		serve(sw, tarot);
		for (const e of [
			fetchEvent('/auth/callback?code=x'),
			fetchEvent('/api/readings', { method: 'POST' }),
			fetchEvent('https://openrouter.ai/api/v1/chat'),
			fetchEvent('/api/me')
		])
			expect(e.respondWith).not.toHaveBeenCalled();
	});

	it('serves a lasting cache first, and fills it once', async () => {
		const { stores, caches } = memoryCaches();
		const net = vi.fn(async () => new Response('art'));
		vi.stubGlobal('caches', caches);
		vi.stubGlobal('fetch', net);
		const { sw, fetchEvent } = worker();
		serve(sw, tarot);
		for (let i = 0; i < 2; i++) {
			const e = fetchEvent('/api/decks/rws/cards/3');
			expect(await (await e.respondWith.mock.calls[0][0]).text()).toBe('art');
		}
		expect(net).toHaveBeenCalledTimes(1);
		expect([...stores.keys()]).toEqual(['tarotarium-v2-cards']);
	});

	it('serves the demo data from the network first, and the cached copy offline', async () => {
		const { caches } = memoryCaches();
		vi.stubGlobal('caches', caches);
		vi.stubGlobal('fetch', vi.fn(async () => new Response('fresh')));
		const { sw, fetchEvent } = worker();
		serve(sw, iching);
		expect(await (await fetchEvent('/data/hexagrams.json').respondWith.mock.calls[0][0]).text()).toBe('fresh');
		vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))));
		expect(await (await fetchEvent('/data/hexagrams.json').respondWith.mock.calls[0][0]).text()).toBe('fresh');
	});
});
