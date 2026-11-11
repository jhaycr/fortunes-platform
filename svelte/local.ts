// The browser-local side of an app's backend handle, for its public demo:
// no server, the journal in IndexedDB (./localjournal.ts), the visitor's own
// AI key in this browser (./keystore.ts). These are the methods every Little
// Fortunes demo has; the app's adapter (src/lib/backend/static.svelte.ts)
// spreads them into its `api` beside its own, and hands in its journal store,
// the loader for its exported data (prompts and version), and how its
// settings are kept as flat strings.
//
// Methods a demo cannot offer throw ApiError 501 and are kept unmounted by
// the capability flags; reaching one is a bug the app's static tests should
// surface.
import { storagePrefix } from './app';
import { forgetKey, getKey } from './keystore';
import { OWNER } from './localjournal';
import { ApiError, type AccountBase, type Me, type Person, type Persona, type UserSettingsBase } from './types';

export { OWNER };

/** The prompts file's part every app's has. */
export interface PromptsBase {
	personas: { slug: string; name: string; description: string; prompt: string }[];
	default_persona: string;
	max_tokens: { default: number };
}

/** What these methods use of the app's journal store (a LocalJournal). */
export interface JournalStore {
	list(limit?: number): Promise<unknown[]>;
	get(id: number): Promise<unknown | null>;
	updateNotes(id: number, notes: string | null | undefined): Promise<unknown | null>;
	remove(id: number): Promise<boolean>;
	count(): Promise<number>;
	all(): Promise<unknown[]>;
	getSetting(key: string): Promise<string>;
	setSetting(key: string, value: string): Promise<void>;
	allSettings(): Promise<Record<string, string>>;
	clear(): Promise<void>;
}

export interface LocalOptions<S extends UserSettingsBase> {
	/** The app's journal store (made once, on first use). */
	journal: () => JournalStore;
	/** The app's exported data: at least its prompts and its version. */
	data: () => Promise<{ prompts: PromptsBase; version: string }>;
	/** Settings as the server keeps them: a flat {key: string} store. */
	settings: {
		read(stored: Record<string, string>): S;
		writes(patch: Partial<S>): [string, string][];
	};
}

/** A method the demo does not have. */
export function notAvailable(what: string) {
	return (..._args: unknown[]): never => {
		throw new ApiError(`${what} is not available in the static demo`, 501);
	};
}

/** The methods every app's demo has, typed by the app's own reading (`R`),
 * settings (`S`) and account page (`A`). */
export function commonLocal<R, S extends UserSettingsBase, A extends AccountBase>(o: LocalOptions<S>) {
	const db = o.journal;
	const mySettings = async (): Promise<S> => o.settings.read(await db().allSettings());
	const displayName = async (): Promise<string> => (await db().getSetting('display_name')) || 'You';

	return {
		me: async (): Promise<Me<S>> => {
			const [c, settings, name] = await Promise.all([o.data().catch(() => null), mySettings(), displayName()]);
			return {
				user: OWNER,
				display_name: name,
				interpretation: !!getKey(), // the visitor's own key switches the AI paths on
				tts: !!getKey(), // and speech: OpenRouter serves both on the same key
				settings,
				limits: { enabled: false },
				is_admin: false,
				authenticated: true,
				logout_url: null,
				version: c?.version ?? 'demo'
			};
		},

		personas: async (): Promise<{ personas: Persona[]; default: string }> => {
			const p = (await o.data()).prompts;
			return {
				personas: p.personas.map(({ slug, name, description }) => ({ slug, name, description })),
				default: p.default_persona
			};
		},
		users: async (): Promise<Person[]> => [],

		// journal
		readings: (limit?: number) => db().list(limit) as Promise<R[]>,
		reading: async (id: number): Promise<R> => {
			const r = await db().get(id);
			if (!r) throw new ApiError('reading not found', 404);
			return r as R;
		},
		// `books` belongs to a server's library: there is none here
		updateReading: async (id: number, patch: { notes?: string; books?: string[] }): Promise<R> => {
			const r = await db().updateNotes(id, patch.notes);
			if (!r) throw new ApiError('reading not found or not yours', 404);
			return r as R;
		},
		deleteReading: async (id: number) => {
			if (!(await db().remove(id))) throw new ApiError('reading not found or not yours', 404);
			return { deleted: id };
		},

		getMySettings: mySettings,
		setMySettings: async (patch: Partial<S>) => {
			for (const [k, v] of o.settings.writes(patch)) await db().setSetting(k, v);
			return mySettings();
		},
		patchMe: async (p: { display_name?: string }) => {
			if (p.display_name !== undefined) await db().setSetting('display_name', p.display_name.trim());
			return { display_name: await displayName() };
		},

		// account
		account: async (): Promise<A> =>
			({
				user: OWNER,
				display_name: await displayName(),
				authenticated: true,
				is_admin: false,
				reading_count: await db().count(),
				shares_granted: [],
				shares_received: []
			}) as unknown as A,
		exportMyData: async () => {
			const [readings, settings, c] = await Promise.all([db().all(), db().allSettings(), o.data().catch(() => null)]);
			const body = {
				readings,
				settings,
				account: {
					username: OWNER,
					display_name: settings.display_name || 'You',
					exported_at: Math.floor(Date.now() / 1000),
					app_version: c?.version ?? 'demo'
				}
			};
			return {
				blob: new Blob([JSON.stringify(body, null, 2)], { type: 'application/json' }),
				filename: `${storagePrefix()}-demo.json`
			};
		},
		deleteMe: async (confirm: string) => {
			if (confirm.trim() !== OWNER) throw new ApiError('type your username exactly to confirm', 400);
			await db().clear();
			forgetKey(); // a remembered key must not outlive "Delete everything" on a shared device
			return {};
		},
		setSharing: notAvailable('Sharing'),
		setupState: async () => ({ required: false, user: OWNER, is_admin: false }),
		claimAdmin: notAvailable('Admin setup'),

		// the instance's own settings: a server admin's, never mounted here
		getLlmSettings: notAvailable('Instance settings'),
		setLlmSettings: notAvailable('Instance settings'),
		getLimitsSettings: notAvailable('Instance settings'),
		setLimitsSettings: notAvailable('Instance settings'),
		adminUsers: notAvailable('Administration'),
		adminUserUpdate: notAvailable('Administration'),
		adminReassign: notAvailable('Administration'),
		adminUserDelete: notAvailable('Administration'),
		adminUsage: notAvailable('Administration'),
		getTtsSettings: notAvailable('Spoken readings'),
		setTtsSettings: notAvailable('Spoken readings'),
		listProviderVoices: notAvailable('Spoken readings'),
		designVoice: notAvailable('Spoken readings'),
		keepDesignedVoice: notAvailable('Spoken readings')
	};
}
