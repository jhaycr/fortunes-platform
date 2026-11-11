<script lang="ts">
	import { app } from './app';
	import type { DesignedVoice, ProviderVoice, TtsSettings, VoiceProvider, VoiceValues } from './types';
	import type { CardStatus } from './settings';

	const api = app().api;
	const appName = app().short;

	let { onstatus }: { onstatus: (s: CardStatus) => void } = $props();

	let baseUrl = $state('');
	let model = $state('');
	let apiKey = $state('');
	let keySet = $state(false);
	let saved = $state(true);
	let error = $state('');
	let managedFields = $state<string[]>([]);
	let provider = $state('openai');
	let providers = $state<VoiceProvider[]>([]);
	let gaps = $state<Record<string, string[]>>({});
	let connections = $state<TtsSettings['connections']>({});
	let configFile = $state<string | null>(null);
	// Editable per-persona blocks for the ACTIVE provider. Shape varies by
	// provider, so the form is generated from its declared fields.
	let voices = $state<Record<string, VoiceValues>>({});
	let providerVoices = $state<ProviderVoice[]>([]);
	let voiceListError = $state('');
	let previewAudio: HTMLAudioElement | null = null;
	// Voice design (providers that build a voice from prose)
	let descriptions = $state<Record<string, string>>({});
	let designOpen = $state(''); // persona whose panel is open
	let designText = $state('');
	let designBusy = $state(false);
	let designError = $state('');
	let designCandidates = $state<DesignedVoice[]>([]);

	const managed = (f: string) => managedFields.includes(f);

	/** The active provider's field specs drive the whole form. */
	const activeProvider = $derived(providers.find((p) => p.name === provider));

	$effect(() => {
		refresh();
	});

	function report() {
		// A provider with a canonical endpoint (ElevenLabs) needs no explicit
		// base URL; the OpenAI-compatible one does. Mirrors tts.config().
		const on = Boolean(baseUrl || (activeProvider && activeProvider.name !== 'openai'));
		const missing = Object.entries(gaps).filter(([, f]) => f.length);
		onstatus({
			on,
			detail: on ? (activeProvider?.label ?? provider) : 'not configured',
			warn:
				on && missing.length
					? `${missing.map(([p]) => p).join(', ')}: no voice set`
					: undefined
		});
	}

	async function refresh() {
		const s = await api.getTtsSettings();
		provider = s.provider;
		providers = s.providers;
		connections = s.connections;
		baseUrl = s.base_url;
		model = s.model;
		keySet = s.api_key_set;
		managedFields = s.managed;
		gaps = s.gaps;
		// Blocks are provider-shaped; start from the provider's defaults so
		// every declared field has a value to bind to.
		voices = Object.fromEntries(
			Object.entries(s.voices).map(([p, v]) => [p, { ...(s.defaults[p] ?? {}), ...(v ?? {}) }])
		);
		configFile = s.config_file;
		descriptions = s.descriptions;
		providerVoices = [];
		voiceListError = '';
		report();
		if (activeProvider?.supports_listing) loadProviderVoices();
	}

	/** Seed the design prompt from the persona's own written character —
	 * the same prose that steers OpenAI, reused at design time. */
	function openDesign(persona: string) {
		designOpen = persona;
		designText = descriptions[persona] ?? '';
		designCandidates = [];
		designError = '';
	}

	async function runDesign() {
		designBusy = true;
		designError = '';
		designCandidates = [];
		try {
			designCandidates = await api.designVoice(designText);
		} catch (e) {
			designError = e instanceof Error ? e.message : String(e);
		} finally {
			designBusy = false;
		}
	}

	async function keepDesigned(persona: string, chosen: DesignedVoice) {
		designBusy = true;
		designError = '';
		try {
			const { voice_id } = await api.keepDesignedVoice({
				generated_voice_id: chosen.generated_voice_id,
				name: `${appName} ${persona}`,
				description: designText,
				rejected: designCandidates
					.filter((c) => c.generated_voice_id !== chosen.generated_voice_id)
					.map((c) => c.generated_voice_id)
			});
			voices[persona] = { ...voices[persona], voice_id };
			saved = false;
			designOpen = '';
			designCandidates = [];
			await loadProviderVoices();
		} catch (e) {
			designError = e instanceof Error ? e.message : String(e);
		} finally {
			designBusy = false;
		}
	}

	async function loadProviderVoices() {
		voiceListError = '';
		try {
			providerVoices = await api.listProviderVoices();
		} catch (e) {
			// Not fatal: the form falls back to a free-text field.
			providerVoices = [];
			voiceListError = e instanceof Error ? e.message : String(e);
		}
	}

	/** Switching provider re-renders the form against that provider's OWN
	 * stored connection and voice blocks. Nothing is carried across — sending
	 * the previous provider's model/base_url would save them under the new
	 * one (which is how ElevenLabs ended up labelled gpt-4o-mini-tts). */
	function pickProvider(name: string) {
		provider = name;
		saved = false;
		const conn = connections[name];
		baseUrl = conn?.base_url ?? '';
		model = conn?.model ?? '';
		keySet = conn?.api_key_set ?? false;
		apiKey = '';
		gaps = conn?.gaps ?? {};
		// That provider's OWN stored blocks (falling back to its defaults),
		// never the previous provider's values and never blanks over data
		// that is still on the server.
		voices = Object.fromEntries(
			Object.keys(conn?.defaults ?? voices).map((persona) => [
				persona,
				{ ...(conn?.defaults?.[persona] ?? {}), ...(conn?.voices?.[persona] ?? {}) }
			])
		);
		providerVoices = [];
		voiceListError = '';
		designOpen = '';
		report();
		if (providers.find((x) => x.name === name)?.supports_listing) loadProviderVoices();
	}

	function applyVoicePreset(persona: string, voiceId: string) {
		const v = providerVoices.find((x) => x.id === voiceId);
		if (!v) return;
		voices[persona] = { ...voices[persona], ...v.settings, voice_id: v.id };
		saved = false;
	}

	function preview(url: string | null) {
		if (!url) return;
		previewAudio?.pause();
		previewAudio = new Audio(url);
		previewAudio.play().catch(() => {});
	}

	async function save() {
		error = '';
		try {
			const editable = Object.entries(voices).filter(([p]) => !managed(`voice_${p}`));
			await api.setTtsSettings({
				...(managed('provider') ? {} : { provider }),
				...(managed('base_url') ? {} : { base_url: baseUrl }),
				...(managed('model') ? {} : { model }),
				...(apiKey && !managed('api_key') ? { api_key: apiKey } : {}),
				voices: Object.fromEntries(editable)
			});
			apiKey = '';
			await refresh();
			saved = true;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
</script>

<p class="dim">
	Spoken readings, in each reader's own voice. Optional and separate from the text model. You
	can run one, both, or neither.
</p>

<div class="fld">
	<span>Provider {#if managed('provider')}<em class="dim">(managed)</em>{/if}</span>
	<div class="choices provider">
		{#each providers as prov (prov.name)}
			<button
				class="choice"
				class:selected={provider === prov.name}
				disabled={managed('provider')}
				onclick={() => pickProvider(prov.name)}
			>
				<strong>{prov.label}</strong>
				<small>{prov.default_model || 'any model'}</small>
			</button>
		{/each}
	</div>
</div>

{#if provider === 'openai'}
	<p class="dim">
		Any OpenAI-compatible <code>/audio/speech</code> endpoint. OpenAI:
		<code>https://api.openai.com/v1</code> with model <code>gpt-4o-mini-tts</code> (supports
		style instructions); self-hosted Kokoro (kokoro-fastapi):
		<code>http://kokoro:8880/v1</code> with model <code>kokoro</code> (pick a voice like
		<code>af_heart</code>; instructions are ignored). Leave the Base URL empty to turn the
		feature off. Audio buttons only appear when it's configured.
	</p>
{:else}
	<p class="dim">
		Base URL is optional. <code>{activeProvider?.default_base_url}</code> is used when blank.
		Voices are account-specific, so each reader needs one chosen below before its audio works.
		Your settings for the other provider are kept, not discarded.
	</p>
{/if}

<div class="conn">
	<label class="fld">
		<span>Base URL {#if managed('base_url')}<em class="dim">(managed)</em>{/if}</span>
		<input
			type="text"
			bind:value={baseUrl}
			oninput={() => (saved = false)}
			disabled={managed('base_url')}
			placeholder={activeProvider?.default_base_url || 'https://api.openai.com/v1'}
		/>
	</label>
	<label class="fld">
		<span>Model {#if managed('model')}<em class="dim">(managed)</em>{/if}</span>
		<input
			type="text"
			bind:value={model}
			oninput={() => (saved = false)}
			disabled={managed('model')}
			placeholder={activeProvider?.default_model || 'gpt-4o-mini-tts'}
		/>
	</label>
	<label class="fld">
		<span>
			API key
			{#if managed('api_key')}<em class="dim">(managed)</em>
			{:else if keySet}<em class="dim">(saved; leave blank to keep)</em>{/if}
		</span>
		<input
			type="password"
			bind:value={apiKey}
			oninput={() => (saved = false)}
			disabled={managed('api_key')}
			placeholder={keySet ? '••••••••' : 'sk-…'}
			autocomplete="off"
		/>
	</label>
</div>

<h3>Readers</h3>
{#if voiceListError}
	<p class="dim">
		Couldn't list voices from the provider. Enter ids by hand. ({voiceListError})
	</p>
{/if}
{#each Object.entries(voices) as [p, v] (p)}
	<fieldset class="voice">
		<legend>
			{p}
			{#if managed(`voice_${p}`)}<em class="dim">(managed)</em>{/if}
			{#if gaps[p]?.length}<em class="gap">(no voice set)</em>{/if}
		</legend>
		<!-- Rendered from the provider's declared fields, so a new
		     provider needs no changes here. -->
		{#each activeProvider?.fields ?? [] as f (f.key)}
			{#if f.kind === 'longtext'}
				<label class="fld">
					<span>{f.label}</span>
					<textarea
						rows="2"
						bind:value={v[f.key]}
						oninput={() => (saved = false)}
						disabled={managed(`voice_${p}`)}
					></textarea>
				</label>
			{:else if f.kind === 'bool'}
				<label class="fld inline">
					<input
						type="checkbox"
						bind:checked={v[f.key] as boolean}
						onchange={() => (saved = false)}
						disabled={managed(`voice_${p}`)}
					/>
					<span>{f.label}</span>
				</label>
			{:else if f.kind === 'slider'}
				<label class="fld">
					<span>{f.label} <em class="dim">{v[f.key]}</em></span>
					<input
						type="range"
						min={f.min ?? 0}
						max={f.max ?? 1}
						step={f.step ?? 0.05}
						bind:value={v[f.key]}
						oninput={() => (saved = false)}
						disabled={managed(`voice_${p}`)}
					/>
				</label>
			{:else if f.kind === 'number'}
				<label class="fld">
					<span>{f.label}</span>
					<input
						type="number"
						min={f.min ?? undefined}
						max={f.max ?? undefined}
						step={f.step ?? undefined}
						bind:value={v[f.key]}
						oninput={() => (saved = false)}
						disabled={managed(`voice_${p}`)}
					/>
				</label>
			{:else if f.required && providerVoices.length}
				<!-- The provider can enumerate voices: pick by name, audition
				     with the provider's own preview (costs nothing). -->
				<div class="fld">
					<span>{f.label}</span>
					<div class="voicepick">
						<select
							bind:value={v[f.key]}
							onchange={(e) => applyVoicePreset(p, (e.currentTarget as HTMLSelectElement).value)}
							disabled={managed(`voice_${p}`)}
						>
							<option value="">Choose a voice…</option>
							{#each providerVoices as pv (pv.id)}
								<option value={pv.id}>{pv.name}{pv.category ? ` (${pv.category})` : ''}</option>
							{/each}
						</select>
						{#each providerVoices.filter((x) => x.id === v[f.key]) as pv (pv.id)}
							<button class="audition" title="Preview this voice" onclick={() => preview(pv.preview_url)}
								>▶</button
							>
						{/each}
					</div>
					{#each providerVoices.filter((x) => x.id === v[f.key]) as pv (pv.id)}
						{#if Object.keys(pv.labels).length}
							<small class="dim">{Object.values(pv.labels).join(' · ')}</small>
						{/if}
					{/each}
				</div>
			{:else}
				<label class="fld">
					<span>{f.label}</span>
					<input
						type="text"
						bind:value={v[f.key]}
						oninput={() => (saved = false)}
						disabled={managed(`voice_${p}`)}
						placeholder={f.help ?? ''}
					/>
				</label>
			{/if}
		{/each}

		{#if activeProvider?.supports_design && !managed(`voice_${p}`)}
			{#if designOpen !== p}
				<button class="design-open" onclick={() => openDesign(p)}>
					✨ Design a voice from {p}'s description
				</button>
			{:else}
				<div class="design">
					<p class="dim">
						Built from how {p} is written to sound. Edit if you like, then generate candidates
						and keep the one that fits. Generating costs provider credits.
					</p>
					<textarea rows="5" bind:value={designText} disabled={designBusy}></textarea>
					<div class="designrow">
						<button onclick={runDesign} disabled={designBusy || designText.trim().length < 20}>
							{designBusy ? 'Designing…' : designCandidates.length ? 'Try again' : 'Generate candidates'}
						</button>
						<button onclick={() => (designOpen = '')} disabled={designBusy}>Cancel</button>
					</div>
					{#if designError}<p class="error">{designError}</p>{/if}
					{#each designCandidates as cand, i (cand.generated_voice_id)}
						<div class="candidate">
							<span class="dim"
								>Candidate {i + 1}{cand.duration_secs
									? ` · ${cand.duration_secs.toFixed(1)}s`
									: ''}</span
							>
							<!-- svelte-ignore a11y_media_has_caption -->
							<audio controls src={cand.audio}></audio>
							<button onclick={() => keepDesigned(p, cand)} disabled={designBusy}>
								Use this one
							</button>
						</div>
					{/each}
				</div>
			{/if}
		{/if}
	</fieldset>
{/each}
{#if managedFields.length}
	<p class="managed">
		Partly managed by <code>{configFile}</code>. Edit the config file rather than this page.
	</p>
{/if}
{#if error}<p class="error">{error}</p>{/if}
<button onclick={save} disabled={saved}>{saved ? 'Saved' : 'Save voice settings'}</button>

<style>
	.conn {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
		gap: 0 0.9rem;
	}

	h3 {
		font-size: 0.95rem;
		margin: 1.2rem 0 0.5rem;
	}

	.voice {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.6rem 0.9rem;
		margin-bottom: 0.8rem;
	}

	.voice legend {
		text-transform: capitalize;
		color: var(--gold);
		padding: 0 0.4rem;
	}

	.gap {
		color: var(--danger);
		font-style: normal;
		font-size: 0.85em;
	}

	/* Provider picker — the same card-button idiom as a home page's
	   choosers, laid out in a row since there are only ever a few. */
	.provider {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
		margin-top: 0.3rem;
	}

	.choice {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		text-align: left;
		gap: 0.15rem;
		padding: 0.5rem 1rem;
	}

	.choice small {
		color: var(--text-dim);
	}

	.choice.selected {
		border-color: var(--gold);
		background: var(--bg-card);
	}

	.voicepick {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}

	.voicepick select {
		flex: 1;
	}

	.audition {
		padding: 0.35rem 0.7rem;
		line-height: 1;
	}

	.design-open {
		margin-top: 0.4rem;
		font-size: 0.9rem;
	}

	.design {
		margin-top: 0.6rem;
		padding: 0.7rem;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
		background: var(--bg-card);
	}

	.design textarea {
		width: 100%;
	}

	.designrow {
		display: flex;
		gap: 0.6rem;
		margin-top: 0.6rem;
	}

	.candidate {
		display: flex;
		align-items: center;
		gap: 0.7rem;
		flex-wrap: wrap;
		margin-top: 0.7rem;
		padding-top: 0.7rem;
		border-top: 1px solid var(--border);
	}

	.candidate audio {
		height: 2rem;
		flex: 1;
		min-width: 12rem;
	}
</style>
