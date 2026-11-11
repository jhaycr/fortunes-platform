<script lang="ts" generics="R extends Shareable">
	// Who can see a reading: nobody else, chosen people, or everyone on the
	// instance. Works on any app's saved reading.
	import { app } from './app';
	import type { Person, Shareable, Visibility } from './types';

	const api = app().api;
	const appName = app().short;

	let { reading = $bindable() }: { reading: R } = $props();

	let people = $state<Person[]>([]);
	let peopleLoaded = false;
	let open = $state(false);
	let busy = $state(false);
	let error = $state('');

	// Draft selection, only committed on Save so picking several people is one
	// round trip rather than one per click.
	let choice = $state<Visibility>(reading.visibility);
	let picked = $state<string[]>([...reading.shared_with]);

	const LABELS: Record<Visibility, string> = {
		private: 'Private',
		specific: 'Specific people',
		everyone: 'Everyone'
	};

	function summary(): string {
		if (reading.visibility === 'everyone') return 'Shared with everyone';
		if (reading.visibility === 'private') return 'Private';
		const n = reading.shared_with.length;
		return n ? `Shared with ${reading.shared_with.map(name).join(', ')}` : 'Shared with nobody yet';
	}

	function name(username: string): string {
		return people.find((p) => p.username === username)?.display_name ?? username;
	}

	async function loadPeople() {
		if (peopleLoaded) return;
		// You always see your own readings; listing yourself as a share
		// target is just a no-op the backend drops.
		people = (await api.users()).filter((p) => p.username !== reading.owner);
		peopleLoaded = true;
	}

	// The closed button's tooltip names who it is shared with, by the names
	// people know them by.
	$effect(() => {
		if (reading.visibility === 'specific' && reading.shared_with.length) loadPeople().catch(() => {});
	});

	async function start() {
		open = true;
		error = '';
		choice = reading.visibility;
		picked = [...reading.shared_with];
		try {
			await loadPeople();
		} catch {
			error = 'Could not load the list of people.';
		}
	}

	function toggle(username: string) {
		picked = picked.includes(username)
			? picked.filter((u) => u !== username)
			: [...picked, username];
	}

	async function save() {
		busy = true;
		error = '';
		try {
			reading = (await api.setSharing(reading.id, choice, picked)) as R;
			open = false;
		} catch {
			error = 'Could not save. Please try again.';
		} finally {
			busy = false;
		}
	}
</script>

{#if open}
	<div class="panel">
		<fieldset>
			<legend>Who can see this reading?</legend>
			{#each Object.entries(LABELS) as [value, label] (value)}
				<label class="opt">
					<input type="radio" name="visibility" {value} bind:group={choice} />
					<span>{label}</span>
				</label>
			{/each}
		</fieldset>

		{#if choice === 'specific'}
			{#if people.length}
				<ul class="people">
					{#each people as p (p.username)}
						<li>
							<label class="opt">
								<input
									type="checkbox"
									checked={picked.includes(p.username)}
									onchange={() => toggle(p.username)}
								/>
								<span>{p.display_name}</span>
							</label>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="dim">Nobody else has used {appName} yet.</p>
			{/if}
		{/if}

		{#if error}<p class="error">{error}</p>{/if}

		<div class="actions">
			<button onclick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
			<button class="ghost" onclick={() => (open = false)} disabled={busy}>Cancel</button>
		</div>
	</div>
{:else}
	<button onclick={start} title={summary()}>
		{LABELS[reading.visibility]}{reading.visibility === 'specific'
			? ` (${reading.shared_with.length})`
			: ''}
	</button>
{/if}

<style>
	.panel {
		display: grid;
		gap: 0.75rem;
		padding: 0.85rem 1rem;
		border: 1px solid var(--line, #3a3a4a);
		border-radius: 0.6rem;
		background: var(--panel, rgba(255, 255, 255, 0.04));
		/* A fixed width, not a percentage: inside a content-sized row a
		   percentage resolves against a width that left it out, and the
		   panel then crowded its neighbours past the edge. */
		width: 20rem;
		max-width: 100%;
	}
	fieldset {
		border: 0;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.35rem;
	}
	legend {
		padding: 0 0 0.4rem;
		font-weight: 600;
	}
	.opt {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		cursor: pointer;
	}
	.people {
		list-style: none;
		margin: 0;
		padding: 0 0 0 1.4rem;
		display: grid;
		gap: 0.3rem;
		max-height: 12rem;
		overflow-y: auto;
	}
	.actions {
		display: flex;
		gap: 0.5rem;
	}
	.ghost {
		background: transparent;
	}
	.dim {
		margin: 0 0 0 1.4rem;
		font-size: 0.9em;
	}
	.error {
		margin: 0;
		font-size: 0.9em;
	}
</style>
