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

afterEach(cleanup);

describe('configure', () => {
	test('app() before configure says what is missing', async () => {
		vi.resetModules();
		const fresh = await import('./app');
		expect(() => fresh.app()).toThrow(/configure\(\) has not been called/);
		expect(fresh.loaderKind()).toBe('twinkle');
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
