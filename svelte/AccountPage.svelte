<script lang="ts">
	// The account page every Little Fortunes app has: who you are, the
	// readings you've shared and that others have shared with you, your data
	// to download, and deleting it. The app hands in how a shared reading with
	// no question is named (`title`), sections of its own (`own`, after the
	// shares; `summary`, after the reading count) and a few words.
	import type { Snippet } from 'svelte';
	import { app } from './app';
	import type { AccountBase, GrantedShareBase, ReceivedShareBase } from './types';

	interface Props {
		/** A shared reading's name: its question, else what the app calls it.
		 * (A method, so an app's function over its own share type fits.) */
		title(s: GrantedShareBase | ReceivedShareBase): string;
		summary?: Snippet<[AccountBase]>;
		own?: Snippet<[AccountBase]>;
		/** What of a reading goes to the AI in the demo ("its cast and question"). */
		pieces?: string;
		/** The end of "the full journal with interpretations and sharing, ...", with its stop. */
		exportHas?: string;
		/** What deleting does on a server, in a sentence or two. */
		erases?: string;
		/** The last-chance dialog's "this permanently erases ...". */
		lastChance?: string;
	}

	let {
		title,
		summary,
		own,
		pieces = 'its question',
		exportHas = 'and your settings.',
		erases = 'Permanently erases your readings and settings.',
		lastChance = 'your journal and settings'
	}: Props = $props();

	const api = app().api;

	let account = $state<AccountBase | null>(null);
	let busy = $state('');
	let deleteConfirm = $state('');
	let deleteError = $state('');

	async function deleteMyData() {
		if (!account) return;
		if (!confirm(`Last chance: this permanently erases ${lastChance}. Continue?`)) return;
		deleteError = '';
		try {
			await api.deleteMe(deleteConfirm.trim());
			// account (and session) are gone — back to the signed-out shell
			window.location.href = '/';
		} catch (e) {
			deleteError = e instanceof Error ? e.message : String(e);
		}
	}

	$effect(() => {
		load();
	});

	async function exportData() {
		busy = 'export';
		try {
			const { blob, filename } = await api.exportMyData();
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = filename;
			a.click();
			setTimeout(() => URL.revokeObjectURL(url), 10_000);
		} finally {
			busy = '';
		}
	}

	async function load() {
		account = await api.account();
	}

	function fmtDate(ts: number): string {
		return new Date(ts * 1000).toLocaleDateString(undefined, { dateStyle: 'medium' });
	}

	/** A person as people know them: their display name, else the username. */
	function nameOf(username: string): string {
		return account?.names?.[username] ?? username;
	}

	// Revoke one grantee by re-setting the share without them; drop to private
	// when they were the last one.
	async function revoke(share: GrantedShareBase, grantee: string) {
		busy = `${share.id}:${grantee}`;
		const remaining = share.shared_with.filter((g) => g !== grantee);
		await api.setSharing(share.id, remaining.length ? 'specific' : 'private', remaining);
		await load();
		busy = '';
	}

	async function makePrivate(share: GrantedShareBase) {
		busy = `${share.id}:all`;
		await api.setSharing(share.id, 'private', []);
		await load();
		busy = '';
	}
</script>

<h1>Account</h1>

