// Spoken readings for the public demo: the visitor's own key, straight from
// the browser to OpenRouter's /audio/speech (the OpenAI-shaped endpoint the
// server's openai adapter speaks). Nothing passes through a server of ours,
// and the page's CSP limits the key to openrouter.ai.
//
// The app hands in its readers' voices; this does the rest: splitting a long
// script, the request and its errors, joining the pieces, a cache in this
// browser so a piece heard once replays free, and the price in plain words.
//
//     const speech = demoSpeech({ voices: VOICES, maxChars: 2500, cache: 'demo-audio-v2' });
//     export const { speaks, synthesize } = speech;

import { ApiError } from './types';
import { authHeaders, explain } from './llm';
import { getLlmPrefs } from './keystore';

export interface DemoVoice {
	model: string;
	/** one of the model's own voices */
	voice: string;
	/** How the audio comes back: 'mp3' plays as it is; 'pcm' is raw samples
	 * (24 kHz, 16-bit, mono) that are joined and given a WAV header. */
	format: 'mp3' | 'pcm';
	/** the rest of the request: a written direction, the provider's options */
	request?: Record<string, unknown>;
	/** What decides how it sounds besides the model and voice, for the cache
	 * key: a change is heard fresh instead of replayed. The request, if not given. */
	sound?: unknown[];
	/** USD per character as listed when the voice was chosen, for when the live price cannot be read */
	listedPerChar: number;
}

export interface DemoSpeechOptions {
	/** by persona slug; a reader not here has no audio button */
	voices: Record<string, DemoVoice>;
	/** the most characters one request carries */
	maxChars: number;
	/** the name of this browser's cache of pieces already heard */
	cache: string;
}

/** tts._chunks, simplified: paragraphs, then sentences, never dropping text. */
export function chunks(script: string, max: number): string[] {
	if (script.length <= max) return [script];
	const pieces: string[] = [];
	for (const para of script.split(/\n{2,}/)) {
		if (para.length <= max) pieces.push(para);
		else
			for (const s of para.split(/(?<=[.!?])\s+/))
				for (let i = 0; i < s.length; i += max) pieces.push(s.slice(i, i + max));
	}
	const out: string[] = [];
	let cur = '';
	for (const p of pieces) {
		if (cur && cur.length + p.length + 2 > max) {
			out.push(cur);
			cur = p;
		} else cur = cur ? `${cur}\n\n${p}` : p;
	}
	if (cur.trim()) out.push(cur);
	return out;
}

/** The sample rate of 'pcm' voices. */
export const PCM_RATE = 24000;

/** PCM as a WAV file a browser can play: the 44-byte RIFF header, then the samples. */
export function wav(pcm: Uint8Array, rate = PCM_RATE): Blob {
	const head = new DataView(new ArrayBuffer(44));
	const ascii = (at: number, text: string) => [...text].forEach((c, i) => head.setUint8(at + i, c.charCodeAt(0)));
	ascii(0, 'RIFF');
	head.setUint32(4, 36 + pcm.length, true);
	ascii(8, 'WAVE');
	ascii(12, 'fmt ');
	head.setUint32(16, 16, true); // the fmt chunk's size
	head.setUint16(20, 1, true); // PCM
	head.setUint16(22, 1, true); // mono
	head.setUint32(24, rate, true);
	head.setUint32(28, rate * 2, true); // bytes a second
	head.setUint16(32, 2, true); // bytes a sample
	head.setUint16(34, 16, true); // bits a sample
	ascii(36, 'data');
	head.setUint32(40, pcm.length, true);
	return new Blob([head.buffer, pcm as BlobPart], { type: 'audio/wav' });
}

/** The cache key of a script in a voice: null where the browser offers no
 * hashing (crypto.subtle needs a secure context), and then audio still plays
 * but replays are paid for again. */
export async function cacheKey(baseUrl: string, v: DemoVoice, script: string): Promise<string | null> {
	if (typeof crypto === 'undefined' || !crypto.subtle) return null;
	const sound = v.sound ?? [v.request ?? {}];
	const data = new TextEncoder().encode(JSON.stringify([baseUrl, v.model, v.voice, ...sound, script]));
	const hash = await crypto.subtle.digest('SHA-256', data);
	const hex = [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, '0')).join('');
	return `https://demo-audio.invalid/${hex}.${v.format === 'pcm' ? 'wav' : 'mp3'}`;
}

