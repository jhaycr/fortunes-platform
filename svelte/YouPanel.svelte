<script lang="ts">
	// Your own settings: the name others see, how readings are heard, and the
	// way to the account page. An app adds preferences of its own (`own`) and
	// says in its own words what a piece read aloud is and what it remembers.
	import { untrack, type Snippet } from 'svelte';
	import { app } from './app';
	import type { Me } from './types';

	let {
		me,
		aloud = "Each piece's reading (and the whole reading's)",
		remembered = 'Your choice of reader is remembered automatically as you use it',
		accountHas = [],
		adminSets = 'AI and voices',
		own
	}: {
		me: Me;
		/** What starts speaking: "Each card's reading (and the whole picture)". */
		aloud?: string;
		/** What is remembered without a setting, as a sentence's start. */
		remembered?: string;
		/** What else the account page holds, beside shared readings ("decks you've published"). */
		accountHas?: string[];
		/** What an admin sets for the whole instance. */
		adminSets?: string;
		/** The app's own preferences, first under "Reading experience". */
		own?: Snippet;
	} = $props();

	const api = app().api;

	// The panel only mounts once /api/me has resolved, and from then on this
	// panel owns these values — seed them once rather than tracking.
	let displayName = $state(untrack(() => me.display_name));
	let displayNameSaved = $state(true);
	let displayNameError = $state('');

	let autoRead = $state(untrack(() => me.settings.auto_read_audio));
	let autoReadError = $state('');

	async function saveDisplayName() {
		displayNameError = '';
		try {
			const r = await api.patchMe({ display_name: displayName.trim() });
			displayName = r.display_name;
			displayNameSaved = true;
		} catch {
			displayNameError = 'Could not save. Try again.';
		}
	}

	async function setAutoRead(v: boolean) {
		autoRead = v;
		autoReadError = '';
		try {
			await api.setMySettings({ auto_read_audio: v });
		} catch {
			autoRead = !v;
			autoReadError = 'Could not save. Try again.';
		}
	}
</script>

<section class="group">
	<h2>Your name</h2>
	<label class="fld">
		<span>
			How your name appears in shares and the journal. Clearing it goes back to the name from
			your sign-in.
		</span>
		<input
			type="text"
			bind:value={displayName}
			maxlength="64"
			oninput={() => (displayNameSaved = false)}
			onkeydown={(e) => e.key === 'Enter' && saveDisplayName()}
		/>
	</label>
	{#if !displayNameSaved}
		<button onclick={saveDisplayName}>Save name</button>
	{/if}
	{#if displayNameError}<p class="error">{displayNameError}</p>{/if}
</section>

<section class="group">
	<h2>Reading experience</h2>
	{@render own?.()}
	{#if me.tts}
		<label class="fld checkline">
			<input
				type="checkbox"
				checked={autoRead}
				onchange={(e) => setAutoRead(e.currentTarget.checked)}
			/>
			<span>
				<strong>Read readings aloud automatically.</strong> {aloud} starts speaking as it
				appears. You can also toggle this during a guided reading.
			</span>
		</label>
		{#if autoReadError}<p class="error">{autoReadError}</p>{/if}
	{/if}

	<p class="dim note">
		These follow your account, so they apply on every device. {remembered}, so there is
		nothing to set here.
	</p>
</section>

<section class="group">
	<h2>Elsewhere</h2>
	<p class="dim">
		{['Shared readings', ...accountHas, 'downloading your data'].join(', ')} and deleting your
		account live on <a href="/account">your account page</a>.
		{#if !me.is_admin}
			Instance-wide settings ({adminSets}) are managed by an admin.
		{/if}
	</p>
</section>

<style>
	.note {
		font-size: 0.9rem;
		margin-bottom: 0;
	}
</style>
