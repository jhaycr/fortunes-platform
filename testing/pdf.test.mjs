// The browser tests' PDF is well formed: every cross-reference points at its
// object, the trailer points at the cross-references, and the words are there.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { textPdf } from './pdf.js';

test('a two-page PDF with its words in it', () => {
	const bytes = textPdf([[[18, 'A Title (with brackets)'], [0, ''], [10.5, 'Body text, Mêng.']], [[10.5, 'Page two.']]]);
	const pdf = new TextDecoder('latin1').decode(bytes);
	assert.ok(pdf.startsWith('%PDF-1.4\n') && pdf.endsWith('%%EOF\n'));
	const xref = Number(pdf.match(/startxref\n(\d+)\n/)[1]);
	assert.equal(pdf.slice(xref, xref + 4), 'xref');
	const offsets = [...pdf.slice(xref).matchAll(/^(\d{10}) 00000 n $/gm)].map((m) => Number(m[1]));
	assert.equal(offsets.length, 8); // catalog, pages, two fonts, and a content and a page for each page
	offsets.forEach((off, i) => assert.ok(pdf.startsWith(`${i + 1} 0 obj\n`, off), `object ${i + 1}`));
	assert.match(pdf, /\(A Title \\\(with brackets\\\)\) Tj/);
	assert.match(pdf, /\(Body text, M\?ng\.\) Tj/); // plain ASCII only
	assert.match(pdf, /\/Count 2 >>/);
	assert.equal(bytes.length, pdf.length);
});
