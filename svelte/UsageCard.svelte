<script lang="ts">
	import { app } from './app';
	import { type UsageSummary } from './types';
	import type { CardStatus } from './settings';

	const api = app().api;

	let { onstatus }: { onstatus: (s: CardStatus) => void } = $props();

	let usage = $state<UsageSummary | null>(null);
	let days = $state(30);

	const fmt = (n: number) => n.toLocaleString();
	// OpenAI TTS returns ~128 kbps MP3 -> ~0.94 MB per audio-minute
	const audioMinutes = (bytes: number) => (bytes / 983040).toFixed(1);

	// Tracks `days` (read before the first await), so changing the period
	// reloads on its own.
	$effect(() => {
		load();
	});

	async function load() {
		usage = await api.adminUsage(days);
		const calls = usage.by_model.reduce((n, m) => n + m.calls, 0);
		onstatus({
			on: calls > 0,
			detail: calls ? `${fmt(calls)} calls in ${days} days` : `nothing in ${days} days`
		});
	}
</script>

<p class="dim">
	One ledger row per paid provider call. Cached audio replays and aborted streams cost nothing
	and aren't counted. Token counts come from the provider when it reports them.
</p>
<label class="fld daysfld">
	<span>Period</span>
	<select bind:value={days}>
		<option value={7}>Last 7 days</option>
		<option value={30}>Last 30 days</option>
		<option value={90}>Last 90 days</option>
		<option value={365}>Last year</option>
	</select>
</label>
{#if usage}
	{#if !usage.by_model.length}
		<p class="dim">
			No AI calls recorded in this period. (The ledger starts with this version; older
			readings predate it.)
		</p>
	{:else}
		<h3>By model</h3>
		<div class="tablewrap">
			<table>
				<thead>
					<tr>
						<th>Component</th><th>Model</th><th>Calls</th><th>Prompt tok</th><th>Output tok</th>
						<th>Audio</th>
					</tr>
				</thead>
				<tbody>
					{#each usage.by_model as m (m.component + m.model)}
						<tr>
							<td>{m.component === 'llm' ? 'LLM' : 'Voice'}</td>
							<td><code>{m.model}</code></td>
							<td>{fmt(m.calls)}</td>
							<td>{m.component === 'llm' ? fmt(m.prompt_tokens) : '—'}</td>
							<td>{m.component === 'llm' ? fmt(m.completion_tokens) : '—'}</td>
							<td>
								{m.component === 'tts'
									? `${audioMinutes(m.audio_bytes)} min · ${fmt(m.characters)} chars`
									: '—'}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<h3>By person</h3>
		<div class="tablewrap">
			<table>
				<thead>
					<tr>
						<th>Person</th><th>Component</th><th>Calls</th><th>Tokens (in / out)</th><th>Audio</th>
					</tr>
				</thead>
				<tbody>
					{#each usage.by_user as r (r.owner + r.component)}
						<tr>
							<td>{r.owner}</td>
							<td>{r.component === 'llm' ? 'LLM' : 'Voice'}</td>
							<td>{fmt(r.calls)}</td>
							<td>
								{r.component === 'llm'
									? `${fmt(r.prompt_tokens)} / ${fmt(r.completion_tokens)}`
									: '—'}
							</td>
							<td>{r.component === 'tts' ? `${audioMinutes(r.audio_bytes)} min` : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<h3>By day</h3>
		<div class="tablewrap">
			<table>
				<thead>
					<tr><th>Day</th><th>Calls</th><th>Tokens (in / out)</th><th>Audio</th></tr>
				</thead>
				<tbody>
					{#each usage.daily as d (d.day)}
						<tr>
							<td>{d.day}</td>
							<td>{fmt(d.calls)}</td>
							<td>{fmt(d.prompt_tokens)} / {fmt(d.completion_tokens)}</td>
							<td>{d.audio_bytes ? `${audioMinutes(d.audio_bytes)} min` : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
{/if}

<style>
	.daysfld select {
		width: auto;
	}

	h3 {
		font-size: 0.95rem;
		margin: 1.1rem 0 0.4rem;
	}

	.tablewrap {
		overflow-x: auto;
	}

	table {
		border-collapse: collapse;
		width: 100%;
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
	}

	th,
	td {
		text-align: left;
		padding: 0.35rem 0.9rem 0.35rem 0;
		border-bottom: 1px solid var(--border);
		white-space: nowrap;
	}

	th {
		color: var(--text-dim);
		font-weight: 600;
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
</style>
