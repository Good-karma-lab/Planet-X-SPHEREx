// Marks a person puts on the sky in the lab (product spec §5, §8). A guest's marks live in the browser (local storage); a signed-in
// user's marks are also kept in the account through one sync hook, `setMarksSync`, which the accounts module (lib/account.ts) calls on
// sign-in and sign-out. The lab never talks to the account for marks.

export type MarkLevel = 'suspicious' | 'interesting' | 'artefact';
export const MARK_LEVELS: MarkLevel[] = ['suspicious', 'interesting', 'artefact'];

/** What a mark says (spec §5g), chosen with one tap after the click: the server acts on it (a search job, a catalogue lookup, an
 *  artefact count, the place's Talk). */
export type MarkKind = 'moves' | 'not_in_catalogues' | 'artefact' | 'odd';
export const MARK_KINDS: MarkKind[] = ['moves', 'not_in_catalogues', 'artefact', 'odd'];
/** what an artefact looks like */
export type MarkLook = 'blinks' | 'glare' | 'spike' | 'edge' | 'hot_dot';
export const MARK_LOOKS: MarkLook[] = ['blinks', 'glare', 'spike', 'edge', 'hot_dot'];
/** the level a kind is kept under for the marks of before (the account still stores a level) */
export const LEVEL_OF_KIND: Record<MarkKind, MarkLevel> = { moves: 'interesting', not_in_catalogues: 'interesting', artefact: 'artefact', odd: 'suspicious' };

/** The shape of spec §8 (`/api/me/marks`). The id is made here, so a guest's mark keeps it when it moves to the account. */
export interface Mark {
  id: string;
  field_key: string;
  cell_id: string | null;
  ra: number;
  dec: number;
  /** the pass on show when the mark was put (its index in the field), null when it belongs to no pass */
  pass: number | null;
  /** the date of the frame the mark was put on, MJD (the middle of the pass, or the visit's time in Days) */
  mjd?: number | null;
  label: string;
  level: MarkLevel;
  /** spec §5g: what it is (none yet on a mark just put, and on the marks of before) */
  kind?: MarkKind | null;
  /** an artefact: what it looks like */
  look?: MarkLook | null;
  note: string;
  /** ISO time, UTC */
  created: string;
}

/** The account side of the marks: the server's list, an upsert by id, a removal. */
export interface MarksRemote {
  list(): Promise<Mark[]>;
  put(m: Mark): Promise<Mark>;
  remove(id: string): Promise<void>;
}

const KEY = 'spherex.marks.v1';

interface Store {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
}

let store: Store | null = typeof localStorage === 'undefined' ? null : localStorage;
let marks: Mark[] | null = null;
let remote: MarksRemote | null = null;
const listeners = new Set<() => void>();
/** what the last sync could not do ('' when all is saved): shown as a status line by whoever draws the marks */
let syncNote = '';

function valid(m: unknown): m is Mark {
  const x = m as Mark;
  return (
    !!x &&
    typeof x.id === 'string' &&
    typeof x.field_key === 'string' &&
    Number.isFinite(x.ra) &&
    Number.isFinite(x.dec) &&
    MARK_LEVELS.includes(x.level)
  );
}

function load(): Mark[] {
  if (marks) return marks;
  marks = [];
  try {
    const raw = store?.getItem(KEY);
    const arr: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(arr)) marks = arr.filter(valid);
  } catch {
    /* an unreadable store starts empty */
  }
  return marks;
}

function save(): void {
  try {
    store?.setItem(KEY, JSON.stringify(marks ?? []));
  } catch {
    /* a full or closed store: the marks stay for this visit */
  }
  for (const f of listeners) f();
}

function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** what the server did with the last mark it was sent (spec §5g: a search job, a lookup, an agreement), with the mark's id */
export interface MarkEffect {
  id: string;
  effect: string;
  state?: string;
  people?: number;
  [k: string]: unknown;
}
let lastEffect: MarkEffect | null = null;

function push(job: Promise<unknown>, id?: string): void {
  job.then(
    (r) => {
      const e = (r as { effect?: { effect?: string } } | null)?.effect;
      const changed = !!syncNote || (!!id && !!e?.effect);
      if (id && e?.effect) lastEffect = { ...(e as MarkEffect), id };
      syncNote = '';
      if (changed) for (const f of listeners) f();
    },
    () => {
      syncNote = 'Marks not saved to the account';
      for (const f of listeners) f();
    },
  );
}

/** Every mark, oldest first. */
export function allMarks(): Mark[] {
  return load().slice();
}

/** The marks of one field (the lab's place and size). */
export function marksOf(fieldKey: string): Mark[] {
  return load().filter((m) => m.field_key === fieldKey);
}

export function addMark(m: Omit<Mark, 'id' | 'created'> & Partial<Pick<Mark, 'id' | 'created'>>): Mark {
  const mark: Mark = { ...m, id: m.id ?? newId(), created: m.created ?? new Date().toISOString() };
  load().push(mark);
  save();
  if (remote) push(remote.put(mark), mark.id);
  return mark;
}

export function updateMark(id: string, change: Partial<Omit<Mark, 'id' | 'created'>>): Mark | null {
  const list = load();
  const i = list.findIndex((m) => m.id === id);
  if (i < 0) return null;
  const mark = { ...list[i]!, ...change };
  list[i] = mark;
  save();
  if (remote) push(remote.put(mark), mark.id);
  return mark;
}

export function removeMark(id: string): void {
  const list = load();
  const i = list.findIndex((m) => m.id === id);
  if (i < 0) return;
  list.splice(i, 1);
  save();
  if (remote) push(remote.remove(id));
}

/** Called on every change of the marks or of the sync state; returns the unsubscribe. */
export function onMarks(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

/** the server's answer to the last mark sent, null before any (a guest's marks get none) */
export function lastMarkEffect(): MarkEffect | null {
  return lastEffect;
}

export function marksSyncNote(): string {
  return syncNote;
}

/**
 * The one sync hook. With a remote (sign-in): the account's marks are merged into the local ones by id, every local mark the account
 * lacks is put there (a guest's marks move to the account), and from then on every add, edit and removal goes to the account too.
 * With null (sign-out): syncing stops; the marks stay in this browser.
 */
export async function setMarksSync(r: MarksRemote | null): Promise<void> {
  remote = r;
  if (!r) {
    syncNote = '';
    for (const f of listeners) f();
    return;
  }
  try {
    const theirs = (await r.list()).filter(valid);
    if (remote !== r) return;
    const list = load();
    const mine = new Map(list.map((m) => [m.id, m]));
    const have = new Set(theirs.map((m) => m.id));
    for (const m of theirs) if (!mine.has(m.id)) list.push(m);
    list.sort((a, b) => a.created.localeCompare(b.created));
    syncNote = '';
    save();
    await Promise.all(list.filter((m) => !have.has(m.id)).map((m) => r.put(m)));
  } catch {
    if (remote === r) {
      syncNote = 'Marks not saved to the account';
      for (const f of listeners) f();
    }
  }
}

/** The key of a lab field, "ra,dec,fov": its centre to 1e-5° and its size in arcminutes ("269.44629,4.76792,5"). */
export function fieldKey(ra: number, dec: number, fov: number): string {
  return `${ra.toFixed(5)},${dec.toFixed(5)},${fov}`;
}

/** For tests: another store (or none) and a clean state. */
export function resetMarksForTest(s: Store | null): void {
  store = s;
  marks = null;
  remote = null;
  syncNote = '';
  listeners.clear();
}
