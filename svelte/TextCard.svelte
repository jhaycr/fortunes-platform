<script lang="ts">
	import { app } from './app';
	import type { CardStatus } from './settings';

	const api = app().api;
	const appName = app().short;

	let { onstatus }: { onstatus: (s: CardStatus) => void } = $props();

	let baseUrl = $state('');
	let model = $state('');
	let apiKey = $state('');
	let keySet = $state(false);
	let fromEnv = $state(false);
	let saved = $state(true);
	let error = $state('');
	let managedFields = $state<string[]>([]);
	let configFile = $state<string | null>(null);
	let configError = $state<string | null>(null);

	const managed = (f: string) => managedFields.includes(f);

	$effect(() => {
		refresh();
	});

	function report() {
		// The backend switches interpretation on as soon as a base URL exists
		// (interpret.config()); a blank model then fails at call time, so say so.
		const on = Boolean(baseUrl);
		onstatus({
			on,
			detail: on ? model || 'no model set' : 'not configured',
			warn: on && !model ? 'connected, but no model set' : undefined,
			configError
		});
	}

	async function refresh() {
		const s = await api.getLlmSettings();
		baseUrl = s.base_url;
		model = s.model;
		keySet = s.api_key_set;
		fromEnv = s.from_env;
		managedFields = s.managed;
		configFile = s.config_file;
		configError = s.config_error;
		report();
	}

	async function save() {
		error = '';
		try {
			// Don't send fields the config file owns; the API rejects them.
			const s = await api.setLlmSettings({
				...(managed('base_url') ? {} : { base_url: baseUrl }),
				...(managed('model') ? {} : { model: model }),
				...(apiKey && !managed('api_key') ? { api_key: apiKey } : {})
			});
			apiKey = '';
			keySet = s.api_key_set;
			fromEnv = s.from_env;
			managedFields = s.managed;
			saved = true;
			report();
		} catch (e) {
			error = String(e);
		}
	}
</script>

<p class="dim">
	Written interpretations come from any OpenAI-compatible endpoint. Leave this blank and
	{appName} simply doesn't offer them. Everything else still works.
</p>
<p class="dim examples">
	OpenRouter: <code>https://openrouter.ai/api/v1</code> · <code>minimax/minimax-m2</code><br />
	Anthropic: <code>https://api.anthropic.com/v1</code> · <code>claude-haiku-4-5</code><br />
	OpenAI: <code>https://api.openai.com/v1</code> · <code>gpt-4o-mini</code><br />
	Local Ollama: <code>http://ollama:11434/v1</code> · <code>llama3.1</code>
</p>
<label class="fld">
	<span>Base URL {#if managed('base_url')}<em class="dim">(managed)</em>{/if}</span>
	<input
		type="text"
		bind:value={baseUrl}
		oninput={() => (saved = false)}
		disabled={managed('base_url')}
		placeholder="https://openrouter.ai/api/v1"
	/>
</label>
<label class="fld">
	<span>Model {#if managed('model')}<em class="dim">(managed)</em>{/if}</span>
	<input
		type="text"
		bind:value={model}
		oninput={() => (saved = false)}
		disabled={managed('model')}
		placeholder="minimax/minimax-m2"
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
<p class="dim fineprint">The API key is encrypted at rest and never shown again after saving.</p>
{#if managedFields.length}
	<p class="managed">
		{managedFields.length === 3 ? 'Managed' : 'Partly managed'} by <code>{configFile}</code>.
		Edit the config file rather than this page.
	</p>
{:else if fromEnv}
	<p class="dim">Currently configured from environment variables; saving here overrides them.</p>
{/if}
{#if error}<p class="error">{error}</p>{/if}
{#if managedFields.length < 3}
	<button onclick={save} disabled={saved}>{saved ? 'Saved' : 'Save connection'}</button>
{/if}

<style>
	.examples {
		font-size: 0.88rem;
		line-height: 1.9;
	}

	.fineprint {
		font-size: 0.85rem;
		margin-top: -0.4rem;
	}
</style>
