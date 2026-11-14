// A small PDF with a text layer, written by hand for a library's browser tests
// (the Python half's fortunes_platform/testing/pdfgen.py is its twin, for the
// import's own tests). Helvetica, plain ASCII: the words are the test's own,
// never taken from a book. Built to pdf.js and pdf.d.ts by `npm run build`;
// edit this file, not those.
//
// It returns bytes; Playwright's setInputFiles takes them as
// `buffer: Buffer.from(textPdf(...))`.

/** A page: (font size, text) lines top to bottom; a size of 0 is a blank line. */
export type Page = [number, string][];

export const PAGE_WIDTH = 432; // a 6 x 9 inch trade paperback
export const PAGE_HEIGHT = 648;

/** A PDF whose text layer is `pages`, set in Helvetica, bold from 13 pt up. */
export function textPdf(pages: Page[]): Uint8Array {
	const ascii = (s: string) => s.replace(/[^\x20-\x7e]/g, '?');
	const objects: string[] = [];
	const add = (body: string) => objects.push(body);
	add(''); // 1 catalog
	add(''); // 2 pages
	add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'); // 3
	add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>'); // 4
	const kids: number[] = [];
	for (const lines of pages) {
		let y = PAGE_HEIGHT - 54;
		const ops: string[] = [];
		for (const [size, text] of lines) {
			if (!size) {
				y -= 14;
				continue;
			}
			y -= size * 1.35;
			const esc = ascii(text).replace(/[\\()]/g, (c) => '\\' + c);
			ops.push(`BT /${size >= 13 ? 'F2' : 'F1'} ${size} Tf 54 ${y.toFixed(1)} Td (${esc}) Tj ET`);
		}
		const stream = ops.join('\n');
		add(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
		const content = objects.length;
		add(
			`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] /Contents ${content} 0 R ` +
				'/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>'
		);
		kids.push(objects.length);
	}
	objects[0] = '<< /Type /Catalog /Pages 2 0 R >>';
	objects[1] = `<< /Type /Pages /Kids [${kids.map((k) => `${k} 0 R`).join(' ')}] /Count ${kids.length} >>`;
	let out = '%PDF-1.4\n';
	const offsets: number[] = [];
	objects.forEach((body, i) => {
		offsets.push(out.length);
		out += `${i + 1} 0 obj\n${body}\nendobj\n`;
	});
	const xref = out.length;
	out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
	for (const off of offsets) out += `${String(off).padStart(10, '0')} 00000 n \n`;
	out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
	return new TextEncoder().encode(out); // all ASCII, so one byte a character
}
