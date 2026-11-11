// The HTTP side of an app's backend handle, for its server build: the
// request helpers, and the methods every Little Fortunes app's API has. An
// app's own adapter (src/lib/backend/http.ts) spreads these into its `api`
// beside its own, with its own types for a reading, its settings and its
// account page:
//
//     export const api = {
//         ...commonHttp<SavedReading, UserSettings, Account>(),
//         capabilities,
//         hexagrams: () => get<Hexagram[]>('/api/hexagrams'),
//         ...
//     };
import { app } from './app';
import {
	ApiError,
	type AccountBase,
	type AdminUser,
	type DesignedVoice,
	type LimitsSettings,
	type LlmSettings,
	type Me,
	type Person,
	type Persona,
	type ProviderVoice,
	type ReassignReport,
	type TtsSettings,
	type UsageSummary,
	type UserSettingsBase,
	type Visibility,
	type VoiceValues
} from './types';

export type StreamEvent =
	| { type: 'token'; data: { text: string } }
	| { type: 'done'; data: { persona: string } }
	| { type: 'error'; data: { message: string } };

/** Endpoints that render their own signed-out state instead of bouncing to
 * the login: the shell (/api/me) and the first-admin page, which must be able
 * to say "sign in first" on an instance where OIDC isn't configured yet. */
const NO_LOGIN_BOUNCE = new Set(['/api/me', '/api/setup']);

/** Signed out (OIDC mode): bounce through the app's login. */
export function redirectToLogin(): void {
	window.location.href =
		'/auth/login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
}

export async function errorFrom(res: Response, fallback: string): Promise<Error> {
	try {
		const detail = (await res.json())?.detail;
		if (typeof detail === 'string' && detail) return new ApiError(detail, res.status);
	} catch {
		// non-JSON body — fall through
	}
	return new ApiError(fallback, res.status);
}

export async function get<T>(url: string): Promise<T> {
	const res = await fetch(url);
	if (res.status === 401 && !NO_LOGIN_BOUNCE.has(url)) redirectToLogin();
	if (!res.ok) throw await errorFrom(res, `${url}: ${res.status}`);
	return res.json();
}

export async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
	const res = await fetch(url, {
		method,
		headers: { 'content-type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (res.status === 401) redirectToLogin();
	if (!res.ok) throw await errorFrom(res, `${method} ${url}: ${res.status}`);
	return res.json();
}

/** POST an SSE stream and yield its parsed events. Used for guided readings —
 * fetch()+ReadableStream rather than EventSource because we must POST a body.
 * abort the signal to stop the server-side LLM call. */
export async function* streamSSE(
	url: string,
	body: unknown,
	signal: AbortSignal
): AsyncGenerator<StreamEvent> {
	const res = await fetch(url, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body),
		signal
	});
	if (!res.ok) throw await errorFrom(res, `${url}: ${res.status}`);
	if (!res.body) throw new Error(`${url}: empty response`);
	const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
	let buf = '';
	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		buf += value;
		let i: number;
		while ((i = buf.indexOf('\n\n')) !== -1) {
			const frame = buf.slice(0, i);
			buf = buf.slice(i + 2);
			let ev = 'message';
			let data = '';
			for (const line of frame.split('\n')) {
				if (line.startsWith('event:')) ev = line.slice(6).trim();
				else if (line.startsWith('data:')) data += line.slice(5).trim();
			}
			if (data) yield { type: ev, data: JSON.parse(data) } as StreamEvent;
		}
	}
}


/** The methods every app's server API has, typed by the app's own reading
 * (`R`), settings (`S`) and account page (`A`). */
