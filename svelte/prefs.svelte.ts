// Saved preferences, server-backed so they follow the account across
// devices. localStorage is a device cache: the UI paints from it instantly
// and it carries offline/PWA use; the server profile wins at load time.
// Writes update state + cache immediately and debounce one PUT per burst.
//
// An app declares its preferences with `persisted` and `persistedList` (and
// `onSync` for any of its own shape), and calls `syncPrefs` once with the
// settings that ride /api/me (the shell's `onme`).
import { app } from './app';
import type { UserSettingsBase } from './types';

let pending: Record<string, unknown> = {};
let timer: ReturnType<typeof setTimeout> | null = null;

const isObject = (v: unknown): v is Record<string, unknown> =>
	typeof v === 'object' && v !== null && !Array.isArray(v);

/** Queue one setting for the next save. An object value merges with one
 * already queued under its key (per-item choices saved in a burst). */
export function queuePref(key: string, value: unknown): void {
	pending[key] = isObject(value) && isObject(pending[key]) ? { ...pending[key], ...value } : value;
	if (timer) clearTimeout(timer);
	timer = setTimeout(flush, 500);
}

async function flush() {
	timer = null;
	const batch = pending;
	pending = {};
	try {
		await app().api.setMySettings(batch);
	} catch {
		// Offline or signed-out: the localStorage cache still holds the value;
		// the next successful sync reconciles.
	}
}

// Whether the account's prefs have arrived this visit (syncPrefs ran). Until
// they have, a pref on a new device holds only its built-in default, so a
// page that finds no usable value may SHOW a fallback but must not save it:
// the save would replace the choice the server is about to deliver.
let loaded = $state(false);
export function prefsLoaded(): boolean {
	return loaded;
}

type Settings = UserSettingsBase & Record<string, unknown>;

// Applied when the server profile arrives; collected for first-login import.
const applyFns: ((s: Settings) => void)[] = [];
const collectFns: ((out: Record<string, unknown>) => void)[] = [];

/** A preference of the app's own shape: `apply` takes the server's profile
 * when it arrives, `collect` adds this device's value for the first-login
 * import. */
export function onSync(apply: (s: Settings) => void, collect: (out: Record<string, unknown>) => void): void {
	applyFns.push(apply);
	collectFns.push(collect);
}

/** A string preference: `serverKey` in the profile, `lsKey` on the device. */
export function persisted(lsKey: string, serverKey: string, fallback: string): { value: string } {
	let value = $state(localStorage.getItem(lsKey) ?? fallback);
	onSync(
		(s) => {
			const sv = s[serverKey];
			if (typeof sv === 'string' && sv !== value) {
				value = sv;
				localStorage.setItem(lsKey, sv);
			}
		},
		(out) => (out[serverKey] = value)
	);
	return {
		get value() {
			return value;
		},
		set value(v: string) {
			value = v;
			localStorage.setItem(lsKey, v);
			queuePref(serverKey, v);
		}
	};
}

/** A list preference, kept the same way. */
export function persistedList(lsKey: string, serverKey: string): { value: string[] } {
	let value = $state<string[]>(JSON.parse(localStorage.getItem(lsKey) ?? '[]'));
	onSync(
		(s) => {
			const sv = s[serverKey];
			if (Array.isArray(sv)) {
				value = sv as string[];
				localStorage.setItem(lsKey, JSON.stringify(sv));
			}
		},
		(out) => (out[serverKey] = value)
	);
	return {
		get value() {
			return value;
		},
		set value(v: string[]) {
			value = v;
			localStorage.setItem(lsKey, JSON.stringify(v));
			queuePref(serverKey, v);
		}
	};
}

/**
 * Reconcile with the account profile (called once per app load, with the
 * settings that ride /api/me). Server wins for every key it has; a user
 * whose profile has never been written gets their current device prefs
 * imported once, so nobody loses their setup crossing to server-side prefs.
 */
export function syncPrefs(s: UserSettingsBase): void {
	if (!s.has_profile) {
		const out: Record<string, unknown> = {};
		for (const fn of collectFns) fn(out);
		app().api.setMySettings(out).catch(() => {});
		loaded = true;
		return;
	}
	for (const fn of applyFns) fn(s as Settings);
	loaded = true;
}
