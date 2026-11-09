// A stand-in for the static host, GitHub Pages: a directory URL serves its
// index.html, and any path with no file behind it serves the site's 404.html
// WITH status 404 — the only SPA fallback GitHub Pages has, so the build
// copies the app shell to 404.html and deep links boot the app from it. A
// /__health probe answers even when nothing has been built yet, so the
// Playwright lane can always start and only the static projects notice a
// missing build.
//
//   node node_modules/fortunes-platform/testing/static-server.mjs [dir=build-static] [port=8766]
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const root = path.resolve(process.argv[2] ?? 'build-static');
const port = Number(process.argv[3] ?? 8766);
const types = {
	'.html': 'text/html; charset=utf-8',
	'.js': 'text/javascript',
	'.css': 'text/css',
	'.json': 'application/json',
	'.webmanifest': 'application/manifest+json',
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.webp': 'image/webp',
	'.gif': 'image/gif',
	'.svg': 'image/svg+xml',
	'.ico': 'image/x-icon',
	'.txt': 'text/plain',
	'.woff2': 'font/woff2'
};

http
	.createServer((req, res) => {
		const pathname = decodeURIComponent(new URL(req.url, 'http://x').pathname);
		if (pathname === '/__health') return res.writeHead(200).end('ok');
		if (pathname.startsWith('/api/')) return res.writeHead(404).end('no API on a static host');
		let file = path.join(root, pathname);
		if (!file.startsWith(root)) return res.writeHead(403).end();
		if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
		let status = 200;
		if (!(fs.existsSync(file) && fs.statSync(file).isFile())) {
			status = 404;
			file = path.join(root, '404.html');
			if (!fs.existsSync(file)) return res.writeHead(404).end('no build-static/404.html: run npm run export:static && npm run build:static');
		}
		res.writeHead(status, {
			'content-type': types[path.extname(file)] ?? 'application/octet-stream',
			'cache-control': 'no-store'
		});
		fs.createReadStream(file).pipe(res);
	})
	.listen(port, '127.0.0.1');
