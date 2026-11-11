<script lang="ts">
	// "Your AI key": the public demo's one AI setting. The visitor connects
	// their own OpenRouter key — one button through OpenRouter's consent page
	// (OAuth PKCE; the key it mints is theirs to cap or revoke), or a pasted
	// key — and it lives in this browser only, sent to nothing but the
	// provider. Session storage by default; "remember on this device" opts
	// into localStorage.
	//
	// The app hands in what is its own: what works without a key, the
	// paragraph on its readers' voices and their price (`voices`), and short
	// samples of those voices (`samples`, each a `<span class="sample">`); and
	// the page's URL with a way to replace it (SvelteKit's page.url and
	// replaceState), for the return from OpenRouter's consent page.
	import { onMount, untrack, type Snippet } from 'svelte';
	import { app, storagePrefix } from './app';
	import { DEFAULT_MODEL, OPENROUTER, RECOMMENDED_MODEL, forgetKey, getLlmPrefs, maskKey, setBaseUrl, setKey, setModel } from './keystore';
	import {
		authorizeUrl,
		exchangeCode,
		keyInfo,
		keySettingsUrl,
		listModels,
		llmComplete,
		pkcePair,
		type KeyInfo,
		type ModelInfo
	} from './llm';

	let {
		url,
		replace,
		withoutAi = app().words.withoutAi,
		voices,
		samples
	}: {
		url: URL;
		replace: (href: string) => void;
		/** What works without a key, as the subject of "... all work without it". */
		withoutAi?: string;
		voices?: Snippet;
		samples?: Snippet;
	} = $props();

	// the app as a sentence names it
	const APP_NAME = app().short;
	const PKCE = `${storagePrefix()}.pkce`; // sessionStorage: verifier + state + remember, across the redirect

	let prefs = $state(getLlmPrefs());
	const connected = $derived(!!prefs.key);
	const isOpenRouter = $derived(prefs.baseUrl === OPENROUTER);

	let remember = $state(false);
	let pasted = $state('');
	let busy = $state('');
	let error = $state('');
	let notice = $state('');
	let info = $state<KeyInfo | null>(null);
	let models = $state<ModelInfo[]>([]);
	let settingsUrl = $state('');
	let customModel = $state(false);

	function refresh() {
		prefs = getLlmPrefs();
		remember = prefs.remembered;
	}

	// ---- connect ------------------------------------------------------------

	async function connect() {
		error = '';
		busy = 'connect';
		try {
			const { verifier, challenge } = await pkcePair();
			const state = crypto.randomUUID();
			sessionStorage.setItem(PKCE, JSON.stringify({ verifier, state, remember }));
			window.location.href = authorizeUrl(`${location.origin}/settings`, challenge, state);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			busy = '';
		}
	}

	/** Back from the consent page: ?code= (+ our state) → exchange → store. */
	async function finishConnect(code: string, state: string | null) {
		let saved: { verifier: string; state: string; remember: boolean } | null = null;
		try {
			saved = JSON.parse(sessionStorage.getItem(PKCE) ?? 'null');
		} catch {
			/* no handshake in progress */
		}
		sessionStorage.removeItem(PKCE);
		replace('/settings?tab=ai'); // the code is single-use: never leave it in the URL
		if (!saved || (state && saved.state !== state)) {
			error = 'That sign-in did not start here. Connect again from this page.';
			return;
		}
		busy = 'exchange';
		try {
			setKey(await exchangeCode(code, saved.verifier), saved.remember);
			refresh();
			notice = `Connected. The key OpenRouter made for ${APP_NAME} is yours: cap or revoke it any time.`;
			await loadAccount();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = '';
		}
	}

	function paste() {
		const k = pasted.trim();
		if (!k) return;
		error = '';
		setKey(k, remember);
		pasted = '';
		refresh();
		loadAccount();
	}

	function forget() {
		forgetKey();
		info = null;
		models = [];
		notice = '';
		error = '';
		refresh();
	}

	// ---- account + models ---------------------------------------------------

	async function loadAccount() {
		if (!prefs.key) return;
		settingsUrl = isOpenRouter ? await keySettingsUrl(prefs.key) : '';
		if (!isOpenRouter) return; // another OpenAI-compatible host: no /key, no /models
		try {
			info = await keyInfo(prefs);
		} catch (e) {
			info = null;
			error = e instanceof Error ? e.message : String(e);
		}
		try {
			const all = await listModels(prefs.baseUrl);
			const free = all.filter((m) => m.free).slice(0, 6);
			const cheap = all.filter((m) => !m.free && m.pricePerM <= 0.5 && m.id !== RECOMMENDED_MODEL).slice(0, 6);
			const rec = all.filter((m) => m.id === RECOMMENDED_MODEL); // empty if OpenRouter ever drops it
			models = [...rec, ...free, ...cheap];
		} catch {
			models = []; // the picker degrades to a text field
		}
		customModel = !models.some((m) => m.id === prefs.model);
	}

	function pickModel(id: string) {
		if (id === '__custom') {
			customModel = true;
			return;
		}
		customModel = false;
		setModel(id);
		refresh();
	}

	async function testKey() {
		error = '';
		notice = '';
		busy = 'test';
		try {
			const text = await llmComplete(
				{ baseUrl: prefs.baseUrl, model: prefs.model, key: prefs.key, maxTokens: 8 },
				'Reply with the single word OK.',
				'Ready?'
			);
			notice = `The key works: ${prefs.model} answered${text ? ` “${text.slice(0, 40)}”` : ''}.`;
			if (isOpenRouter) info = await keyInfo(prefs).catch(() => info);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = '';
		}
	}

	const money = (n: number | null | undefined) => (n == null ? null : `$${n.toFixed(2)}`);

	onMount(() => {
		const { code, state } = untrack(() => ({ code: url.searchParams.get('code'), state: url.searchParams.get('state') }));
		if (code) finishConnect(code, state);
		else if (prefs.key) loadAccount();
	});
