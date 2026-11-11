<script lang="ts">
	// The settings page every Little Fortunes app has: You for everyone; AI
	// and People for an admin; on a backend that holds the visitor's own key
	// (the public demo), an AI tab for everyone with the key card. The app
	// hands in its pieces:
	//   you      its You panel (fortunes-platform/YouPanel.svelte, in its words)
	//   keyCard  the key card, for a backend with a local key
	//   aiExtra  a card shown after the AI settings (and, for someone with no
	//            AI tab whose instance reads, under You)
	//   tabs     admin tabs of its own, after You
	// and the page's URL with a way to replace it (SvelteKit's page.url and
	// replaceState), so ?tab= links a section and survives a reload.
	import { untrack, type Snippet } from 'svelte';
	import { app } from './app';
	import AiPanel from './AiPanel.svelte';
	import PeoplePanel from './PeoplePanel.svelte';
	import type { Me } from './types';

	let {
		url,
		replace,
		you,
		keyCard,
		aiExtra,
		tabs: own = []
	}: {
		url: URL;
		replace: (href: string) => void;
		you: Snippet<[Me]>;
		keyCard?: Snippet;
		aiExtra?: Snippet;
		tabs?: { id: string; label: string; panel: Snippet }[];
	} = $props();

	const api = app().api;

	let me = $state<Me | null>(null);

	$effect(() => {
		api.me().then((m) => (me = m));
	});

	// A backend that holds the visitor's own key (the public demo) has an AI
	// tab for everyone: the key card instead of the admin's connection panel.
	const tabs = $derived(
		me?.is_admin
			? [
					{ id: 'you', label: 'You' },
					...own.map((t) => ({ id: t.id, label: t.label })),
					{ id: 'ai', label: 'AI' },
					{ id: 'people', label: 'People' }
				]
			: api.capabilities.localKey
				? [
						{ id: 'you', label: 'You' },
						{ id: 'ai', label: 'AI' }
					]
				: [{ id: 'you', label: 'You' }]
	);

	// ?tab= makes a section linkable and survives a reload. It's honored only
	// once the tab actually exists for this person — a non-admin landing on
	// ?tab=ai gets their own settings, not an empty page.
	// Coming back from OpenRouter's consent page lands on /settings?code=…:
	// that belongs to the AI tab, which finishes the sign-in.
	// read once: the tab the page opened on
	const initial = untrack(() => url.searchParams.get('tab') ?? (url.searchParams.has('code') ? 'ai' : 'you'));
	let requested = $state(initial);
	const active = $derived(tabs.some((t) => t.id === requested) ? requested : 'you');
	// One tab isn't a tab bar: a non-admin gets a plain page, with none of the
	// tab roles that would then point at buttons that don't exist.
	const multi = $derived(tabs.length > 1);

	// Panels stay mounted once opened, so half-typed settings survive a trip
	// to another tab.
	let visited = $state<Record<string, boolean>>({ you: true, [initial]: true });

	function pick(id: string) {
		requested = id;
		visited[id] = true;
		replace(id === 'you' ? '/settings' : `/settings?tab=${id}`);
	}

	function onTabKey(e: KeyboardEvent) {
		const i = tabs.findIndex((t) => t.id === active);
		let n = i;
		if (e.key === 'ArrowRight') n = (i + 1) % tabs.length;
		else if (e.key === 'ArrowLeft') n = (i - 1 + tabs.length) % tabs.length;
		else if (e.key === 'Home') n = 0;
		else if (e.key === 'End') n = tabs.length - 1;
		else return;
		e.preventDefault();
		pick(tabs[n].id);
		document.getElementById(`tab-${tabs[n].id}`)?.focus();
	}
</script>

<h1>Settings</h1>

