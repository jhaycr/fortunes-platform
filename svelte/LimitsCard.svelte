<script lang="ts">
	import { app } from './app';
	import type { CardStatus } from './settings';

	const api = app().api;
	const practice = app().words.practice;

	let { onstatus }: { onstatus: (s: CardStatus) => void } = $props();

	let readings = $state('');
	let tokens = $state('');
	let minutes = $state('');
	let managedFields = $state<string[]>([]);
	let saved = $state(true);
	let error = $state('');
	let configFile = $state<string | null>(null);

	const managed = (f: string) => managedFields.includes(f);

	$effect(() => {
		refresh();
	});

	function report() {
		const set = [
			readings && `${readings} readings`,
			tokens && `${Number(tokens).toLocaleString()} tokens`,
			minutes && `${minutes} min audio`
		].filter(Boolean);
		onstatus({
			on: set.length > 0,
			detail: set.length ? `${set.join(' · ')} per person per day` : 'no caps'
		});
	}

	async function refresh() {
		const s = await api.getLimitsSettings();
		readings = s.readings_per_day != null ? String(s.readings_per_day) : '';
		tokens = s.llm_tokens_per_day != null ? String(s.llm_tokens_per_day) : '';
		minutes = s.tts_minutes_per_day != null ? String(s.tts_minutes_per_day) : '';
		managedFields = s.managed;
		configFile = s.config_file;
		report();
	}

	async function save() {
		error = '';
		try {
			// blank = disabled (sent as 0, stored as unset); managed fields not sent
			const num = (v: string) => (v.trim() === '' ? 0 : Number(v));
			await api.setLimitsSettings({
				...(managed('readings_per_day') ? {} : { readings_per_day: num(readings) }),
				...(managed('llm_tokens_per_day') ? {} : { llm_tokens_per_day: num(tokens) }),
				...(managed('tts_minutes_per_day') ? {} : { tts_minutes_per_day: num(minutes) })
			});
			await refresh();
			saved = true;
		} catch (e) {
			error = String(e);
		}
	}
</script>

<p class="dim">
	Per-person daily caps that stop runaway AI spend. Blank = no cap. Admins are always exempt.
	Blocked users see "resets at midnight"; {practice} and the journal keep working. Only AI
	generation pauses. A cap can be passed slightly by one in-flight call, never more.
</p>
<div class="limitrow">
	<label class="fld">
		<span>
			Readings / day
			{#if managed('readings_per_day')}<em class="dim">(managed)</em>{/if}
		</span>
		<input
			type="text"
			inputmode="numeric"
			bind:value={readings}
			oninput={() => (saved = false)}
			disabled={managed('readings_per_day')}
			placeholder="e.g. 10"
		/>
	</label>
	<label class="fld">
		<span>
			LLM tokens / day
			{#if managed('llm_tokens_per_day')}<em class="dim">(managed)</em>{/if}
		</span>
		<input
			type="text"
			inputmode="numeric"
			bind:value={tokens}
			oninput={() => (saved = false)}
			disabled={managed('llm_tokens_per_day')}
			placeholder="e.g. 150000"
		/>
	</label>
	<label class="fld">
		<span>
			Voice minutes / day
			{#if managed('tts_minutes_per_day')}<em class="dim">(managed)</em>{/if}
		</span>
		<input
			type="text"
			inputmode="decimal"
			bind:value={minutes}
			oninput={() => (saved = false)}
			disabled={managed('tts_minutes_per_day')}
			placeholder="e.g. 30"
		/>
	</label>
</div>
{#if managedFields.length}
	<p class="managed">
		Partly managed by <code>{configFile}</code>. Edit the config file rather than this page.
	</p>
{/if}
{#if error}<p class="error">{error}</p>{/if}
{#if managedFields.length < 3}
	<button onclick={save} disabled={saved}>{saved ? 'Saved' : 'Save limits'}</button>
{/if}

<style>
	.limitrow {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
		gap: 0 0.9rem;
	}
</style>
