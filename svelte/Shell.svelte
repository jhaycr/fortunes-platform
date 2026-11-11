<script lang="ts">
	// The page shell every Little Fortunes app has: the name (the suite's half
	// goes to the suite's page, the app's half home), the app's links, who is
	// signed in with the way out, the public demo's notice, and the page.
	// The app's root layout renders it around its pages:
	//
	//   <Shell {links} {favicon} path={page.url.pathname} onme={(m) => syncPrefs(m.settings)}>
	//     {#snippet mark()}<Mark />{/snippet}
	//     {@render children()}
	//   </Shell>
	import type { Snippet } from 'svelte';
	import { app, SUITE_URL } from './app';
	import type { Me } from './types';

	let {
		links,
		path,
		favicon,
		mark,
		accountTitle = 'Your account: identity, shares, your data',
		onme,
		children
	}: {
		/** The app's own pages, in nav order; '/' is home. */
		links: { href: string; label: string }[];
		/** The current page's path (the app's `page.url.pathname`): its link is marked. */
		path: string;
		favicon: string;
		/** The app's sign, before its name. */
		mark: Snippet;
		/** The account link's tooltip. */
		accountTitle?: string;
		/** Called once with who is signed in (an app syncs its saved prefs here). */
		onme?: (m: Me) => void;
		children: Snippet;
	} = $props();

	const api = app().api;
	const APP_NAME = app().name;

	let user = $state('');
	let logoutUrl = $state<string | null>(null);
	let version = $state('');
	// Clean semver gets a "v" prefix; dev stamps (e.g. "dev-201ba30-dirty") print as-is.
	let versionLabel = $derived(version ? (/^\d/.test(version) ? `v${version}` : version) : '');
	$effect(() => {
		api.me().then((m) => {
			user = m.display_name;
			logoutUrl = m.logout_url;
			version = m.version;
			onme?.(m);
		});
	});

	// The name is the suite's and the app's: on every build the suite's half goes to the
	// suite's page and the app's half is the way home.
	const [suite, own] = APP_NAME.split(' · ');

	function isActive(href: string): boolean {
		return href === '/' ? path === '/' : path.startsWith(href);
	}

	async function doLogout() {
		try {
			const r = await fetch('/auth/logout', { method: 'POST' });
			const out = await r.json();
			window.location.href = out.logout_url || '/';
		} catch {
			window.location.href = '/';
		}
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="shell">
	<header>
		<div class="brand">
			<span class="title">
				{#if own}
					<a href={SUITE_URL}>{@render mark()} {suite}</a> · <a href="/">{own}</a>
				{:else}
					<a href="/">{@render mark()} {APP_NAME}</a>
				{/if}
			</span>
			{#if versionLabel}<span class="version">{versionLabel}</span>{/if}
		</div>
		<nav>
			{#each links as link (link.href)}
				<a href={link.href} class:active={isActive(link.href)}>{link.label}</a>
			{/each}
		</nav>
		{#if user}
			<span class="account">
				<a class="user" href="/account" title={accountTitle}>☾ {user}</a>
				{#if logoutUrl === '/auth/logout'}
					<!-- OIDC session: the app destroys it (POST), then we follow the
					     IdP's end-session URL so the SSO session ends too. -->
					<button class="logout" onclick={doLogout} title="Sign out">Log out</button>
				{:else if logoutUrl}
					<a class="logout" href={logoutUrl} data-sveltekit-reload title="Sign out">Log out</a>
				{/if}
			</span>
		{/if}
	</header>
	{#if api.capabilities.localData}
		<p class="demo">Public demo. Your readings stay in this browser.</p>
	{/if}
	<main>
		{@render children()}
	</main>
</div>

<style>
	.shell {
		max-width: 72rem;
		margin: 0 auto;
		padding: 0 1rem 4rem;
	}

	header {
		display: flex;
		align-items: baseline;
		gap: 2rem;
		padding: 1.2rem 0.2rem;
		border-bottom: 1px solid var(--border);
		margin-bottom: 1.5rem;
	}

	.brand {
		display: inline-flex;
		flex-direction: column;
		line-height: 1.1;
	}

	.brand .title {
		font-size: 1.3rem;
		color: var(--gold-bright);
		letter-spacing: 0.06em;
	}

	.brand .title a {
		color: inherit;
	}

	.brand .title a:hover {
		text-decoration: underline;
		text-underline-offset: 0.2em;
	}

	.brand .version {
		font-size: 0.62rem;
		color: var(--text-dim);
		letter-spacing: 0.14em;
		text-transform: uppercase;
	}

	nav {
		display: flex;
		gap: 1.2rem;
	}

	nav a {
		color: var(--text-dim);
		padding-bottom: 0.2rem;
	}

	nav a.active {
		color: var(--gold);
		border-bottom: 1px solid var(--gold);
	}

	.account {
		margin-left: auto;
		display: flex;
		align-items: baseline;
		gap: 0.9rem;
	}

	.user {
		color: var(--text-dim);
		font-size: 0.85rem;
		border-bottom: 1px solid transparent;
	}

	.user:hover {
		color: var(--gold);
		border-bottom-color: var(--gold);
	}

	.logout {
		all: unset;
		cursor: pointer;
		color: var(--text-dim);
		font-size: 0.85rem;
		border-bottom: 1px solid transparent;
	}

	.logout:hover {
		color: var(--gold);
		border-bottom-color: var(--gold);
	}

	/* On a phone the links take a row of their own, and the account goes up
	   beside the title rather than onto a third row by itself. */
	@media (max-width: 480px) {
		header {
			gap: 1rem;
			flex-wrap: wrap;
		}
		.account {
			order: 1;
		}
		nav {
			order: 2;
			flex-basis: 100%;
		}
	}

	.demo {
		margin: 0 0 1rem;
		padding: 0.35rem 0.8rem;
		text-align: center;
		font-size: 0.85rem;
		color: var(--text-dim);
		background: var(--bg-raised);
		border: 1px solid var(--border);
		border-radius: var(--radius);
	}
</style>
