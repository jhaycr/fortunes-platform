// The browser's own LLM client: OpenAI-compatible chat completions straight
// from the page to the provider with the visitor's key (OpenRouter by
// default — CORS-open, streams over fetch, mints visitor-scoped keys through
// its OAuth PKCE flow). The TypeScript twin of interpret.py's `_chat_body`,
// `_auth_headers` and the SSE loop in `interpret_stream`. Provider failures
// become ApiErrors with plain-language messages, because the pages print
// `e.message` verbatim.
import { app } from './app';
import { ApiError } from './types';

export interface LlmConfig {
	baseUrl: string;
	model: string;
	key: string;
	maxTokens: number;
}

export interface ChatBody {
	model: string;
	messages: { role: 'system' | 'user'; content: string }[];
	max_tokens: number;
	stream?: true;
}

/** Mirrors interpret._chat_body: model, the two messages, max_tokens, stream. */
export function chatBody(system: string, user: string, model: string, maxTokens: number, stream: boolean): ChatBody {
	return {
		model,
		messages: [
			{ role: 'system', content: system },
			{ role: 'user', content: user }
		],
		max_tokens: maxTokens,
		...(stream ? { stream: true as const } : {})
	};
}

/** Bearer auth plus OpenRouter's attribution headers (ignored elsewhere). */
export function authHeaders(key: string): Record<string, string> {
	const h: Record<string, string> = { 'content-type': 'application/json', Authorization: `Bearer ${key}` };
	if (typeof location !== 'undefined') h['HTTP-Referer'] = location.origin;
	// a header value is bytes, not text: the name's middle dot goes as a plain hyphen
	h['X-OpenRouter-Title'] = app().name.replace(/[^\x20-\x7e]/g, '-');
	return h;
}

/** A provider failure in words a querent can act on. */
export function explain(status: number, detail?: string | null, provider = 'The AI provider'): string {
	switch (status) {
		case 0:
			return `Could not reach ${provider}. Check your connection, then try again.`;
		case 401:
		case 403:
			return 'That key was refused. Check it in Settings.';
		case 402:
			return 'Your credit with the AI provider is used up. Add credit, or switch to a free model in Settings.';
		case 404:
			return detail ? `${provider} says: ${detail}` : 'That model was not found. Pick another in Settings.';
		case 429:
			return 'The AI provider is rate-limiting this key. Wait a moment, or add credit for higher limits.';
		default:
			return detail ? `${provider} error (${status}): ${detail}` : `${provider} error (${status}).`;
	}
}

/** The `error.message` of an OpenAI/OpenRouter-style error body, if any. */
async function detailOf(res: Response): Promise<string | null> {
	try {
		const body = await res.json();
		const m = body?.error?.message ?? body?.message ?? body?.detail;
		return typeof m === 'string' && m ? m : null;
	} catch {
		return null;
	}
}

async function post(cfg: LlmConfig, body: ChatBody, signal?: AbortSignal): Promise<Response> {
	let res: Response;
	try {
		res = await fetch(`${cfg.baseUrl}/chat/completions`, {
			method: 'POST',
			headers: authHeaders(cfg.key),
			body: JSON.stringify(body),
			signal
		});
	} catch (e) {
		if ((e as Error).name === 'AbortError') throw e;
		throw new ApiError(explain(0), 0);
	}
	if (!res.ok) throw new ApiError(explain(res.status, await detailOf(res)), res.status);
	return res;
}

/** Text deltas from an OpenAI-style SSE body: skips blank lines and `:`
 * keepalive comments (OpenRouter sends `: OPENROUTER PROCESSING`), stops at
 * `[DONE]`, ignores unparseable frames, and turns a mid-stream error chunk
 * (OpenRouter reports rate limits that way, with `finish_reason: "error"`)
 * into an ApiError. */
export async function* parseSse(body: ReadableStream<Uint8Array<ArrayBuffer>>): AsyncGenerator<string> {
	const reader = body.pipeThrough(new TextDecoderStream()).getReader();
	let buf = '';
	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		buf += value;
		let nl: number;
		while ((nl = buf.indexOf('\n')) !== -1) {
			const line = buf.slice(0, nl).trim();
			buf = buf.slice(nl + 1);
			if (!line || line.startsWith(':') || !line.startsWith('data:')) continue;
			const data = line.slice(5).trim();
			if (data === '[DONE]') return;
			let chunk: {
				error?: { message?: string; code?: number };
				choices?: { delta?: { content?: string }; finish_reason?: string | null }[];
			};
			try {
				chunk = JSON.parse(data);
			} catch {
				continue;
			}
			if (chunk.error) throw new ApiError(explain(chunk.error.code ?? 500, chunk.error.message), chunk.error.code ?? 500);
			const choice = chunk.choices?.[0];
			const text = choice?.delta?.content;
			if (text) yield text;
			if (choice?.finish_reason === 'error') throw new ApiError(explain(500, 'the model stopped with an error'), 500);
		}
	}
}

