// @vitest-environment node
// (one Blob and Response, as in a browser; jsdom brings its own Blob)
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './types';
import { useTestApp } from './testapp';
import {
	aboutCost,
	cacheKey,
	chunks,
	demoSpeech,
	livePricePerChar,
	PCM_RATE,
	perThousand,
	wav,
	type DemoVoice
} from './demovoice';

beforeEach(() => useTestApp());
afterEach(() => vi.unstubAllGlobals());

const MP3: DemoVoice = {
	model: 'elevenlabs/x',
	voice: 'jessica',
	format: 'mp3',
	request: { provider: { options: { elevenlabs: { voice_settings: { stability: 0.35 } } } } },
	sound: [{ stability: 0.35 }],
	listedPerChar: 0.00008
};
const PCM: DemoVoice = {
	model: 'google/tts',
	voice: 'Charon',
	format: 'pcm',
	request: { instructions: 'Slow and low.' },
	listedPerChar: 0.000015
};

describe('chunks', () => {
	it('splits long scripts without dropping text', () => {
		const para = 'One sentence here. '.repeat(30).trim();
		const script = [para, para, para].join('\n\n');
		const parts = chunks(script, 700);
		expect(parts.length).toBeGreaterThan(1);
		for (const p of parts) expect(p.length).toBeLessThanOrEqual(700);
		expect(parts.join(' ').replace(/\s+/g, ' ')).toBe(script.replace(/\s+/g, ' '));
		expect(chunks('short', 700)).toEqual(['short']);
	});
});

describe('wav', () => {
	it('wraps raw samples in a header a browser can play', async () => {
		const blob = wav(new Uint8Array([1, 0, 2, 0, 3, 0]));
		expect(blob.type).toBe('audio/wav');
		const b = new DataView(await blob.arrayBuffer());
		const text = (at: number) => String.fromCharCode(...[0, 1, 2, 3].map((i) => b.getUint8(at + i)));
		expect([text(0), text(8), text(12), text(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data']);
		expect(b.getUint32(4, true)).toBe(36 + 6);
		expect([b.getUint16(20, true), b.getUint16(22, true), b.getUint32(24, true), b.getUint16(34, true)]).toEqual([1, 1, PCM_RATE, 16]);
		expect(b.getUint32(40, true)).toBe(6);
		expect(b.byteLength).toBe(44 + 6);
	});
});

describe('cacheKey', () => {
	it('hashes what decides the sound, as the apps did, so pieces already heard stay free', async () => {
		const sha = async (parts: unknown[]) => {
			const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(parts)));
			return [...new Uint8Array(h)].map((x) => x.toString(16).padStart(2, '0')).join('');
		};
		expect(await cacheKey('https://o.test', MP3, 'Hi.')).toBe(
			`https://demo-audio.invalid/${await sha(['https://o.test', 'elevenlabs/x', 'jessica', { stability: 0.35 }, 'Hi.'])}.mp3`
		);
		// with no `sound`, the whole request decides it
		expect(await cacheKey('https://o.test', PCM, 'Hi.')).toBe(
			`https://demo-audio.invalid/${await sha(['https://o.test', 'google/tts', 'Charon', { instructions: 'Slow and low.' }, 'Hi.'])}.wav`
		);
	});
});

function visitor(key: string | null) {
	vi.stubGlobal('sessionStorage', { getItem: (k: string) => (k === 'testapp.llm.key' ? key : null), setItem() {}, removeItem() {} });
	vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL() {} }));
	const store = new Map<string, Response>();
	vi.stubGlobal('caches', {
		open: async () => ({
			match: async (k: string) => store.get(k)?.clone(),
			put: async (k: string, r: Response) => void store.set(k, r)
		})
	});
	return store;
}

