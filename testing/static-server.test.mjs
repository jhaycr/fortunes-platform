// The stand-in static host behaves as the real one does for a single-page
// app: a file is served as itself, a directory as its index.html, and any
// other path gets the site's 404.html with status 404.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

const server = path.join(import.meta.dirname, 'static-server.mjs');

test('a file, a directory and a miss', async (t) => {
	const root = fs.mkdtempSync(path.join(os.tmpdir(), 'site-'));
	fs.writeFileSync(path.join(root, 'index.html'), 'home');
	fs.writeFileSync(path.join(root, '404.html'), 'shell');
	fs.mkdirSync(path.join(root, 'data'));
	fs.writeFileSync(path.join(root, 'data', 'v.json'), '{"v":1}');
	const port = 20000 + Math.floor(Math.random() * 20000);
	const proc = spawn(process.execPath, [server, root, String(port)], { stdio: 'ignore' });
	t.after(() => {
		proc.kill();
		fs.rmSync(root, { recursive: true, force: true });
	});
	const base = `http://127.0.0.1:${port}`;
	for (let i = 0; i < 50; i++) {
		try {
			if ((await fetch(`${base}/__health`)).ok) break;
		} catch {
			await new Promise((r) => setTimeout(r, 100));
		}
	}

	const file = await fetch(`${base}/data/v.json`);
	assert.equal(file.status, 200);
	assert.match(file.headers.get('content-type') ?? '', /application\/json/);
	assert.equal(await file.text(), '{"v":1}');

	const dir = await fetch(`${base}/`);
	assert.equal(await dir.text(), 'home');

	const miss = await fetch(`${base}/journal/12`);
	assert.equal(miss.status, 404);
	assert.equal(await miss.text(), 'shell');
});