/** Stream a completion's text deltas; abort through `signal`. */
export async function* llmStream(
	cfg: LlmConfig,
	system: string,
	user: string,
	signal?: AbortSignal,
	maxTokens?: number
): AsyncGenerator<string> {
	const res = await post(cfg, chatBody(system, user, cfg.model, maxTokens ?? cfg.maxTokens, true), signal);
	if (!res.body) throw new ApiError(explain(500, 'empty response'), 500);
	yield* parseSse(res.body);
}

/** One non-streaming completion (the quick reading). */
export async function llmComplete(cfg: LlmConfig, system: string, user: string, maxTokens?: number): Promise<string> {
	const res = await post(cfg, chatBody(system, user, cfg.model, maxTokens ?? cfg.maxTokens, false));
	const data = await res.json();
	const text: unknown = data?.choices?.[0]?.message?.content;
	return typeof text === 'string' ? text.trim() : '';
}

// ---- OpenRouter account helpers (the key card) -------------------------------

export interface KeyInfo {
	label: string | null;
	/** USD cap on the key, null = none. */
	limit: number | null;
	limit_remaining: number | null;
	usage: number | null;
	usage_daily: number | null;
	is_free_tier: boolean | null;
	free_model_daily_requests: { limit: number; used: number; remaining: number } | null;
}

/** GET /key: what this key may still spend. */
export async function keyInfo(cfg: Pick<LlmConfig, 'baseUrl' | 'key'>): Promise<KeyInfo> {
	let res: Response;
	try {
		res = await fetch(`${cfg.baseUrl}/key`, { headers: authHeaders(cfg.key) });
	} catch {
		throw new ApiError(explain(0), 0);
	}
	if (!res.ok) throw new ApiError(explain(res.status, await detailOf(res)), res.status);
	const body = await res.json();
	const d = body?.data ?? body ?? {};
	return {
		label: d.label ?? null,
		limit: d.limit ?? null,
		limit_remaining: d.limit_remaining ?? null,
		usage: d.usage ?? null,
		usage_daily: d.usage_daily ?? null,
		is_free_tier: d.is_free_tier ?? null,
		free_model_daily_requests: d.free_model_daily_requests ?? null
	};
}

export interface ModelInfo {
	id: string;
	name: string;
	/** USD per 1M tokens, prompt + completion; 0 for free models. */
	pricePerM: number;
	free: boolean;
}

/** GET /models (no auth): text-output models, free ones first then by price. */
export async function listModels(baseUrl: string): Promise<ModelInfo[]> {
	let res: Response;
	try {
		res = await fetch(`${baseUrl}/models`);
	} catch {
		throw new ApiError(explain(0), 0);
	}
	if (!res.ok) throw new ApiError(explain(res.status, await detailOf(res)), res.status);
	const body = await res.json();
	const rows: {
		id: string;
		name?: string;
		pricing?: { prompt?: string; completion?: string };
		architecture?: { output_modalities?: string[] };
	}[] = body?.data ?? [];
	return rows
		.filter((m) => !m.architecture?.output_modalities || m.architecture.output_modalities.includes('text'))
		.map((m) => {
			const p = Number(m.pricing?.prompt ?? 0) + Number(m.pricing?.completion ?? 0);
			return { id: m.id, name: m.name ?? m.id, pricePerM: p * 1e6, free: m.id.endsWith(':free') || p === 0 };
		})
		.sort((a, b) => Number(b.free) - Number(a.free) || a.pricePerM - b.pricePerM || a.id.localeCompare(b.id));
}

// ---- OpenRouter OAuth PKCE -----------------------------------------------------

function base64url(bytes: Uint8Array): string {
	return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** A fresh verifier + its S256 challenge (RFC 7636). */
export async function pkcePair(): Promise<{ verifier: string; challenge: string }> {
	const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
	const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
	return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

/** The consent page: the visitor comes back to `callbackUrl?code=…`. */
export function authorizeUrl(callbackUrl: string, challenge: string, state: string): string {
	const u = new URL('https://openrouter.ai/auth');
	u.searchParams.set('callback_url', callbackUrl);
	u.searchParams.set('code_challenge', challenge);
	u.searchParams.set('code_challenge_method', 'S256');
	u.searchParams.set('key_label', `${app().name} demo`);
	u.searchParams.set('state', state);
	return u.toString();
}

/** Trade the one-time code for the visitor's own key. */
export async function exchangeCode(code: string, verifier: string): Promise<string> {
	let res: Response;
	try {
		res = await fetch(`${OPENROUTER_API}/auth/keys`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ code, code_verifier: verifier, code_challenge_method: 'S256' })
		});
	} catch {
		throw new ApiError(explain(0, null, 'OpenRouter'), 0);
	}
	if (!res.ok) throw new ApiError(`OpenRouter did not accept the sign-in (${res.status}). Try connecting again.`, res.status);
	const body = await res.json();
	if (typeof body?.key !== 'string' || !body.key) throw new ApiError('OpenRouter returned no key. Try connecting again.', 502);
	return body.key;
}

export const OPENROUTER_API = 'https://openrouter.ai/api/v1';

/** openrouter.ai/keys/<sha256 of the key>: the key's own settings page. */
export async function keySettingsUrl(key: string): Promise<string> {
	const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
	const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
	return `https://openrouter.ai/keys/${hex}`;
}
