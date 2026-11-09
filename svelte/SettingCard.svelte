<script lang="ts">
	import type { Snippet } from 'svelte';

	/** A collapsible settings block. The summary carries a one-line status so
	 * the whole tab can be read without opening anything; cards that need
	 * attention (nothing configured yet, a gap, an error) open themselves. */
	let {
		title,
		status = '',
		tone = 'idle',
		open = false,
		children
	}: {
		title: string;
		status?: string;
		tone?: 'on' | 'off' | 'warn' | 'idle';
		open?: boolean;
		children: Snippet;
	} = $props();
</script>

<details class="card" {open}>
	<summary>
		<span class="chev" aria-hidden="true">▸</span>
		<span class="ttl">{title}</span>
		{#if status}
			<span class="status {tone}">
				{#if tone !== 'idle'}<span class="dot" aria-hidden="true">●</span>{/if}
				{status}
			</span>
		{/if}
	</summary>
	<div class="body">
		{@render children()}
	</div>
</details>

<style>
	.card {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--bg-raised);
		margin-bottom: 0.7rem;
	}

	summary {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		padding: 0.75rem 1rem;
		cursor: pointer;
		list-style: none;
		border-radius: var(--radius);
	}

	summary::-webkit-details-marker {
		display: none;
	}

	summary:hover .ttl {
		color: var(--gold-bright);
	}

	.chev {
		color: var(--text-dim);
		font-size: 0.8rem;
		transition: transform 0.15s;
		display: inline-block;
	}

	.card[open] .chev {
		transform: rotate(90deg);
	}

	.ttl {
		color: var(--gold);
		letter-spacing: 0.02em;
	}

	.status {
		margin-left: auto;
		font-size: 0.85rem;
		color: var(--text-dim);
		text-align: right;
	}

	.dot {
		font-size: 0.6rem;
		vertical-align: middle;
	}

	.status.on .dot {
		color: var(--gold);
	}

	.status.off .dot {
		color: var(--border);
	}

	.status.warn {
		color: var(--danger);
	}

	.body {
		padding: 0.2rem 1rem 1rem;
		border-top: 1px solid var(--border);
		margin-top: -1px;
		padding-top: 1rem;
	}
</style>
