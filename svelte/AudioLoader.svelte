<script lang="ts">
	// The audio button's loading state. Understated on purpose: brightness or
	// one slow turn, never a busy spinner. Under prefers-reduced-motion every
	// variant holds still. The app picks the variant (`loader` in configure).
	import { loaderKind, type LoaderKind } from './app';

	let { kind = loaderKind() }: { kind?: LoaderKind } = $props();
</script>

{#if kind === 'card'}
	<span class="card" aria-hidden="true"><span class="turn"><i class="face">✦</i><i class="back"></i></span></span>
{:else if kind === 'breath'}
	<span class="breath" aria-hidden="true">✦</span>
{:else}
	<span class="twinkle" aria-hidden="true"><i>✦</i><i>✦</i><i>✦</i></span>
{/if}

<style>
	/* twinkle: three small stars catch the light in turn. Brightness only. */
	.twinkle {
		display: inline-flex;
		gap: 0.12em;
		font-size: 0.62em;
		color: var(--gold);
		vertical-align: 0.1em;
	}

	.twinkle i {
		font-style: normal;
		opacity: 0.25;
		animation: twinkle 1.4s ease-in-out infinite;
	}

	.twinkle i:nth-child(2) {
		animation-delay: 0.22s;
	}

	.twinkle i:nth-child(3) {
		animation-delay: 0.44s;
	}

	@keyframes twinkle {
		0%,
		60%,
		100% {
			opacity: 0.25;
			text-shadow: none;
		}
		25% {
			opacity: 1;
			text-shadow: 0 0 0.35em var(--gold);
		}
	}

	/* card: a tiny card turns face down and back up, resting between turns. */
	.card {
		display: inline-block;
		width: 0.62em;
		height: 0.9em;
		vertical-align: -0.08em;
		perspective: 3em;
	}

	.turn {
		position: relative;
		display: block;
		width: 100%;
		height: 100%;
		transform-style: preserve-3d;
		animation: turn 2.4s ease-in-out infinite;
	}

	.face,
	.back {
		position: absolute;
		inset: 0;
		border: 1px solid var(--gold);
		border-radius: 0.1em;
		backface-visibility: hidden;
	}

	.face {
		display: grid;
		place-items: center;
		font-style: normal;
		font-size: 0.5em;
		line-height: 1;
		color: var(--gold);
	}

	.back {
		transform: rotateY(180deg);
		background: repeating-linear-gradient(45deg, transparent 0 0.08em, color-mix(in srgb, var(--gold) 45%, transparent) 0.08em 0.12em);
	}

	@keyframes turn {
		0%,
		20% {
			transform: rotateY(0deg);
		}
		45%,
		70% {
			transform: rotateY(180deg);
		}
		95%,
		100% {
			transform: rotateY(360deg);
		}
	}

	/* breath: one star slowly brightens and dims. */
	.breath {
		display: inline-block;
		font-size: 0.8em;
		color: var(--gold);
		animation: breath 1.8s ease-in-out infinite;
	}

	@keyframes breath {
		0%,
		100% {
			opacity: 0.3;
			text-shadow: none;
		}
		50% {
			opacity: 1;
			text-shadow: 0 0 0.4em var(--gold);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.twinkle i,
		.turn,
		.breath {
			animation: none;
		}
		.twinkle i,
		.breath {
			opacity: 0.55;
		}
	}
</style>