describe('demoSpeech', () => {
	const speech = demoSpeech({ voices: { alice: MP3, elder: PCM }, maxChars: 40, cache: 'demo-audio-test' });

	it('knows who has a voice, a missing persona meaning the default', () => {
		expect(speech.speaks('alice', 'elder')).toBe(true);
		expect(speech.speaks(null, 'elder')).toBe(true);
		expect(speech.speaks('maud', 'alice')).toBe(false);
	});

	it('asks for each piece in the voice, joins them, and replays from the cache for free', async () => {
		const store = visitor('sk-or-test');
		const sent: Record<string, unknown>[] = [];
		const net = vi.fn(async (_url: string, init: RequestInit) => {
			sent.push(JSON.parse(String(init.body)));
			return new Response(new Uint8Array([1, 0]));
		});
		vi.stubGlobal('fetch', net);
		const script = 'The first part is here.\n\nThe second part follows it.';
		expect(await speech.synthesize(script, 'elder')).toBe('blob:x');
		expect(sent).toEqual([
			{ model: 'google/tts', voice: 'Charon', instructions: 'Slow and low.', input: 'The first part is here.', response_format: 'pcm' },
			{ model: 'google/tts', voice: 'Charon', instructions: 'Slow and low.', input: 'The second part follows it.', response_format: 'pcm' }
		]);
		const [blob] = [...store.values()];
		expect(blob.headers.get('content-type')).toBe('audio/wav');
		expect((await blob.clone().arrayBuffer()).byteLength).toBe(44 + 4);
		await speech.synthesize(script, 'elder');
		expect(net).toHaveBeenCalledTimes(2);

		await speech.synthesize('Short.', 'alice');
		expect(sent[2]).toEqual({
			model: 'elevenlabs/x',
			voice: 'jessica',
			provider: { options: { elevenlabs: { voice_settings: { stability: 0.35 } } } },
			input: 'Short.',
			response_format: 'mp3'
		});
		expect([...store.values()][1].headers.get('content-type')).toBe('audio/mpeg');
	});

	it('says what is missing in words a visitor can act on', async () => {
		visitor(null);
		await expect(speech.synthesize('Hi.', 'maud')).rejects.toThrow('no voice in the demo');
		await expect(speech.synthesize('Hi.', 'alice')).rejects.toThrow('Add your own AI key');
		visitor('sk-or-test');
		vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 402 })));
		const err = await speech.synthesize('Hi.', 'alice').catch((e) => e);
		expect(err).toBeInstanceOf(ApiError);
		expect([err.status, err.message]).toEqual([402, expect.stringContaining('credit is used up')]);
	});
});

describe('costs', () => {
	it('reads a price per audio token as one per character, and a per-character one as it is', async () => {
		const models = (pricing: object) => vi.fn(async () => new Response(JSON.stringify({ data: [{ id: PCM.model, pricing }] })));
		vi.stubGlobal('fetch', models({ prompt: '0.0000005', completion: '0.000009' }));
		expect(await livePricePerChar('https://o.test', PCM)).toBeCloseTo(0.000015, 12);
		vi.stubGlobal('fetch', models({ prompt: '0.00008', completion: '0' }));
		expect(await livePricePerChar('https://o.test', PCM)).toBe(0.00008);
		vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 500 })));
		expect(await livePricePerChar('https://o.test', PCM)).toBeNull();
	});

	it('puts amounts in plain words', () => {
		expect(aboutCost(0.004)).toBe('under a cent');
		expect(aboutCost(0.012)).toBe('about a cent');
		expect(aboutCost(0.2)).toBe('about 20 cents');
		expect(aboutCost(1.234)).toBe('about $1.23');
		expect(perThousand(0.000015)).toBe('$0.015 per 1,000 characters');
		expect(perThousand(0.00004)).toBe('$0.04 per 1,000 characters');
		expect(perThousand(0.00008)).toBe('$0.08 per 1,000 characters');
		expect(perThousand(0.0002)).toBe('$0.20 per 1,000 characters');
	});
});