{#if !me}
	<p class="dim">Loading…</p>
{:else}
	{#if multi}
		<div class="tabs" role="tablist" aria-label="Settings sections">
			{#each tabs as t (t.id)}
				<button
					class="tab"
					class:active={active === t.id}
					role="tab"
					id="tab-{t.id}"
					aria-controls="panel-{t.id}"
					aria-selected={active === t.id}
					tabindex={active === t.id ? 0 : -1}
					onclick={() => pick(t.id)}
					onkeydown={onTabKey}
				>
					{t.label}
					{#if t.id === 'ai'}<small class="opt">optional</small>{/if}
				</button>
			{/each}
		</div>
	{/if}

	{#if visited.you}
		<div
			class="panel"
			id="panel-you"
			role={multi ? 'tabpanel' : undefined}
			aria-labelledby={multi ? 'tab-you' : undefined}
			hidden={active !== 'you'}
		>
			{@render you(me)}<!-- an AI setting; people without an AI tab find it here -->{#if !me.is_admin && !api.capabilities.localKey && me.interpretation}{@render aiExtra?.()}{/if}
		</div>
	{/if}
	{#if !me.is_admin && api.capabilities.localKey && visited.ai}
		<div class="panel" id="panel-ai" role="tabpanel" aria-labelledby="tab-ai" hidden={active !== 'ai'}>
			{@render keyCard?.()}{@render aiExtra?.()}
		</div>
	{/if}
	{#if me.is_admin}
		{#each own as t (t.id)}
			{#if visited[t.id]}
				<div
					class="panel"
					id="panel-{t.id}"
					role="tabpanel"
					aria-labelledby="tab-{t.id}"
					hidden={active !== t.id}
				>
					{@render t.panel()}
				</div>
			{/if}
		{/each}
		{#if visited.ai}
			<div
				class="panel"
				id="panel-ai"
				role="tabpanel"
				aria-labelledby="tab-ai"
				hidden={active !== 'ai'}
			>
				<AiPanel />{@render aiExtra?.()}
			</div>
		{/if}
		{#if visited.people}
			<div
				class="panel"
				id="panel-people"
				role="tabpanel"
				aria-labelledby="tab-people"
				hidden={active !== 'people'}
			>
				<PeoplePanel me_username={me.user} />
			</div>
		{/if}
	{/if}
{/if}

<style>
	.tabs {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
		margin: 0.9rem 0 1.4rem;
		border-bottom: 1px solid var(--border);
		padding-bottom: 0.7rem;
	}

	.tab {
		display: inline-flex;
		align-items: baseline;
		gap: 0.4rem;
		border-radius: 999px;
		padding: 0.3rem 1rem;
		color: var(--text-dim);
		background: transparent;
	}

	.tab.active {
		color: var(--gold-bright);
		border-color: var(--gold);
		background: var(--bg-raised);
	}

	.tab .opt {
		font-size: 0.7rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-dim);
	}

	.panel {
		max-width: 46rem;
	}

	/* Form furniture shared by every panel. Scoped to .panel so it reaches
	   the child components' markup without leaking to the rest of the app. */
	.panel :global(.group) {
		margin-bottom: 2rem;
	}

	.panel :global(h2) {
		font-size: 1.05rem;
	}

	.panel :global(.lede) {
		margin-top: 0;
		margin-bottom: 1.2rem;
	}

	.panel :global(.fld) {
		display: block;
		margin-bottom: 0.8rem;
	}

	.panel :global(.fld > span) {
		display: block;
		color: var(--text-dim);
		margin-bottom: 0.25rem;
	}

	.panel :global(.fld input:disabled),
	.panel :global(.fld textarea:disabled),
	.panel :global(.fld select:disabled) {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.panel :global(.fld.inline) {
		display: flex;
		flex-direction: row;
		align-items: center;
		gap: 0.5rem;
	}

	.panel :global(.fld.inline input) {
		width: auto;
	}

	.panel :global(.checkline) {
		display: flex;
		gap: 0.6rem;
		align-items: flex-start;
		cursor: pointer;
	}

	.panel :global(.checkline input) {
		margin-top: 0.35rem;
		accent-color: var(--gold);
	}

	.panel :global(.checkline strong) {
		color: var(--text);
		font-weight: 600;
	}

	.panel :global(textarea) {
		width: 100%;
		font-size: 0.9rem;
		line-height: 1.5;
	}

	.panel :global(.managed) {
		color: var(--text-dim);
		border-left: 2px solid var(--border);
		padding-left: 0.6rem;
		font-size: 0.92em;
	}

	.panel :global(.range) {
		display: flex;
		align-items: center;
		gap: 0.8rem;
	}

	.panel :global(.range input) {
		flex: 1;
		accent-color: var(--gold);
	}

	.panel :global(input[type='range']) {
		accent-color: var(--gold);
	}

	.panel :global(.range strong) {
		color: var(--gold-bright);
		min-width: 3rem;
		text-align: right;
	}

	.panel :global(code) {
		background: var(--bg-card);
		padding: 0.1rem 0.3rem;
		border-radius: 4px;
	}

	/* Inside a raised card, fields need to sit a shade darker than the card
	   or they vanish into it. */
	.panel :global(.card input[type='text']),
	.panel :global(.card input[type='password']),
	.panel :global(.card input[type='number']),
	.panel :global(.card textarea),
	.panel :global(.card select) {
		background: var(--bg);
	}
</style>
