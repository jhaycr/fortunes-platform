// The shared components say the app's own name and words, and call its api.
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { app, configure, loaderKind } from './app';
import { calls, useTestApp } from './testapp';
import AiPanel from './AiPanel.svelte';
import AudioLoader from './AudioLoader.svelte';
import LimitsCard from './LimitsCard.svelte';
import PeoplePanel from './PeoplePanel.svelte';
import SignInSetup from './SignInSetup.svelte';
import VisibilitySelect from './VisibilitySelect.svelte';
import Shell from './Shell.svelte';
import { createRawSnippet } from 'svelte';
import { api as testApi } from './testapp';
import { persisted, persistedList, prefsLoaded, queuePref, syncPrefs } from './prefs.svelte';

afterEach(cleanup);

describe('configure', () => {
	test('app() before configure says what is missing', () => {
		configure(null as never);
		expect(() => app()).toThrow(/configure\(\) has not been called/);
		expect(loaderKind()).toBe('twinkle');
	});

	test('app() returns what was handed in, and a second call replaces it', () => {
		useTestApp();
		expect(app().short).toBe('Test App');
		configure({ ...app(), short: 'Other', loader: 'breath' });
		expect(app().short).toBe('Other');
		expect(loaderKind()).toBe('breath');
	});
});

test('the loader draws the kind the app chose', () => {
	useTestApp({ loader: 'card' });
	const { container } = render(AudioLoader);
	expect(container.querySelector('.card')).not.toBeNull();
	cleanup();
	useTestApp();
	expect(render(AudioLoader).container.querySelector('.twinkle')).not.toBeNull();
});

test('the AI panel says what works without AI, in the app own words', async () => {
	useTestApp();
	render(AiPanel);
	expect(await screen.findByText(/Shuffling and the journal all work with nothing configured here/)).toBeTruthy();
	expect(screen.getByText(/Test App gains two extra things/)).toBeTruthy();
});

test('the limits card names the app own act', () => {
	useTestApp();
	render(LimitsCard, { onstatus: () => {} });
	expect(screen.getByText(/shuffling and the journal keep working/)).toBeTruthy();
});

test('the setup page names the log line by the app id', async () => {
	useTestApp();
	render(SignInSetup);
	expect(await screen.findByText('TESTAPP SETUP:')).toBeTruthy();
	expect(screen.getByText('docker logs testapp')).toBeTruthy();
});

describe('people', () => {
	test('the warnings list what a person owns, with the app extras', async () => {
		useTestApp({ words: { withoutAi: 'x', practice: 'x', ownedExtras: 'drafts', deleteNote: 'what they published stays' } });
		const asked: string[] = [];
		vi.stubGlobal('confirm', (m: string) => (asked.push(m), false));
		render(PeoplePanel);
		const del = await screen.findAllByRole('button', { name: /delete/i });
		await fireEvent.click(del[del.length - 1]);
		expect(asked[0]).toMatch(/Their readings, shares, settings and drafts are erased; what they published stays\. This cannot be undone\./);
		vi.unstubAllGlobals();
	});

	test('without extras the list still reads', async () => {
		useTestApp();
		const asked: string[] = [];
		vi.stubGlobal('confirm', (m: string) => (asked.push(m), false));
		render(PeoplePanel);
		const del = await screen.findAllByRole('button', { name: /delete/i });
		await fireEvent.click(del[del.length - 1]);
		expect(asked[0]).toMatch(/Their readings, shares and settings are erased\. This cannot be undone\./);
		vi.unstubAllGlobals();
	});
});

test('sharing saves through the app api and names the app when nobody else is there', async () => {
	useTestApp();
	const reading = { id: 1, owner: 'sam', visibility: 'private' as const, shared_with: [] as string[], extra: 'kept' };
	render(VisibilitySelect, { reading });
	await fireEvent.click(screen.getByRole('button'));
	expect(calls.some((c) => c.method === 'users')).toBe(true);
	await fireEvent.click(screen.getByLabelText('Everyone'));
	await fireEvent.click(screen.getByRole('button', { name: /save/i }));
	await vi.waitFor(() => expect(calls.find((c) => c.method === 'setSharing')?.args).toEqual([1, 'everyone', []]));
});

