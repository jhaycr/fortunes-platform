import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './types';
import { useTestApp } from './testapp';
import { authHeaders, chatBody, explain, keyInfo, llmComplete, llmStream, parseSse, type LlmConfig } from './llm';

beforeEach(() => useTestApp());

const cfg: LlmConfig = { baseUrl: 'https://example.test/v1', model: 'm', key: 'sk-or-test', maxTokens: 900 };

const sse = (lines: string[]) => new Response(lines.join('\n') + '\n').body!;
const collect = async (gen: AsyncGenerator<string>) => {
	const out: string[] = [];
	for await (const t of gen) out.push(t);
	return out;
};

afterEach(() => vi.unstubAllGlobals());

describe('chatBody', () => {
	it('matches interpret._chat_body: model, two messages, max_tokens, stream only when streaming', () => {
		expect(chatBody('S', 'U', 'm', 42, false)).toEqual({ model: 'm', messages: [{ role: 'system', content: 'S' }, { role: 'user', content: 'U' }], max_tokens: 42 });
		expect(chatBody('S', 'U', 'm', 42, true).stream).toBe(true);
		expect(authHeaders('k').Authorization).toBe('Bearer k');
	});
});

describe('parseSse', () => {
	it('yields deltas, skips keepalives and junk, stops at [DONE]', async () => {
		const got = await collect(
			parseSse(
				sse([
					': OPENROUTER PROCESSING',
					'',
					'data: {"choices":[{"delta":{"content":"Hel"}}]}',
					'data: not json at all',
					'event: ping',
					'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":null}]}',
					'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":1}}',
					'data: [DONE]',
					'data: {"choices":[{"delta":{"content":"never"}}]}'
				])
			)
		);
		expect(got).toEqual(['Hel', 'lo']);
	});

	it('handles frames split across reads', async () => {
		const parts = ['data: {"choices":[{"del', 'ta":{"content":"ab"}}]}\ndata: {"choi', 'ces":[{"delta":{"content":"c"}}]}\n'];
		const body = new ReadableStream<Uint8Array<ArrayBuffer>>({
			start(c) {
				for (const p of parts) c.enqueue(new TextEncoder().encode(p));
				c.close();
			}
		});
		expect(await collect(parseSse(body))).toEqual(['ab', 'c']);
	});

	it('turns a mid-stream error chunk into a plain-language ApiError', async () => {
		const gen = parseSse(sse(['data: {"choices":[{"delta":{"content":"x"}}]}', 'data: {"error":{"code":429,"message":"slow down"}}']));
		const out: string[] = [];
		await expect(async () => {
			for await (const t of gen) out.push(t);
		}).rejects.toMatchObject({ status: 429, message: expect.stringMatching(/rate-limiting/) });
		expect(out).toEqual(['x']);
	});
});

describe('explain', () => {
	it('maps the statuses a querent can act on', () => {
		expect(explain(401)).toMatch(/refused/);
		expect(explain(402)).toMatch(/credit/);
		expect(explain(429)).toMatch(/rate-limiting/);
		expect(explain(0)).toMatch(/connection/);
		expect(explain(404, 'No endpoints found for x')).toMatch(/No endpoints found/);
		expect(explain(503, 'overloaded')).toBe('The AI provider error (503): overloaded');
	});
});

describe('llmStream / llmComplete', () => {
	it('fails before the first token with the provider message when the handshake is refused', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { message: 'User not found.', code: 401 } }), { status: 401 })));
		await expect(collect(llmStream(cfg, 'S', 'U'))).rejects.toMatchObject({ status: 401, message: expect.stringMatching(/refused/) });
	});

	it('streams the body and sends the right request', async () => {
		const fetchMock = vi.fn(async () => new Response('data: {"choices":[{"delta":{"content":"hi"}}]}\ndata: [DONE]\n', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);
		expect(await collect(llmStream(cfg, 'S', 'U', undefined, 77))).toEqual(['hi']);
		const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('https://example.test/v1/chat/completions');
		expect(JSON.parse(init.body as string)).toMatchObject({ model: 'm', max_tokens: 77, stream: true });
		expect((init.headers as Record<string, string>).Authorization).toBe('Bearer sk-or-test');
	});

	it('a network failure is a connection message, an abort stays an AbortError', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
		await expect(collect(llmStream(cfg, 'S', 'U'))).rejects.toMatchObject({ status: 0, message: expect.stringMatching(/connection/) });
		vi.stubGlobal('fetch', vi.fn(async () => { throw new DOMException('aborted', 'AbortError'); }));
		await expect(collect(llmStream(cfg, 'S', 'U'))).rejects.toMatchObject({ name: 'AbortError' });
	});

	it('llmComplete returns the trimmed message text', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ choices: [{ message: { content: '  A reading.  ' } }] }))));
		expect(await llmComplete(cfg, 'S', 'U')).toBe('A reading.');
	});
});

describe('keyInfo', () => {
	it('reads the data envelope and tolerates missing fields', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: { label: 'demo', limit: 5, limit_remaining: 4.5, free_model_daily_requests: { limit: 50, used: 2, remaining: 48 } } }))));
		expect(await keyInfo(cfg)).toMatchObject({ label: 'demo', limit_remaining: 4.5, usage: null, free_model_daily_requests: { remaining: 48 } });
		vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
		await expect(keyInfo(cfg)).rejects.toBeInstanceOf(ApiError);
	});
});
