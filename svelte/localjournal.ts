// The browser-local journal of a public demo: IndexedDB standing in for the
// server's SQLite (fortunes_platform/db.py). Same rows, same shapes —
// `readings` plus a child `interpretations` table keyed (reading_id,
// position) with -1 for the whole reading — so the pages see exactly what
// /api/readings would send.
//
// A reading's own fields (a cast's method and lines; a spread's deck and
// cards) are the app's: `F`, stored and handed back as given. The database
// is named after the app's storage prefix (`<prefix>-demo`).
import { openDB, type DBSchema, type IDBPDatabase } from 'idb';
import { storagePrefix } from './app';

export type InterpretationMode = 'isolated' | 'cumulative' | 'single';
export type InterpretationStatus = 'none' | 'in_progress' | 'complete';

export const OWNER = 'you';
export const POSITION_WHOLE = -1;

export interface StoredBase {
	id?: number;
	owner: string;
	created_at: number;
	question: string | null;
	notes: string;
	visibility: 'private';
	interpretation_mode: InterpretationMode | null;
	interpretation_status: InterpretationStatus;
}

export type StoredReading<F extends object = object> = StoredBase & F;

/** A reading as the API presents it, with the app's own fields. */
export type ReadingView<F extends object = object> = Omit<StoredBase, 'id' | 'interpretation_mode' | 'interpretation_status'> &
	F & {
		id: number;
		shared_with: string[];
		yours: true;
		interpretation: {
			mode: InterpretationMode | null;
			status: InterpretationStatus;
			focused?: Record<string, string>;
			comprehensive?: string | null;
			done_positions?: number[];
			personas?: Record<string, string | null>;
		};
	};

export interface StoredInterpretation {
	reading_id: number;
	position: number;
	kind: 'focused' | 'comprehensive';
	text: string;
	persona: string | null;
	created_at: number;
	updated_at: number;
}

interface Schema extends DBSchema {
	readings: { key: number; value: StoredReading; indexes: { created_at: number } };
	interpretations: { key: [number, number]; value: StoredInterpretation; indexes: { reading_id: number } };
	settings: { key: string; value: { key: string; value: string } };
}

const now = () => Math.floor(Date.now() / 1000);

/** db.py's _reading_dict: the row as the API presents it. With `rows` the
 * per-piece texts are folded in (a single-reading fetch); without, only the
 * mode and status (the list view). */
export function view<F extends object>(r: StoredReading<F>, rows?: StoredInterpretation[]): ReadingView<F> {
	const { id, interpretation_mode, interpretation_status, ...rest } = r;
	const interpretation: ReadingView['interpretation'] = {
		mode: interpretation_mode,
		status: interpretation_status || 'none'
	};
	if (rows) {
		const focused: Record<string, string> = {};
		let comprehensive: string | null = null;
		const done: number[] = [];
		const personas: Record<string, string | null> = {};
		for (const ir of [...rows].sort((a, b) => a.position - b.position)) {
			personas[String(ir.position)] = ir.persona;
			if (ir.kind === 'comprehensive') comprehensive = ir.text;
			else {
				focused[String(ir.position)] = ir.text;
				done.push(ir.position);
			}
		}
		Object.assign(interpretation, { focused, comprehensive, done_positions: done, personas });
	}
	return { ...rest, id: id as number, shared_with: [], yours: true, interpretation } as ReadingView<F>;
}

export type NewReading<F extends object> = F & {
	question: string | null;
	notes?: string;
};

export interface LocalJournalOptions<F extends object> {
	/** The IndexedDB name; `<storage prefix>-demo` by default. */
	name?: string;
	/** How many pieces a reading's guided interpretation reads: a reading of
	 * one piece completes when that piece is read. */
	pieces?: (r: StoredReading<F>) => number;
	/** The app's own fields as stored, from what a page handed over. */
	prepare?: (input: F) => F;
}

export class LocalJournal<F extends object, V = ReadingView<F>> {
	protected dbp: Promise<IDBPDatabase<Schema>>;
	private pieces: (r: StoredReading<F>) => number;
	private prepare: (input: F) => F;

	constructor(options: LocalJournalOptions<F> = {}) {
		const name = options.name ?? `${storagePrefix()}-demo`;
		this.pieces = options.pieces ?? (() => 0);
		this.prepare = options.prepare ?? ((f) => f);
		this.dbp = openDB<Schema>(name, 1, {
			upgrade(db) {
				const r = db.createObjectStore('readings', { keyPath: 'id', autoIncrement: true });
				r.createIndex('created_at', 'created_at');
				const i = db.createObjectStore('interpretations', { keyPath: ['reading_id', 'position'] });
				i.createIndex('reading_id', 'reading_id');
				db.createObjectStore('settings', { keyPath: 'key' });
			}
		});
	}

	// ---- readings ----------------------------------------------------------

	/** Newest first; `limit` for the home page's recent strip. */
	async list(limit?: number): Promise<V[]> {
		const db = await this.dbp;
		const out: StoredReading[] = [];
		let cur = await db.transaction('readings').store.index('created_at').openCursor(null, 'prev');
		while (cur && (limit === undefined || out.length < limit)) {
			out.push(cur.value);
			cur = await cur.continue();
		}
		return out.map((r) => view(r as StoredReading<F>) as V);
	}

