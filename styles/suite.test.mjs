// The suite stylesheet: every colour it uses is one of its own tokens, the
// shared classes are there, and an app can import it by its package name.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';

const here = import.meta.dirname;
const css = fs.readFileSync(path.join(here, 'suite.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

test('every var() it reads is a token it defines', () => {
	const root = css.match(/:root\s*\{([^}]*)\}/)[1];
	const defined = new Set([...root.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
	const used = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
	for (const v of used) assert.ok(defined.has(v), `${v} is used but not defined`);
});

test('the classes every page repeats are here', () => {
	for (const sel of ['.dim', '.error', '.danger', '.locktag', 'button:disabled', 'button.primary']) {
		assert.match(css, new RegExp(`(^|[\\s,}])${sel.replace(/[.]/g, '\\.')}[\\s,{:]`), sel);
	}
});

test('it is exported as fortunes-platform/styles.css', () => {
	const pkg = JSON.parse(fs.readFileSync(path.join(here, '..', 'package.json'), 'utf8'));
	const target = pkg.exports['./styles.css'];
	assert.ok(target, 'no ./styles.css export');
	assert.ok(fs.existsSync(path.join(here, '..', typeof target === 'string' ? target : target.default)));
});