{#if account}
	<section class="identity">
		<div class="who">
			<span class="name">☾ {account.display_name}</span>
			{#if account.is_admin}<span class="tag">admin</span>{/if}
			{#if !account.authenticated}<span class="tag warn">local / LAN</span>{/if}
		</div>
		<p class="dim">
			Saved under <code>{account.user}</code> · {account.reading_count} reading{account.reading_count ===
			1
				? ''
				: 's'}
			{@render summary?.(account)}
		</p>
	</section>

	{#if api.capabilities.sharing}
	<section>
		<h2>Readings you've shared</h2>
		{#if account.shares_granted.length === 0}
			<p class="dim">You haven't shared any readings.</p>
		{:else}
			<ul class="shares">
				{#each account.shares_granted as s (s.id)}
					<li>
						<a href="/journal/{s.id}" class="rlink">{title(s)}</a>
						<span class="dim">· {fmtDate(s.created_at)}</span>
						{#if s.visibility === 'everyone'}
							<div class="row">
								<span class="tag">Everyone</span>
								<button disabled={busy === `${s.id}:all`} onclick={() => makePrivate(s)}>
									Make private
								</button>
							</div>
						{:else}
							<div class="grantees">
								{#each s.shared_with as g (g)}
									<span class="chip">
										{nameOf(g)}
										<button
											class="x"
											title="Revoke {nameOf(g)}"
											disabled={busy === `${s.id}:${g}`}
											onclick={() => revoke(s, g)}>×</button
										>
									</span>
								{/each}
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<section>
		<h2>Shared with you</h2>
		{#if account.shares_received.length === 0}
			<p class="dim">No one has shared a reading with you.</p>
		{:else}
			<ul class="shares">
				{#each account.shares_received as s (s.id)}
					<li>
						<a href="/journal/{s.id}" class="rlink">{title(s)}</a>
						<span class="dim">· from {nameOf(s.owner)} · {fmtDate(s.granted_at)}</span>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
	{/if}

	{@render own?.(account)}

	<section>
		<h2>Your data</h2>
		{#if api.capabilities.localData}
			<p class="dim">
				Everything here lives in this browser. Nothing leaves it unless you add your own AI
				key: then a reading you ask to have interpreted ({pieces}) goes to
				OpenRouter, and nowhere else. Download your data as a file: the full journal with
				interpretations, and your settings.
			</p>
		{:else}
			<p class="dim">
				Download everything your account owns: the full journal with interpretations and
				sharing, {exportHas}
			</p>
		{/if}
		<p>
			<button class="export" onclick={exportData} disabled={busy === 'export'}>
				{busy === 'export' ? 'Preparing…' : 'Download my data'}
			</button>
		</p>

		<h3 class="dangerhead">Delete my data</h3>
		{#if api.capabilities.localData}
			<p class="dim">
				Erases your readings, settings and any AI key from this browser. Type
				<code>{account.user}</code> to arm the button.
			</p>
		{:else}
			<p class="dim">
				{erases} Signing in again afterwards starts a fresh, empty account. Type your username
				(<code>{account.user}</code>) to arm the button.
			</p>
		{/if}
		<p class="deleterow">
			<input type="text" bind:value={deleteConfirm} placeholder={account.user} />
			<button
				class="danger"
				disabled={deleteConfirm !== account.user}
				onclick={deleteMyData}
			>
				Delete everything
			</button>
		</p>
		{#if deleteError}<p class="error">{deleteError}</p>{/if}
	</section>
{:else}
	<p class="dim">Loading…</p>
{/if}

<style>
	.export {
		all: unset;
		cursor: pointer;
		color: var(--gold-bright);
		border-bottom: 1px solid var(--gold);
	}

	.export:disabled {
		cursor: progress;
		opacity: 0.7;
	}

	.dangerhead {
		margin-top: 1.6rem;
		color: var(--danger);
	}

	.deleterow {
		display: flex;
		gap: 0.6rem;
		align-items: center;
	}

	.deleterow input {
		max-width: 16rem;
	}

	.identity {
		margin-bottom: 1.5rem;
	}
	.who {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}
	.name {
		font-size: 1.2rem;
		font-weight: 600;
	}
	section {
		margin-bottom: 1.8rem;
	}
	h2 {
		font-size: 1.05rem;
		margin-bottom: 0.6rem;
	}
	.shares {
		list-style: none;
		padding: 0;
		display: grid;
		gap: 0.7rem;
	}
	.shares li {
		padding: 0.6rem 0.8rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
	}
	.rlink {
		font-weight: 600;
		color: var(--text);
	}
	.row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.4rem;
	}
	.grantees {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		margin-top: 0.4rem;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		padding: 0.15rem 0.2rem 0.15rem 0.5rem;
		background: var(--bg-raised);
		border-radius: 999px;
		font-size: 0.85rem;
	}
	.chip .x {
		border: 0;
		background: transparent;
		cursor: pointer;
		font-size: 1rem;
		line-height: 1;
		padding: 0 0.3rem;
		color: var(--accent);
	}
	.tag {
		font-size: 0.75rem;
		padding: 0.1rem 0.45rem;
		border-radius: 999px;
		background: var(--bg-raised);
		color: var(--accent);
	}
	.tag.warn {
		color: #e0a030;
	}
	code {
		background: var(--bg-raised);
		padding: 0.1rem 0.4rem;
		border-radius: 4px;
	}
</style>
