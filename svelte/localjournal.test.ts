// The demo's journal store, for a stand-in app whose readings are a spread
// of tiles: its own fields stored and handed back as given, a one-tile
// reading complete once its tile is read, the name from the storage prefix.
import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it } from 'vitest';
import { LocalJournal } from './localjournal';
import { useTestApp } from './testapp';

type Tiles = { spread: string; tiles: { n: number }[]; books?: string[] };

let n = 0;
const fresh = () =>
	new LocalJournal<Tiles>({
		name: `test-${Date.now()}-${n++}`,
		pieces: (r) => r.tiles.length,
		prepare: (f) => ({ ...f, books: [...new Set(f.books ?? [])].sort() })
	});

const tile = (n: number) => ({ n });
const three = { question: 'Why?', spread: 'three', tiles: [tile(1), tile(2), tile(3)] };

beforeEach(() => useTestApp());

describe('Journal readings', () => {
	it('saves a quick reading and reads it back as the API would send it', async () => {
		const j = fresh();
		const saved = await j.save({ ...three, notes: 'AI interpretation:\nhello' });
		expect(saved.id).toBeTypeOf('number');
		expect(saved).toMatchObject({
			owner: 'you',
			yours: true,
			visibility: 'private',
			shared_with: [],
			books: [],
			spread: 'three',
			tiles: [{ n: 1 }, { n: 2 }, { n: 3 }],
			notes: 'AI interpretation:\nhello',
			interpretation: { mode: null, status: 'none', focused: {}, comprehensive: null, done_positions: [] }
		});
		expect(await j.get(saved.id)).toEqual(saved);
		expect(await j.get(999)).toBeNull();
	});

	it('lists newest first and honours limit, without the detail-only fields', async () => {
		const j = fresh();
		const a = await j.save(three);
		const b = await j.save({ ...three, question: 'second' });
		const list = await j.list();
		expect(list.map((r) => r.id)).toEqual([b.id, a.id]);
		expect(list[0].interpretation).toEqual({ mode: null, status: 'none' });
		expect((await j.list(1)).map((r) => r.id)).toEqual([b.id]);
	});

	it('updates notes and deletes with its interpretation rows', async () => {
		const j = fresh();
		const r = await j.createGuided({ ...three, mode: 'cumulative' });
		expect(r.interpretation).toMatchObject({ mode: 'cumulative', status: 'in_progress' });
		expect((await j.updateNotes(r.id, 'kept'))?.notes).toBe('kept');
		expect((await j.updateNotes(r.id, undefined))?.notes).toBe('kept');
		await j.setFocused(r.id, 0, 'first card', 'sage');
		expect(await j.remove(r.id)).toBe(true);
		expect(await j.remove(r.id)).toBe(false);
		expect(await j.get(r.id)).toBeNull();
		expect(await j.count()).toBe(0);
	});

	it('records focused readings as done positions and completes on the whole picture', async () => {
		const j = fresh();
		const r = await j.createGuided({ ...three, mode: 'cumulative' });
		let v = await j.setFocused(r.id, 1, 'second', 'sage');
		expect(v?.interpretation).toMatchObject({ status: 'in_progress', focused: { '1': 'second' }, done_positions: [1] });
		v = await j.setFocused(r.id, 0, 'first', 'sage');
		expect(v?.interpretation.done_positions).toEqual([0, 1]);
		v = await j.setFocused(r.id, 0, 'first, retold', 'sage'); // a re-stream overwrites
		expect(v?.interpretation.focused).toEqual({ '0': 'first, retold', '1': 'second' });
		v = await j.setComprehensive(r.id, 'all together', 'sage');
		expect(v?.interpretation).toMatchObject({ status: 'complete', comprehensive: 'all together', done_positions: [0, 1] });
		expect(await j.setFocused(999, 0, 'ghost', null)).toBeNull();
	});

	it('a one-piece reading completes at its one focused reading', async () => {
		const j = fresh();
		const r = await j.createGuided({ ...three, spread: 'single', tiles: [tile(5)], mode: 'cumulative' });
		const v = await j.setFocused(r.id, 0, 'the one', 'imp');
		expect(v?.interpretation.status).toBe('complete');
	});

	it('exports everything in full and clears everything', async () => {
		const j = fresh();
		const r = await j.createGuided({ ...three, mode: 'cumulative' });
		await j.setFocused(r.id, 0, 'x', 'sage');
		await j.setSetting('deck', 'rws');
		const all = await j.all();
		expect(all[0].interpretation.focused).toEqual({ '0': 'x' });
		await j.clear();
		expect(await j.count()).toBe(0);
		expect(await j.allSettings()).toEqual({});
	});
});

describe('Journal settings', () => {
	it('is a string k/v store where an empty value deletes', async () => {
		const j = fresh();
		expect(await j.getSetting('deck')).toBe('');
		await j.setSetting('deck', 'rws');
		await j.setSetting('spread', 'single');
		expect(await j.allSettings()).toEqual({ deck: 'rws', spread: 'single' });
		await j.setSetting('deck', '');
		expect(await j.allSettings()).toEqual({ spread: 'single' });
	});
});

describe('the store', () => {
	it('is named after the app storage prefix, and keeps a page proxy out', async () => {
		useTestApp({ storage: 'testapp-store' });
		const j = new LocalJournal<Tiles>({ prepare: (f) => ({ ...f, books: [...new Set(f.books ?? [])].sort() }) });
		const proxy = new Proxy({ ...three, books: ['b', 'a', 'b'] }, {});
		const r = await j.save(proxy);
		expect(r.books).toEqual(['a', 'b']);
		const dbs = await indexedDB.databases();
		expect(dbs.map((d) => d.name)).toContain('testapp-store-demo');
	});
});