	async get(id: number): Promise<V | null> {
		const db = await this.dbp;
		const r = await db.get('readings', id);
		if (!r) return null;
		return view(r as StoredReading<F>, await db.getAllFromIndex('interpretations', 'reading_id', id)) as V;
	}

	async save(input: NewReading<F>): Promise<V> {
		return this.insert(input, null, 'none');
	}

	/** The in-progress guided reading, created up front so it can be resumed. */
	async createGuided(input: NewReading<F> & { mode: InterpretationMode }): Promise<V> {
		const { mode, ...rest } = input;
		return this.insert(rest as NewReading<F>, mode, 'in_progress');
	}

	private async insert(input: NewReading<F>, mode: InterpretationMode | null, status: InterpretationStatus) {
		const db = await this.dbp;
		const { question, notes, ...own } = input;
		const id = await db.add('readings', {
			owner: OWNER,
			created_at: now(),
			question: question || null,
			// the server receives JSON; so does the store. The pages hand over
			// Svelte state proxies, which IndexedDB's structured clone rejects.
			...JSON.parse(JSON.stringify(this.prepare(own as unknown as F))),
			notes: notes ?? '',
			visibility: 'private',
			interpretation_mode: mode,
			interpretation_status: status
		});
		return (await this.get(id)) as V;
	}

	async updateNotes(id: number, notes: string | null | undefined): Promise<V | null> {
		const db = await this.dbp;
		const r = await db.get('readings', id);
		if (!r) return null;
		if (notes !== null && notes !== undefined) await db.put('readings', { ...r, notes });
		return this.get(id);
	}

	/** Deletes the reading and its interpretation rows (the FK cascade). */
	async remove(id: number): Promise<boolean> {
		const db = await this.dbp;
		const tx = db.transaction(['readings', 'interpretations'], 'readwrite');
		const r = await tx.objectStore('readings').get(id);
		if (!r) {
			await tx.done;
			return false;
		}
		await tx.objectStore('readings').delete(id);
		let cur = await tx.objectStore('interpretations').index('reading_id').openCursor(id);
		while (cur) {
			await cur.delete();
			cur = await cur.continue();
		}
		await tx.done;
		return true;
	}

	/** Upsert one piece's focused reading; a reading of one piece completes
	 * here (its one focused reading IS the whole). */
	async setFocused(id: number, position: number, text: string, persona: string | null): Promise<V | null> {
		return this.upsertInterpretation(id, position, 'focused', text, persona, (r) => this.pieces(r as StoredReading<F>) === 1);
	}

	/** Upsert the whole reading's text and mark it complete. */
	async setComprehensive(id: number, text: string, persona: string | null): Promise<V | null> {
		return this.upsertInterpretation(id, POSITION_WHOLE, 'comprehensive', text, persona, () => true);
	}

	private async upsertInterpretation(
		id: number,
		position: number,
		kind: StoredInterpretation['kind'],
		text: string,
		persona: string | null,
		completes: (r: StoredReading) => boolean
	): Promise<V | null> {
		const db = await this.dbp;
		const tx = db.transaction(['readings', 'interpretations'], 'readwrite');
		const r = await tx.objectStore('readings').get(id);
		if (!r) {
			await tx.done;
			return null;
		}
		const t = now();
		const existing = await tx.objectStore('interpretations').get([id, position]);
		await tx.objectStore('interpretations').put({
			reading_id: id,
			position,
			kind,
			text,
			persona,
			created_at: existing?.created_at ?? t,
			updated_at: t
		});
		if (completes(r)) await tx.objectStore('readings').put({ ...r, interpretation_status: 'complete' });
		await tx.done;
		return this.get(id);
	}

	async count(): Promise<number> {
		return (await this.dbp).count('readings');
	}

	/** Every reading in full, newest first (the export). */
	async all(): Promise<V[]> {
		const list = (await this.list()) as { id: number }[];
		return Promise.all(list.map((r) => this.get(r.id) as Promise<V>));
	}

	// ---- settings (the server's user_settings k/v) -------------------------

	async getSetting(key: string): Promise<string> {
		return (await (await this.dbp).get('settings', key))?.value ?? '';
	}

	/** An empty value deletes the key, like db.set_user_setting. */
	async setSetting(key: string, value: string): Promise<void> {
		const db = await this.dbp;
		if (value === '') await db.delete('settings', key);
		else await db.put('settings', { key, value });
	}

	async allSettings(): Promise<Record<string, string>> {
		const rows = await (await this.dbp).getAll('settings');
		return Object.fromEntries(rows.map((r) => [r.key, r.value]));
	}

	// ---- erase everything --------------------------------------------------

	async clear(): Promise<void> {
		const db = await this.dbp;
		const tx = db.transaction(['readings', 'interpretations', 'settings'], 'readwrite');
		await Promise.all([
			tx.objectStore('readings').clear(),
			tx.objectStore('interpretations').clear(),
			tx.objectStore('settings').clear()
		]);
		await tx.done;
	}
}
