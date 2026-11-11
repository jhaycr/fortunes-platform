// The app these web pages belong to, handed in once at startup.
//
// The shared components are the same in every Little Fortunes app but for a
// few names and words: the app's name, its backend handle, and a phrase or
// two about its subject. The app hands those in with `configure`, from a
// module its root layout imports before anything renders:
//
//     // src/lib/brand.ts
//     import { configure } from 'fortunes-platform/app';
//     import { api } from '$lib/api';
//
//     configure({
//         id: 'iching',
//         name: 'Little Fortunes · I Ching',
//         short: 'I Ching',
//         api,
//         words: {
//             withoutAi: 'Casting, the hexagrams with their texts, and the journal',
//             practice: 'casting'
//         }
//     });
//
// Components read it with `app()` when they render, never at import time, so
// the order modules load in does not matter.

import type { PlatformApi } from './types';

/** The audio button's loading animation; all live in AudioLoader.svelte.
 *   twinkle  three small stars brighten in turn
 *   breath   a single star slowly brightens and dims
 *   card     a tiny card turns over and back */
export type LoaderKind = 'twinkle' | 'breath' | 'card';

export interface AppConfig {
	/** Lower-case identifier: names files and the setup log line. */
	id: string;
	/** Prefix of the app's browser storage (its key, its demo's journal);
	 * `id` if not given. Kept as it was first shipped, so visitors keep what
	 * they saved ("tarotarium" for Tarotarium). */
	storage?: string;
	/** The display name, as the header shows it ("Little Fortunes · I Ching"). */
	name: string;
	/** The name in a sentence ("I Ching", "Tarotarium"). */
	short: string;
	/** The app's backend handle (HTTP or browser-local). */
	api: PlatformApi;
	/** The audio button's loading animation (default twinkle). */
	loader?: LoaderKind;
	words: {
		/** What works with no AI set up, as the subject of "... all work with
		 * nothing configured here" ("Casting, the hexagrams with their texts,
		 * and the journal"). */
		withoutAi: string;
		/** The app's own act, as in "casting and the journal keep working". */
		practice: string;
		/** What a person owns beyond readings and settings, for the admin's
		 * reassign and delete warnings ("drafts"). */
		ownedExtras?: string;
		/** A sentence added to the delete warning, after "This cannot be
		 * undone" is not yet said ("anything they published stays as former
		 * member"). */
		deleteNote?: string;
	};
}

/** The suite's own page: the first half of every app's name links there. */
export const SUITE_URL = 'https://littlefortunes.app';

let current: AppConfig | null = null;

/** Hand in the app. Called once, before the first page renders; a second
 * call replaces the first (tests may configure a stand-in). */
export function configure(config: AppConfig): void {
	current = config;
}

/** The loading animation the app chose; twinkle if none (or not yet configured). */
export function loaderKind(): LoaderKind {
	return current?.loader ?? 'twinkle';
}

/** The prefix of the app's browser storage. */
export function storagePrefix(): string {
	const a = app();
	return a.storage ?? a.id;
}

/** The app, as handed in. Throws if `configure` has not run. */
export function app(): AppConfig {
	if (!current) {
		throw new Error(
			"fortunes-platform: configure() has not been called. The app's root layout must import the module that hands the app in."
		);
	}
	return current;
}
