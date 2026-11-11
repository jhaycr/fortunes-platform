// Component tests for the shared Svelte half: compiled by the Svelte plugin,
// rendered into jsdom. `npm test` runs them after the type check.
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [svelte()],
	resolve: { conditions: ['browser'] },
	test: {
		include: ['svelte/**/*.test.ts'],
		environment: 'jsdom'
	}
});