async function speechRequest(baseUrl: string, key: string, v: DemoVoice, input: string): Promise<Blob> {
	let res: Response;
	try {
		res = await fetch(`${baseUrl}/audio/speech`, {
			method: 'POST',
			headers: authHeaders(key),
			body: JSON.stringify({ model: v.model, voice: v.voice, ...v.request, input, response_format: v.format })
		});
	} catch {
		throw new ApiError(explain(0, null, 'OpenRouter'), 0);
	}
	if (!res.ok) {
		let detail: string | null = null;
		try {
			const body = await res.json();
			detail = body?.error?.message ?? null;
		} catch {
			/* not JSON */
		}
		if (res.status === 402)
			throw new ApiError('Your OpenRouter credit is used up. Add credit on openrouter.ai to hear readings aloud.', 402);
		throw new ApiError(explain(res.status, detail, 'OpenRouter'), res.status);
	}
	return res.blob();
}

/** The pieces as one playable file. */
async function join(parts: Blob[], format: DemoVoice['format']): Promise<Blob> {
	if (format === 'mp3') return new Blob(parts, { type: 'audio/mpeg' });
	// raw samples join seamlessly; one header goes on the whole
	const bytes = await Promise.all(parts.map(async (b) => new Uint8Array(await b.arrayBuffer())));
	const pcm = new Uint8Array(bytes.reduce((n, x) => n + x.length, 0));
	bytes.reduce((at, x) => (pcm.set(x, at), at + x.length), 0);
	return wav(pcm);
}

export function demoSpeech(o: DemoSpeechOptions) {
	async function cached(key: string): Promise<Blob | null> {
		try {
			const hit = await (await caches.open(o.cache)).match(key);
			return hit ? await hit.blob() : null;
		} catch {
			return null; // no Cache Storage (private window, blocked site data)
		}
	}

	async function remember(key: string, blob: Blob): Promise<void> {
		try {
			await (await caches.open(o.cache)).put(key, new Response(blob, { headers: { 'content-type': blob.type } }));
		} catch {
			// replays will simply be paid for again
		}
	}

	let lastUrl: string | null = null;

	return {
		voices: o.voices,

		/** Whether `persona` (or, when none is chosen, the default) has a voice. */
		speaks(persona: string | null | undefined, fallback: string): boolean {
			return (persona || fallback) in o.voices;
		},

		/** Audio for `script` in `persona`'s voice, as an object URL for the
		 * shared audio element. A piece already heard plays from this browser's cache. */
		async synthesize(script: string, persona: string): Promise<string> {
			const v = o.voices[persona];
			if (!v) throw new ApiError('This reader has no voice in the demo yet.', 404);
			const { key, baseUrl } = getLlmPrefs();
			if (!key) throw new ApiError('Add your own AI key in Settings to hear readings aloud.', 404);
			const ck = await cacheKey(baseUrl, v, script);
			let blob = ck ? await cached(ck) : null;
			if (!blob) {
				const parts: Blob[] = [];
				for (const c of chunks(script, o.maxChars)) parts.push(await speechRequest(baseUrl, key, v, c));
				blob = await join(parts, v.format);
				if (ck) await remember(ck, blob);
			}
			if (lastUrl) URL.revokeObjectURL(lastUrl);
			lastUrl = URL.createObjectURL(blob);
			return lastUrl;
		}
	};
}

// ---- what it costs ---------------------------------------------------------

/** A voice billed by the audio token (Gemini TTS) bills 25 to a second of
 * speech (Google's pricing page), and an unhurried reading speaks about 15
 * characters a second. */
export const AUDIO_TOKENS_PER_SECOND = 25;
export const CHARS_PER_SECOND = 15;

/** USD per character for voice `v`'s model, live from OpenRouter's public
 * model list (no key needed); null when it cannot be read. A model billed by
 * the audio token is converted at the rates above; one billed by the
 * character is read as it is. */
export async function livePricePerChar(baseUrl: string, v: DemoVoice): Promise<number | null> {
	try {
		const res = await fetch(`${baseUrl}/models?output_modalities=speech`);
		if (!res.ok) return null;
		const rows: { id: string; pricing?: { prompt?: string; completion?: string } }[] = (await res.json())?.data ?? [];
		const price = rows.find((m) => m.id === v.model)?.pricing;
		const audio = Number(price?.completion);
		const perChar = audio > 0 ? (audio * AUDIO_TOKENS_PER_SECOND) / CHARS_PER_SECOND : Number(price?.prompt);
		return Number.isFinite(perChar) && perChar > 0 ? perChar : null;
	} catch {
		return null;
	}
}

/** "under a cent", "about a cent", "about 3 cents", "about $1.20". */
export function aboutCost(usd: number): string {
	if (usd < 0.01) return 'under a cent';
	if (usd < 0.015) return 'about a cent';
	if (usd < 1) return `about ${Math.round(usd * 100)} cents`;
	return `about $${usd.toFixed(2)}`;
}

/** "$0.015 per 1,000 characters", "$0.08 per 1,000 characters": two
 * significant figures under a dime, cents above. */
export function perThousand(perChar: number): string {
	const usd = perChar * 1000;
	return `$${usd >= 0.1 ? usd.toFixed(2) : Number(usd.toPrecision(2))} per 1,000 characters`;
}