</script>

<section class="group">
	<h2>Your AI key <em class="dim optional">(optional)</em></h2>
	<p class="dim">
		You don't need this. {withoutAi} all work without it. A key only adds written
		interpretations and guided readings, from a language
		model you use free or pay for yourself through
		<a href="https://openrouter.ai" target="_blank" rel="noopener">OpenRouter</a>. The key stays in
		this browser and is sent to nothing but OpenRouter.
	</p>
	{@render voices?.()}

	{#if !connected}
		<div class="row">
			<button class="primary" onclick={connect} disabled={busy !== ''}>
				{busy === 'exchange' ? 'Connecting…' : 'Connect OpenRouter'}
			</button>
			<span class="dim">A consent page on openrouter.ai makes a key just for {APP_NAME}.</span>
		</div>
		<details class="alt">
			<summary>Or paste a key</summary>
			<div class="row">
				<input type="password" placeholder="sk-or-…" bind:value={pasted} onkeydown={(e) => e.key === 'Enter' && paste()} />
				<button onclick={paste} disabled={!pasted.trim()}>Use this key</button>
			</div>
		</details>
		<label class="checkline">
			<input type="checkbox" bind:checked={remember} />
			<span>Remember on this device <em class="dim">(otherwise the key is forgotten when this tab closes)</em></span>
		</label>
	{:else}
		<p class="status">
			<span class="dot" aria-hidden="true">●</span>
			Connected: <code>{maskKey(prefs.key)}</code>
			<span class="dim">· {prefs.remembered ? 'remembered on this device' : 'this tab only'}</span>
		</p>
		{#if info}
			<p class="dim fine">
				{#if info.limit_remaining != null}{money(info.limit_remaining)} left of a {money(info.limit)} cap{:else if info.usage != null}{money(info.usage)} used, no cap set{/if}
				{#if info.free_model_daily_requests}· {info.free_model_daily_requests.remaining} of {info.free_model_daily_requests.limit} free requests left today{/if}
			</p>
		{/if}

		<label class="fld">
			<span>Model</span>
			{#if models.length}
				<select value={customModel ? '__custom' : prefs.model} onchange={(e) => pickModel(e.currentTarget.value)}>
					{#each models.filter((m) => m.id === RECOMMENDED_MODEL) as m (m.id)}
						<optgroup label="Recommended: balanced, what this app's own instance uses">
							<option value={m.id}>{m.name} · ${m.pricePerM.toFixed(2)}/M · about $0.001 a reading</option>
						</optgroup>
					{/each}
					<optgroup label="Free (shared daily quota)">
						{#each models.filter((m) => m.free) as m (m.id)}<option value={m.id}>{m.name}</option>{/each}
					</optgroup>
					<optgroup label="Cheap (well under a cent per reading)">
						{#each models.filter((m) => !m.free && m.id !== RECOMMENDED_MODEL) as m (m.id)}<option value={m.id}>{m.name} · ${m.pricePerM.toFixed(2)}/M</option>{/each}
					</optgroup>
					<option value="__custom">Other…</option>
				</select>
			{/if}
			{#if customModel || !models.length}
				<input
					type="text"
					value={prefs.model}
					placeholder={DEFAULT_MODEL}
					onchange={(e) => {
						setModel(e.currentTarget.value || DEFAULT_MODEL);
						refresh();
					}}
				/>
			{/if}
		</label>

		{#if samples}
			<div class="fld">
				<span>The readers' voices <em class="dim">(a short sample each)</em></span>
				<div class="row">
					{@render samples()}
				</div>
			</div>
		{/if}

		<div class="row">
			<button onclick={testKey} disabled={busy !== ''}>{busy === 'test' ? 'Testing…' : 'Test this key'}</button>
			<button class="danger" onclick={forget}>Forget key</button>
			{#if settingsUrl}
				<a href={settingsUrl} target="_blank" rel="noopener" class="dim">Manage or revoke on openrouter.ai ↗</a>
			{/if}
		</div>

		<details class="alt">
			<summary>Advanced: another OpenAI-compatible endpoint</summary>
			<label class="fld">
				<span>
					Base URL <em class="dim">(must allow browser requests; OpenRouter does. The public demo's
					security policy only lets the key travel to openrouter.ai, so this is for your own
					deployment.)</em>
				</span>
				<input
					type="text"
					value={prefs.baseUrl}
					onchange={(e) => {
						setBaseUrl(e.currentTarget.value || OPENROUTER);
						refresh();
						loadAccount();
					}}
				/>
			</label>
		</details>
	{/if}

	{#if notice}<p class="ok">{notice}</p>{/if}
	{#if error}<p class="error">{error}</p>{/if}
</section>

<style>
	.group {
		margin-bottom: 1.6rem;
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
		align-items: center;
		margin: 0.6rem 0;
	}

	.row input {
		flex: 1 1 14rem;
		width: auto;
	}

	.fld {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin: 0.8rem 0;
	}

	.fld span {
		color: var(--text-dim);
		font-size: 0.9rem;
	}

	.fld .row {
		margin: 0;
		gap: 0.6rem 1.4rem;
	}

	.fld :global(.sample) {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--text);
		font-size: 1rem;
	}

	.checkline {
		display: flex;
		gap: 0.5rem;
		align-items: flex-start;
		margin-top: 0.6rem;
	}

	.checkline input {
		margin-top: 0.3rem;
	}

	.alt {
		margin: 0.6rem 0;
	}

	.alt summary {
		cursor: pointer;
		color: var(--text-dim);
	}

	.status .dot {
		color: var(--gold);
		font-size: 0.7rem;
		vertical-align: middle;
	}

	.fine {
		font-size: 0.9rem;
	}

	.ok {
		color: var(--gold);
	}

	.optional {
		font-size: 0.8em;
		font-weight: normal;
	}
</style>
