<script lang="ts">
	// First-admin bootstrap. A fresh instance has no admin, so the server
	// prints a single-use token to the container log; pasting it here promotes
	// whoever is currently signed in. Backend routes under /auth/ are
	// login/callback/logout only: /auth/setup falls through to the SPA, which
	// is why an app mounts this as its routes/auth/setup page.
	import { app } from './app';
	import { ApiError } from './types';

	const api = app().api;
	const appId = app().id;

	type View = 'loading' | 'signedout' | 'open' | 'closed' | 'done' | 'unavailable';

	let view = $state<View>('loading');
	let isAdmin = $state(false);
	let who = $state('');
	let token = $state('');
	let submitting = $state(false);
	let error = $state('');

	$effect(() => {
		load();
	});

	async function load() {
		try {
			const s = await api.setupState();
			who = s.user;
			isAdmin = s.is_admin;
			view = s.required ? 'open' : 'closed';
		} catch (e) {
			if ((e as ApiError)?.status === 401) {
				view = 'signedout';
			} else {
				error = e instanceof Error ? e.message : String(e);
				view = 'unavailable';
			}
		}
	}

	async function claim(event: SubmitEvent) {
		event.preventDefault();
		if (!token.trim() || submitting) return;
		error = '';
		submitting = true;
		try {
			const r = await api.claimAdmin(token.trim());
			who = r.user;
			isAdmin = true;
			token = '';
			view = 'done';
		} catch (e) {
			// The server's wording is the useful one ("invalid or already-used
			// setup token") — show it verbatim.
			error = e instanceof Error ? e.message : String(e);
		} finally {
			submitting = false;
		}
	}
</script>

<section class="setup">
	<h1>First admin</h1>

	{#if view === 'loading'}
		<p class="dim">Checking…</p>
	{:else if view === 'signedout'}
		<p class="dim">
			Sign in first. The setup token promotes the account you're signed in as, so there has to be
			one.
		</p>
		<a class="primary-link" href="/auth/login?next=%2Fauth%2Fsetup">Sign in</a>
	{:else if view === 'open'}
		<p class="dim">
			This instance has no admin yet. Its setup token was printed to the container log.
			Look for the line beginning <code>{appId.toUpperCase()} SETUP:</code> (Dozzle, or
			<code>docker logs {appId}</code>). Entering it makes
			<strong>{who}</strong> an admin.
		</p>
		<form onsubmit={claim}>
			<label class="field">
				<span>Setup token</span>
				<!-- svelte-ignore a11y_autofocus -->
				<input
					type="text"
					bind:value={token}
					autofocus
					autocomplete="off"
					spellcheck="false"
					placeholder="paste the token from the log"
				/>
			</label>
			<button class="primary" type="submit" disabled={submitting || !token.trim()}>
				{submitting ? 'Claiming…' : 'Become admin'}
			</button>
		</form>
		{#if error}<p class="error">{error}</p>{/if}
	{:else if view === 'done'}
		<p class="ok">✦ Done. <strong>{who}</strong> is now an admin.</p>
		<p class="dim">
			The token is spent. Add other admins from Settings → People; a new token is only issued if the
			instance is ever left without one.
		</p>
		<a class="primary-link" href="/settings" data-sveltekit-reload>Go to Settings</a>
	{:else if view === 'closed'}
		<p class="dim">
			This instance already has an admin, so setup is closed and no token is live.
		</p>
		{#if isAdmin}
			<p class="dim">You're an admin. Manage the rest from Settings → People.</p>
			<a class="primary-link" href="/settings">Go to Settings</a>
		{:else}
			<p class="dim">Ask an admin if you need access to something.</p>
			<a class="primary-link" href="/">Back to readings</a>
		{/if}
	{:else}
		<p class="error">{error}</p>
		<button onclick={load}>Try again</button>
	{/if}
</section>

<style>
	.setup {
		max-width: 34rem;
		margin: 2rem auto;
		display: flex;
		flex-direction: column;
		gap: 1rem;
		align-items: flex-start;
	}

	form {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		width: 100%;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}

	.field span {
		font-size: 0.85rem;
		color: var(--text-dim);
		letter-spacing: 0.04em;
	}

	.dim {
		margin: 0;
	}

	code {
		background: var(--bg-raised);
		border-radius: 4px;
		padding: 0.05rem 0.3rem;
	}

	.ok {
		color: var(--gold-bright);
		margin: 0;
	}

	.error {
		margin: 0;
	}

	.primary-link {
		display: inline-block;
		padding: 0.6rem 1.6rem;
		border: 1px solid var(--gold);
		border-radius: 8px;
		color: var(--gold-bright);
	}

	.primary-link:hover {
		background: var(--bg-card);
	}

	button:disabled {
		opacity: 0.5;
		cursor: default;
	}
</style>
