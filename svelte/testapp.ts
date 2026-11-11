// A stand-in app for the component tests: every PlatformApi method answers
// with a small fixed value, and the calls are recorded.
import { configure, type AppConfig } from './app';
import type { PlatformApi } from './types';

export const calls: { method: string; args: unknown[] }[] = [];

function stub<T>(method: string, value: T) {
	return (...args: unknown[]) => {
		calls.push({ method, args });
		return Promise.resolve(value);
	};
}

const managed = { managed: [], config_file: null, config_error: null };

export const me = {
	user: 'sam',
	display_name: 'Sam',
	interpretation: false,
	tts: false,
	settings: { auto_read_audio: false, persona: null, has_profile: true },
	limits: { enabled: false },
	is_admin: true,
	authenticated: true,
	logout_url: '/auth/logout',
	version: '1.2.3'
};

export const api: PlatformApi = {
	capabilities: { sharing: true, tts: true, localKey: false, localData: false },
	me: stub('me', me),
	account: stub('account', {
		user: 'sam',
		display_name: 'Sam',
		authenticated: true,
		is_admin: true,
		reading_count: 2,
		shares_granted: [{ id: 4, question: null, created_at: 0, visibility: 'specific' as const, shared_with: ['robin'], spread: 'three' }],
		shares_received: [{ id: 9, owner: 'robin', question: 'Why?', created_at: 0, granted_at: 0 }],
		names: { robin: 'Robin' }
	}),
	exportMyData: stub('exportMyData', { blob: new Blob(['x']), filename: 'testapp-export.zip' }),
	deleteMe: stub('deleteMe', {}),
	setMySettings: stub('setMySettings', {}),
	users: stub('users', [{ username: 'robin', display_name: 'Robin' }]),
	setSharing: stub('setSharing', { id: 1, owner: 'sam', visibility: 'everyone' as const, shared_with: [] }),
	patchMe: stub('patchMe', { display_name: 'Sam' }),
	setupState: stub('setupState', { required: true, user: 'sam', is_admin: false }),
	claimAdmin: stub('claimAdmin', { user: 'sam', is_admin: true }),
	getLlmSettings: stub('getLlmSettings', { base_url: '', model: '', api_key_set: false, from_env: false, ...managed }),
	setLlmSettings: stub('setLlmSettings', { base_url: '', model: '', api_key_set: false, from_env: false, ...managed }),
	getLimitsSettings: stub('getLimitsSettings', { readings_per_day: null, llm_tokens_per_day: null, tts_minutes_per_day: null, ...managed }),
	setLimitsSettings: stub('setLimitsSettings', { readings_per_day: null, llm_tokens_per_day: null, tts_minutes_per_day: null, ...managed }),
	adminUsers: stub('adminUsers', [
		{ username: 'sam', display_name: 'Sam', kind: 'person', active: true, is_admin: true, first_seen: 0, last_seen: 0 },
		{ username: 'robin', display_name: 'Robin', kind: 'person', active: true, is_admin: false, first_seen: 0, last_seen: 0 }
	]),
	adminUserUpdate: stub('adminUserUpdate', { username: 'robin', display_name: 'Robin', kind: 'person', active: true, is_admin: false, first_seen: 0, last_seen: 0 }),
	adminReassign: stub('adminReassign', { from: 'robin', to: 'sam', readings: 3, settings: 1, usage_rows: 2, deactivated: true }),
	adminUserDelete: stub('adminUserDelete', {}),
	adminUsage: stub('adminUsage', { days: 30, by_model: [], daily: [], by_user: [] }),
	getTtsSettings: stub('getTtsSettings', {
		provider: 'openai', providers: [], connections: {}, base_url: '', model: '', api_key_set: false,
		voices: {}, gaps: {}, descriptions: {}, defaults: {}, ...managed
	}),
	setTtsSettings: stub('setTtsSettings', {
		provider: 'openai', providers: [], connections: {}, base_url: '', model: '', api_key_set: false,
		voices: {}, gaps: {}, descriptions: {}, defaults: {}, ...managed
	}),
	listProviderVoices: stub('listProviderVoices', []),
	designVoice: stub('designVoice', []),
	keepDesignedVoice: stub('keepDesignedVoice', { voice_id: 'v1' })
};

export function useTestApp(overrides: Partial<AppConfig> = {}): void {
	calls.length = 0;
	configure({
		id: 'testapp',
		name: 'Little Fortunes · Test App',
		short: 'Test App',
		api,
		words: { withoutAi: 'Shuffling and the journal', practice: 'shuffling' },
		...overrides
	});
}
