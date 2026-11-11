<script lang="ts">
	import { app } from './app';
	import SettingCard from './SettingCard.svelte';
	import TextCard from './TextCard.svelte';
	import VoiceCard from './VoiceCard.svelte';
	import LimitsCard from './LimitsCard.svelte';
	import UsageCard from './UsageCard.svelte';
	import type { CardStatus } from './settings';

	const api = app().api;
	const appName = app().short;
	const withoutAi = app().words.withoutAi;

	const blank: CardStatus = { on: false, detail: '…' };
	let text = $state<CardStatus>(blank);
	let voice = $state<CardStatus>(blank);
	let limits = $state<CardStatus>(blank);
	let usage = $state<CardStatus>(blank);

	// Any card may be the one that notices a broken /data/config.yaml.
	const configError = $derived(text.configError || voice.configError || null);
	const tone = (s: CardStatus) => (s.warn ? 'warn' : s.on ? 'on' : 'off');
	const line = (s: CardStatus) => s.warn ?? s.detail;
</script>

<div class="intro">
	<p>
		<strong>AI is optional.</strong> {withoutAi} all work with nothing configured here. Set up a
		connection and {appName} gains two extra things:
		written interpretations of a reading, and readings spoken aloud in each reader's voice.
	</p>
	<dl class="statuses">
		<div>
			<dt>Written interpretations</dt>
			<dd class={tone(text)}><span class="dot" aria-hidden="true">●</span> {line(text)}</dd>
		</div>
		{#if api.capabilities.tts}
			<div>
				<dt>Spoken readings</dt>
				<dd class={tone(voice)}><span class="dot" aria-hidden="true">●</span> {line(voice)}</dd>
			</div>
		{/if}
	</dl>
	<p class="dim fine">Each is independent. You can run one, both, or neither.</p>
</div>

{#if configError}
	<p class="error cfg">Config file problem: {configError}</p>
{/if}

<SettingCard
	title="Written interpretations"
	status={line(text)}
	tone={tone(text)}
	open={!text.on}
>
	<TextCard onstatus={(s) => (text = s)} />
</SettingCard>

{#if api.capabilities.tts}
	<SettingCard
		title="Spoken readings"
		status={line(voice)}
		tone={tone(voice)}
		open={!voice.on || Boolean(voice.warn)}
	>
		<VoiceCard onstatus={(s) => (voice = s)} />
	</SettingCard>
{/if}

<SettingCard title="Daily limits" status={line(limits)} tone={tone(limits)}>
	<LimitsCard onstatus={(s) => (limits = s)} />
</SettingCard>

<SettingCard title="Usage" status={line(usage)} tone="idle">
	<UsageCard onstatus={(s) => (usage = s)} />
</SettingCard>

<style>
	.intro {
		border: 1px solid var(--border);
		border-left: 3px solid var(--accent);
		border-radius: var(--radius);
		padding: 0.9rem 1.1rem;
		margin-bottom: 1.2rem;
		background: var(--bg-raised);
	}

	.intro p {
		margin: 0;
	}

	.intro strong {
		color: var(--gold-bright);
	}

	.statuses {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem 2.2rem;
		margin: 0.9rem 0 0.6rem;
	}

	.statuses div {
		display: flex;
		flex-direction: column;
	}

	dt {
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
	}

	dd {
		margin: 0;
	}

	dd .dot {
		font-size: 0.6rem;
		vertical-align: middle;
	}

	dd.on .dot {
		color: var(--gold);
	}

	dd.off {
		color: var(--text-dim);
	}

	dd.off .dot {
		color: var(--border);
	}

	dd.warn {
		color: var(--danger);
	}

	.fine {
		font-size: 0.88rem;
	}

	.cfg {
		margin-top: 0;
	}
</style>
