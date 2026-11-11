<script lang="ts">
	// Waiting on a reader: the audio button's loading animation, larger, with
	// the words kept for screen readers. The same animation as the audio
	// button's, as the app chose it.
	import AudioLoader from './AudioLoader.svelte';

	let { label }: { label: string } = $props();
</script>

<span class="waiting" role="status"><AudioLoader /><span class="sr">{label}</span></span>

<style>
	.waiting {
		display: inline-flex;
		align-items: center;
		font-size: 1.6rem;
		line-height: 1;
		min-height: 1.5rem;
	}

	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}
</style>
