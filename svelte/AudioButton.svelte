<script lang="ts" module>
	// One shared element so two pieces never speak over each other, and so the
	// first user-gesture play unlocks programmatic playback (iOS allows later
	// .play() calls on an element that has already played from a gesture).
	let audio: HTMLAudioElement | null = null;
	let stopCurrent: (() => void) | null = null;
	let unlocked = false;

	export function audioUnlocked(): boolean {
		return unlocked;
	}
</script>

<script lang="ts">
	import AudioLoader from './AudioLoader.svelte';

	// `src` may be a URL (persisted pieces) or an async provider (ephemeral text
	// -> object URL). `label` is for screen readers / the hover tooltip.
	let {
		src,
		label = 'Read aloud',
		onended
	}: {
		src: string | (() => Promise<string>);
		label?: string;
		onended?: () => void;
	} = $props();

	let state_ = $state<'idle' | 'loading' | 'playing' | 'error'>('idle');
	let errorMsg = $state('');
	let showWhy = $state(false);

	/** Why a URL would not play. An <audio> element reports only that it
	 * failed, so ask the URL itself: the server answers an error with a JSON
	 * `detail` (a refused provider request costs nothing to repeat). */
	async function reasonFor(url: string): Promise<string> {
		if (!url.startsWith('blob:')) {
			try {
				const res = await fetch(url);
				if (!res.ok) {
					const body = await res.json().catch(() => null);
					if (typeof body?.detail === 'string' && body.detail) return body.detail;
					return `The voice service answered ${res.status}.`;
				}
			} catch {
				return 'Could not reach the server.';
			}
		}
		return 'The audio could not be played.';
	}

	function fail(message: string) {
		state_ = 'error';
		errorMsg = message || 'The audio could not be played.';
		showWhy = false;
	}

	function stop() {
		if (audio) {
			audio.pause();
			audio.onended = null;
			audio.onerror = null;
		}
		if (stopCurrent === reset) stopCurrent = null;
		state_ = 'idle';
	}

	function reset() {
		state_ = 'idle';
	}

	/** Start playback. Exported so auto-read can trigger it programmatically. */
	export async function play(): Promise<void> {
		if (state_ === 'playing' || state_ === 'loading') return;
		stopCurrent?.(); // silence whatever else is speaking
		stopCurrent = reset;
		state_ = 'loading';
		errorMsg = '';
		showWhy = false;
		try {
			const url = typeof src === 'string' ? src : await src();
			audio ??= new Audio();
			audio.src = url;
			audio.onended = () => {
				state_ = 'idle';
				if (stopCurrent === reset) stopCurrent = null;
				onended?.();
			};
			audio.onerror = async () => {
				if (stopCurrent === reset) stopCurrent = null;
				fail(await reasonFor(url));
			};
			await audio.play();
			unlocked = true;
			state_ = 'playing';
		} catch (e) {
			// NotAllowedError = autoplay blocked pre-gesture: quietly back to idle
			// so the visible button invites the tap that will unlock audio.
			if ((e as Error).name === 'NotAllowedError') state_ = 'idle';
			else if ((e as Error).name === 'NotSupportedError' && typeof src === 'string') fail(await reasonFor(src));
			else fail(e instanceof Error ? e.message : String(e));
			if (stopCurrent === reset) stopCurrent = null;
		}
	}

	function toggle() {
		if (state_ === 'playing') stop();
		else if (state_ === 'error') showWhy = !showWhy; // a tap says why (phones have no hover)
		else play();
	}

	function retry() {
		state_ = 'idle';
		play();
	}

	$effect(() => () => {
		// leaving the page mid-speech: stop rather than talk over the next view
		if (state_ === 'playing' || state_ === 'loading') stop();
	});
</script>

<button
	class="audio"
	class:playing={state_ === 'playing'}
	class:err={state_ === 'error'}
	onclick={toggle}
	title={state_ === 'error' ? errorMsg : label}
	aria-label={state_ === 'error' ? `${label}: failed. Show why` : label}
	aria-expanded={state_ === 'error' ? showWhy : undefined}
	disabled={state_ === 'loading'}
	aria-busy={state_ === 'loading'}
>
	{#if state_ === 'loading'}<AudioLoader />
	{:else if state_ === 'playing'}
		<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4.5" y="4.5" width="7" height="7" rx="1.2" fill="currentColor" /></svg>
	{:else if state_ === 'error'}
		<svg viewBox="0 0 16 16" aria-hidden="true"
			><path d="M8 2.6 14.2 13.4H1.8Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /><path
				d="M8 6.6v3.1"
				stroke="currentColor"
				stroke-width="1.4"
				stroke-linecap="round"
			/><circle cx="8" cy="11.6" r="0.8" fill="currentColor" /></svg
		>
	{:else}
		<svg viewBox="0 0 16 16" aria-hidden="true"
			><path d="M2.6 6.1h2.5L8.6 3.2v9.6L5.1 9.9H2.6Z" fill="currentColor" /><path
				d="M10.9 5.6a3.3 3.3 0 0 1 0 4.8M12.7 3.9a5.7 5.7 0 0 1 0 8.2"
				fill="none"
				stroke="currentColor"
				stroke-width="1.3"
				stroke-linecap="round"
			/></svg
		>
	{/if}
</button>
{#if state_ === 'error' && showWhy}
	<span class="why" role="status">
		{errorMsg}
		<button class="retry" onclick={retry}>Try again</button>
	</span>
{/if}

<style>
	/* A fixed-size pill, so changing state never nudges the heading. Its
	   centre sits on the capitals' visual centre (vertical-align: middle aims
	   at half the x-height, which reads low beside a capitalised heading);
	   the lane checks this within a pixel. */
	.audio {
		all: unset;
		box-sizing: border-box;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		font-size: 0.85em;
		line-height: 1;
		width: 2.3em;
		height: 1.35em;
		border: 1px solid var(--border);
		border-radius: 999px;
		color: var(--text-dim);
		vertical-align: middle;
		position: relative;
		top: -0.12em;
	}

	.audio svg {
		width: 1.05em;
		height: 1.05em;
		display: block;
	}

	.audio:hover {
		border-color: var(--gold);
		color: var(--gold);
	}

	.audio.playing {
		border-color: var(--gold);
		color: var(--gold);
	}

	.audio.err {
		border-color: var(--danger);
		color: var(--danger);
	}

	.why {
		display: block;
		margin: 0.35rem 0 0.2rem;
		font-size: 0.8rem;
		font-weight: normal;
		line-height: 1.4;
		color: var(--danger);
		overflow-wrap: anywhere;
	}

	.retry {
		all: unset;
		cursor: pointer;
		margin-left: 0.4rem;
		color: var(--gold);
		text-decoration: underline;
	}

	.audio:disabled {
		cursor: wait;
	}
</style>
