// Where the visitor's own LLM key lives: this browser, nowhere else. The
// default is sessionStorage — gone when the tab closes, the right default
// for a public demo that may be opened on a shared device — with an opt-in
// "remember on this device" that moves it to localStorage. The model and
// base URL travel with the key. Every read and write is guarded: storage can
// be absent or throwing (private windows, blocked site data), and the app
// must still render. The names are under the app's storage prefix
// (`<prefix>.llm.key`), so each app keeps its own visitors' keys.
import { storagePrefix } from './app';

const KEY = () => `${storagePrefix()}.llm.key`;
const MODEL = () => `${storagePrefix()}.llm.model`;
const BASE_URL = () => `${storagePrefix()}.llm.base_url`;

export const OPENROUTER = 'https://openrouter.ai/api/v1';
/** A free model to start on; the key card offers the live list. Free ids
 * churn, so a 404 from the provider is reported plainly, not hidden. */
export const DEFAULT_MODEL = 'google/gemma-4-31b-it:free';
/** The balanced pick: the model the author's own instance reads with
 * by default. Paid, about a tenth of a cent a reading, so it
 * is offered first but never the default — the default costs nothing. */
export const RECOMMENDED_MODEL = 'minimax/minimax-01';

function stores(): Storage[] {
	const out: Storage[] = [];
	try {
		if (typeof sessionStorage !== 'undefined') out.push(sessionStorage);
	} catch {
		/* blocked */
	}
	try {
		if (typeof localStorage !== 'undefined') out.push(localStorage);
	} catch {
		/* blocked */
	}
	return out;
}

function read(name: string): string {
	for (const s of stores()) {
		try {
			const v = s.getItem(name);
			if (v) return v;
		} catch {
			/* blocked */
		}
	}
	return '';
}

function write(name: string, value: string, remember: boolean): void {
	for (const s of stores()) {
		try {
			const here = remember ? s === localStorage : s === sessionStorage;
			if (here && value) s.setItem(name, value);
			else s.removeItem(name);
		} catch {
			/* blocked */
		}
	}
}

export interface LlmPrefs {
	key: string;
	/** True when the key is kept in localStorage rather than for the session. */
	remembered: boolean;
	model: string;
	baseUrl: string;
}

export function getKey(): string {
	return read(KEY());
}

export function isRemembered(): boolean {
	try {
		return typeof localStorage !== 'undefined' && !!localStorage.getItem(KEY());
	} catch {
		return false;
	}
}

export function getLlmPrefs(): LlmPrefs {
	return {
		key: getKey(),
		remembered: isRemembered(),
		model: read(MODEL()) || DEFAULT_MODEL,
		baseUrl: read(BASE_URL()) || OPENROUTER
	};
}

/** Store the key (and keep model/base URL in the same place as the key). */
export function setKey(key: string, remember: boolean): void {
	const prefs = getLlmPrefs();
	write(KEY(), key.trim(), remember);
	write(MODEL(), prefs.model, remember);
	write(BASE_URL(), prefs.baseUrl, remember);
}

export function setModel(model: string): void {
	write(MODEL(), model.trim(), isRemembered());
}

export function setBaseUrl(url: string): void {
	write(BASE_URL(), url.trim().replace(/\/+$/, ''), isRemembered());
}

/** Remove the key and its companions from both storages. */
export function forgetKey(): void {
	for (const s of stores()) {
		try {
			s.removeItem(KEY());
			s.removeItem(MODEL());
			s.removeItem(BASE_URL());
		} catch {
			/* blocked */
		}
	}
}

/** The key, masked for display: first 7 and last 4 characters. */
export function maskKey(key: string): string {
	if (key.length <= 14) return '•'.repeat(key.length);
	return `${key.slice(0, 7)}…${key.slice(-4)}`;
}