describe('the shell', () => {
	const links = [
		{ href: '/', label: 'Shuffle' },
		{ href: '/journal', label: 'Journal' }
	];

	test('the suite half of the name goes to the suite, the app half home', async () => {
		useTestApp();
		const mark = createRawSnippet(() => ({ render: () => '<i>✦</i>' }));
		const children = createRawSnippet(() => ({ render: () => '<p>page</p>' }));
		const seen: string[] = [];
		render(Shell, { links, path: '/journal/3', favicon: '', mark, children, onme: (m) => seen.push(m.user) });
		expect(screen.getByRole('link', { name: /Little Fortunes/ }).getAttribute('href')).toBe('https://littlefortunes.app');
		expect(screen.getByRole('link', { name: 'Test App' }).getAttribute('href')).toBe('/');
		expect(screen.getByRole('link', { name: 'Journal' }).classList.contains('active')).toBe(true);
		expect(screen.getByRole('link', { name: 'Shuffle' }).classList.contains('active')).toBe(false);
		await vi.waitFor(() => expect(seen).toEqual(['sam']));
		expect(await screen.findByText('v1.2.3')).toBeTruthy();
		expect(screen.getByText('page')).toBeTruthy();
	});

	test('the public demo says where readings are kept', async () => {
		useTestApp({ api: { ...testApi, capabilities: { ...testApi.capabilities, localData: true } } });
		const snip = createRawSnippet(() => ({ render: () => '<span></span>' }));
		render(Shell, { links, path: '/', favicon: '', mark: snip, children: snip });
		expect(screen.getByText(/Your readings stay in this browser/)).toBeTruthy();
	});

	test('an app that keeps more in the demo says so in its notice', async () => {
		useTestApp({ api: { ...testApi, capabilities: { ...testApi.capabilities, localData: true } } });
		const snip = createRawSnippet(() => ({ render: () => '<span></span>' }));
		render(Shell, { links, path: '/', favicon: '', mark: snip, children: snip, demoNote: 'Public demo. Your readings and decks stay in this browser.' });
		expect(screen.getByText('Public demo. Your readings and decks stay in this browser.')).toBeTruthy();
		expect(screen.queryByText('Public demo. Your readings stay in this browser.')).toBeNull();
	});

	test('a server shows no demo notice', async () => {
		useTestApp();
		const snip = createRawSnippet(() => ({ render: () => '<span></span>' }));
		render(Shell, { links, path: '/', favicon: '', mark: snip, children: snip, demoNote: 'Public demo. Kept here.' });
		expect(screen.queryByText(/Public demo/)).toBeNull();
	});
});

describe('saved preferences', () => {
	test('the server wins, a burst of changes is one save, and a new account imports this device', async () => {
		useTestApp();
		localStorage.setItem('testapp.reader', 'imp');
		const reader = persisted('testapp.reader', 'persona', 'sage');
		const favs = persistedList('testapp.favs', 'favs');
		expect(reader.value).toBe('imp');
		expect(prefsLoaded()).toBe(false);

		syncPrefs({ auto_read_audio: false, persona: 'sage', has_profile: true, favs: ['a'] } as never);
		expect(reader.value).toBe('sage');
		expect(favs.value).toEqual(['a']);
		expect(localStorage.getItem('testapp.reader')).toBe('sage');
		expect(prefsLoaded()).toBe(true);

		vi.useFakeTimers();
		reader.value = 'imp';
		favs.value = ['a', 'b'];
		queuePref('extras', { one: true });
		queuePref('extras', { two: false });
		await vi.advanceTimersByTimeAsync(600);
		vi.useRealTimers();
		const saves = calls.filter((c) => c.method === 'setMySettings');
		expect(saves).toHaveLength(1);
		expect(saves[0].args[0]).toEqual({ persona: 'imp', favs: ['a', 'b'], extras: { one: true, two: false } });

		calls.length = 0;
		syncPrefs({ auto_read_audio: false, persona: null, has_profile: false });
		expect(calls.find((c) => c.method === 'setMySettings')?.args[0]).toMatchObject({ persona: 'imp', favs: ['a', 'b'] });
	});
});

test('the account page names shares the app way and carries its own section', async () => {
	useTestApp();
	const { default: AccountPage } = await import('./AccountPage.svelte');
	const own = createRawSnippet(() => ({ render: () => '<section><h2>Decks you published</h2></section>' }));
	render(AccountPage, {
		title: (s: { question: string | null; spread?: string }) => s.question ?? `A ${s.spread} spread`,
		own,
		erases: 'Erases it all.'
	});
	expect(await screen.findByText('A three spread')).toBeTruthy();
	expect(screen.getByText('Why?')).toBeTruthy();
	expect(screen.getByText(/from Robin/)).toBeTruthy();
	expect(screen.getByText('Decks you published')).toBeTruthy();
	expect(screen.getByText(/Erases it all\. Signing in again/)).toBeTruthy();
});

test('the demo account page names what this browser keeps and what deleting erases', async () => {
	useTestApp({ api: { ...testApi, capabilities: { ...testApi.capabilities, localData: true } } });
	const { default: AccountPage } = await import('./AccountPage.svelte');
	render(AccountPage, { title: (s: { question: string | null }) => s.question ?? 'A reading' });
	expect(await screen.findByText(/Erases your readings, settings and any AI key from this browser\./)).toBeTruthy();
	expect(screen.getByText(/with interpretations, and your settings\./)).toBeTruthy();
	cleanup();

	render(AccountPage, {
		title: (s: { question: string | null }) => s.question ?? 'A reading',
		localErases: 'your readings, decks, settings and any AI key',
		localExportHas: 'and your settings. Each deck of your own exports from its page.'
	});
	expect(await screen.findByText(/Erases your readings, decks, settings and any AI key from this browser\./)).toBeTruthy();
	expect(screen.getByText(/and your settings\. Each deck of your own exports from its page\./)).toBeTruthy();
});
