<script lang="ts">
	import { app } from './app';
	import { type AdminUser, type ReassignReport } from './types';

	const api = app().api;
	const { ownedExtras, deleteNote } = app().words;
	/** "a, b and c" */
	const list = (xs: string[]) => (xs.length > 1 ? `${xs.slice(0, -1).join(', ')} and ${xs.at(-1)}` : xs.join(''));
	const extras = ownedExtras ? [ownedExtras] : [];

	let { me_username }: { me_username: string } = $props();

	let people = $state<AdminUser[]>([]);
	let peopleError = $state('');
	let reassignTarget = $state<Record<string, string>>({});
	let reassignReport = $state<ReassignReport | null>(null);
	// Pending flag edits, keyed by username — nothing is sent until Save.
	let edits = $state<Record<string, { is_admin?: boolean; active?: boolean }>>({});
	const dirtyCount = $derived(Object.keys(edits).length);

	$effect(() => {
		loadPeople();
	});

	async function loadPeople() {
		people = await api.adminUsers();
		edits = {};
	}

	function flagOf(u: AdminUser, flag: 'is_admin' | 'active'): boolean {
		return edits[u.username]?.[flag] ?? u[flag];
	}

	function editFlag(u: AdminUser, flag: 'is_admin' | 'active', value: boolean) {
		const pending = { ...edits[u.username], [flag]: value };
		// drop entries that are back to the server state
		if (pending.is_admin === u.is_admin) delete pending.is_admin;
		if (pending.active === u.active) delete pending.active;
		if (Object.keys(pending).length) edits[u.username] = pending;
		else delete edits[u.username];
	}

	async function savePeople() {
		peopleError = '';
		for (const [username, patch] of Object.entries(edits)) {
			try {
				await api.adminUserUpdate(username, patch);
			} catch (e) {
				peopleError = `${username}: ${errMsgOf(e)}`;
			}
		}
		await loadPeople();
	}

	async function reassign(u: AdminUser) {
		const to = reassignTarget[u.username];
		if (!to) return;
		if (
			!confirm(
				`Move ALL of ${u.username}'s ${list(['readings', 'settings', ...extras, 'history'])} to ${to}, then deactivate ${u.username}? This cannot be undone from the UI.`
			)
		)
			return;
		peopleError = '';
		try {
			reassignReport = await api.adminReassign(u.username, to);
			await loadPeople();
		} catch (e) {
			peopleError = errMsgOf(e);
		}
	}

	function errMsgOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function deleteUser(u: AdminUser) {
		if (
			!confirm(
				`Permanently DELETE ${u.username}? Their ${list(['readings', 'shares', 'settings', ...extras])} are erased${deleteNote ? `; ${deleteNote}` : ''}. This cannot be undone.`
			)
		)
			return;
		peopleError = '';
		try {
			await api.adminUserDelete(u.username);
			await loadPeople();
		} catch (e) {
			peopleError = errMsgOf(e);
		}
	}
</script>

<p class="dim lede">
	Everyone this instance has seen. Admin and active changes are staged until you save them;
	reassigning or deleting takes effect immediately.
</p>

<div class="people">
	{#each people as u (u.username)}
		<div class="person" class:inactive={!u.active}>
			<span class="who">
				<strong>{u.display_name}</strong>
				<small class="dim">{u.username}{u.kind === 'system' ? ' · system' : ''}</small>
			</span>
			<label class="flag" class:pending={edits[u.username]?.is_admin !== undefined}>
				<input
					type="checkbox"
					checked={flagOf(u, 'is_admin')}
					disabled={u.username === me_username && u.is_admin}
					onchange={(e) => editFlag(u, 'is_admin', e.currentTarget.checked)}
				/>
				admin
			</label>
			<label class="flag" class:pending={edits[u.username]?.active !== undefined}>
				<input
					type="checkbox"
					checked={flagOf(u, 'active')}
					disabled={u.username === me_username}
					onchange={(e) => editFlag(u, 'active', e.currentTarget.checked)}
				/>
				active
			</label>
			{#if u.kind === 'system' || !u.active}
				<span class="reassign">
					<select bind:value={reassignTarget[u.username]}>
						<option value="" disabled selected>reassign data to…</option>
						{#each people.filter((p) => p.kind === 'person' && p.active && p.username !== u.username) as t (t.username)}
							<option value={t.username}>{t.display_name}</option>
						{/each}
					</select>
					<button onclick={() => reassign(u)} disabled={!reassignTarget[u.username]}>
						Reassign
					</button>
				</span>
			{/if}
			{#if !u.is_admin && u.username !== me_username}
				<button class="danger" onclick={() => deleteUser(u)}>Delete</button>
			{/if}
		</div>
	{/each}
</div>
{#if dirtyCount > 0}
	<p class="savebar">
		<button class="primary" onclick={savePeople}>
			Save {dirtyCount}
			{dirtyCount === 1 ? 'change' : 'changes'}
		</button>
		<button onclick={() => (edits = {})}>Discard</button>
	</p>
{/if}
{#if reassignReport}
	<p class="dim">
		Moved to {reassignReport.to}: {reassignReport.readings} readings,
		{reassignReport.usage_rows} usage rows, {reassignReport.settings} settings{#if reassignReport.staging_moved},
			{reassignReport.staging_moved.length} draft folders{reassignReport.staging_collisions?.length
				? ` (${reassignReport.staging_collisions.length} renamed on collision)`
				: ''}{/if}{#if reassignReport.library_restamped}, {reassignReport.library_restamped.length} library attributions{/if}.
		{reassignReport.from} deactivated.
	</p>
{/if}
{#if peopleError}<p class="error">{peopleError}</p>{/if}

<style>
	.people {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.person {
		display: flex;
		align-items: center;
		gap: 1rem;
		flex-wrap: wrap;
		padding: 0.4rem 0.6rem;
		border: 1px solid var(--border);
		border-radius: 8px;
	}

	.person.inactive {
		opacity: 0.55;
	}

	.person .who {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-width: 10rem;
	}

	.person .flag {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.85rem;
		color: var(--text-dim);
	}

	.person .reassign {
		display: inline-flex;
		gap: 0.4rem;
		align-items: center;
	}

	.person .reassign button {
		font-size: 0.85rem;
		padding: 0.25rem 0.7rem;
	}

	.person .danger {
		font-size: 0.85rem;
		padding: 0.25rem 0.7rem;
	}

	.person .flag.pending {
		color: var(--gold-bright);
	}

	.savebar {
		display: flex;
		gap: 0.6rem;
		align-items: center;
	}
</style>