export function commonHttp<R, S extends UserSettingsBase, A extends AccountBase>() {
	return {
		me: () => get<Me<S>>('/api/me'),
		getLlmSettings: () => get<LlmSettings>('/api/settings/llm'),
		setLlmSettings: (s: { base_url?: string; model?: string; api_key?: string }) =>
			send<LlmSettings>('PUT', '/api/settings/llm', s),
		personas: () => get<{ personas: Persona[]; default: string }>('/api/personas'),
		getMySettings: () => get<S>('/api/settings/me'),
		getLimitsSettings: () => get<LimitsSettings>('/api/settings/limits'),
		setLimitsSettings: (s: {
			readings_per_day?: number;
			llm_tokens_per_day?: number;
			tts_minutes_per_day?: number;
		}) => send<LimitsSettings>('PUT', '/api/settings/limits', s),
		setMySettings: (s: Partial<S>) => send<S>('PUT', '/api/settings/me', s),
		patchMe: (p: { display_name?: string }) =>
			send<{ display_name: string }>('PATCH', '/api/me', p),
		adminUsers: () => get<AdminUser[]>('/api/admin/users'),
		adminUserUpdate: (u: string, p: { display_name?: string; active?: boolean; is_admin?: boolean }) =>
			send<AdminUser>('PATCH', `/api/admin/users/${encodeURIComponent(u)}`, p),
		adminReassign: (u: string, to: string) =>
			send<ReassignReport>('POST', `/api/admin/users/${encodeURIComponent(u)}/reassign`, { to }),
		adminUserDelete: (u: string) =>
			send<Record<string, unknown>>('DELETE', `/api/admin/users/${encodeURIComponent(u)}`),
		deleteMe: (confirm: string) =>
			send<Record<string, unknown>>('POST', '/api/me/delete', { confirm }),
		setupState: () =>
			get<{ required: boolean; user: string; is_admin: boolean }>('/api/setup'),
		claimAdmin: (token: string) =>
			send<{ user: string; is_admin: boolean }>('POST', '/api/setup', { token }),
		adminUsage: (days: number) => get<UsageSummary>(`/api/admin/usage?days=${days}`),
		getTtsSettings: () => get<TtsSettings>('/api/settings/tts'),
		setTtsSettings: (s: {
			provider?: string;
			base_url?: string;
			model?: string;
			api_key?: string;
			voices?: Record<string, VoiceValues>;
		}) => send<TtsSettings>('PUT', '/api/settings/tts', s),
		/** The active provider's selectable voices; 501 when it can't list them. */
		listProviderVoices: () => get<ProviderVoice[]>('/api/settings/tts/voices'),
		designVoice: (description: string, preview_text?: string) =>
			send<DesignedVoice[]>('POST', '/api/settings/tts/design', {
				description,
				preview_text: preview_text || null
			}),
		keepDesignedVoice: (v: {
			generated_voice_id: string;
			name: string;
			description: string;
			rejected?: string[];
		}) => send<{ voice_id: string }>('POST', '/api/settings/tts/design/keep', v),
		/** Spoken audio for a persisted interpretation piece (-1 = the whole reading).
		 * Optional persona overrides the voice (text stays as written). */
		readingAudio: (id: number, position: number, persona?: string | null): string | (() => Promise<string>) =>
			`/api/readings/${id}/audio/${position}` +
			(persona ? `?persona=${encodeURIComponent(persona)}` : ''),
		/** Whether this reader has a voice. The server voices every persona (and
		 * reports a gap itself), so the container build always says yes. */
		speaks: (_persona?: string | null): boolean => true,
		/** Audio for ephemeral text (the quick reading). Returns an object URL. */
		speak: async (text: string, persona?: string | null): Promise<string> => {
			const res = await fetch('/api/tts', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ text, persona: persona || null })
			});
			if (!res.ok) {
				const body = await res.json().catch(() => null);
				const detail = typeof body?.detail === 'string' && body.detail ? body.detail : `The voice service answered ${res.status}.`;
				throw new ApiError(detail, res.status);
			}
			return URL.createObjectURL(await res.blob());
		},
		/** `limit` fetches only the newest few (home page's recent strip); the
		 * journal asks for everything. */
		readings: (limit?: number) =>
			get<R[]>(limit ? `/api/readings?limit=${limit}` : '/api/readings'),
		reading: (id: number) => get<R>(`/api/readings/${id}`),
		streamComprehensive: (id: number, persona: string | null, signal: AbortSignal, books: string[] = []) =>
			streamSSE(`/api/readings/${id}/interpret/comprehensive`, { persona, books }, signal),
		/** `books`: library texts a reading added after the save drew on (recorded, not replaced) */
		updateReading: (id: number, patch: { notes?: string; books?: string[] }) =>
			send<R>('PATCH', `/api/readings/${id}`, patch),
		users: () => get<Person[]>('/api/users'),
		account: () => get<A>('/api/account'),
		/** Everything the account owns, as a file to save. */
		exportMyData: async (): Promise<{ blob: Blob; filename: string }> => {
			const res = await fetch('/api/me/export');
			if (!res.ok) throw await errorFrom(res, `export: ${res.status}`);
			const name = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') ?? '')?.[1];
			return { blob: await res.blob(), filename: name ?? `${app().id}-export.zip` };
		},
		setSharing: (id: number, visibility: Visibility, grantees: string[] = []) =>
			send<R>('PUT', `/api/readings/${id}/sharing`, { visibility, grantees }),
	};
}
