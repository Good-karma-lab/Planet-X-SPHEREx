// The account side of the product flow (product spec §8): who is signed in, sign in and out, the marks sync for lib/marks.ts,
// tasks, and the view history. A guest uses the whole site without it; every call here that needs a user is a no-op for a guest.

import { pageVisible } from './idle';
import { useEffect, useState } from 'preact/hooks';

export interface User {
  id: string;
  name: string;
  email: string;
  /** granted on the server (python -m service.accounts.verdict --grant): the cabinet links the review page */
  reviewer?: boolean;
}
export interface AuthInfo {
  /** e-mail sign-in is open (a mail sender is set up, or dev mode); false with google false: "Sign-in opens soon" */
  email: boolean;
  /** Google sign-in is set up on the server */
  google: boolean;
  /** dev mode: the sign-in link is shown on the page */
  dev: boolean;
}

/** A mark as lib/marks.ts keeps it and `/api/me/marks` stores it. */
export interface AccountMark {
  id: string;
  field_key: string;
  cell_id: string | null;
  ra: number;
  dec: number;
  pass: number | null;
  label: string;
  level: 'suspicious' | 'interesting' | 'artefact';
  note: string;
  created: string;
  /** the date (MJD) of the frame the mark was put on, when the lab knows it */
  mjd?: number | null;
  /** what the service concluded (known · no_match · needs_another_pass · waiting · checking · comment), its text ready to show */
  verdict?: { state: string; text: string; name?: string } | null;
}
/** What lib/marks.ts takes in `setMarksSync`. */
export interface AccountMarksRemote {
  list(): Promise<AccountMark[]>;
  put(m: AccountMark): Promise<AccountMark>;
  remove(id: string): Promise<void>;
}

export interface Task {
  id: string;
  kind: 'cell' | 'finding';
  cell_id: string | null;
  /** the quarter of the square (NW, NE, SW, SE): people look through a square a quarter at a time */
  quarter?: string | null;
  /** the size of the view in arcmin (10 for a quarter) */
  fov?: number | null;
  finding_id: string | null;
  field_key: string | null;
  ra: number | null;
  dec: number | null;
  /** a square handed out for a lead in it: which find, its kind and promise (GET /api/tasks/next) */
  why?: { finding_id?: string; kind?: string; promise?: number; ra?: number; dec?: number; text?: string; ask?: { kind?: string; finding_id?: string; series?: string } } | null;
  /** a time-lapse track's task: the date of the film its search ran on (MJD); the lab plays that film, the one warmed ahead */
  film?: number | null;
  /** where the find itself stands in the field the task opens on (a built one that holds it) */
  find_ra?: number | null;
  find_dec?: number | null;
  /** left: given back without an answer */
  state: 'open' | 'done' | 'left';
  result: string | null;
  mark_ids: string[];
  created: string;
  done_at: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** One call of the product API with the session cookie; throws ApiError with the server's own words. */
export async function api<T>(path: string, init?: { method?: string; body?: unknown; signal?: AbortSignal }): Promise<T> {
  const r = await fetch(path, {
    method: init?.method ?? 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    signal: init?.signal,
    headers: init?.body === undefined ? undefined : { 'content-type': 'application/json' },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (!r.ok) {
    let detail = `the service answered ${r.status}`;
    try {
      const d = ((await r.json()) as { detail?: unknown }).detail;
      if (typeof d === 'string') detail = d;
    } catch {
      /* no body */
    }
    throw new ApiError(r.status, detail);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

// ------------------------------------------------------------------------------------------------------------------ the user

let user: User | null = null;
let auth: AuthInfo = { email: true, google: false, dev: false };
let loaded = false;
const listeners = new Set<(u: User | null) => void>();

/** The signed-in user, or null for a guest (and until the first answer of the server). */
export function currentUser(): User | null {
  return user;
}
export function authInfo(): AuthInfo {
  return auth;
}
/** True once the server has said who this is. */
export function userLoaded(): boolean {
  return loaded;
}
/** Called on sign-in, sign-out and the first answer; returns the unsubscribe. */
export function onUser(cb: (u: User | null) => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function setUser(u: User | null): void {
  const changed = (u?.id ?? null) !== (user?.id ?? null) || !loaded;
  user = u;
  loaded = true;
  if (changed) void syncMarks();
  for (const f of listeners) f(u);
}

/** Ask the server who this is (at the start, and after coming back from a sign-in link). */
export async function loadUser(): Promise<User | null> {
  try {
    const r = await api<{ user: User | null; auth?: Partial<AuthInfo> & { google: boolean; dev: boolean } }>('/api/me');
    if (r.auth) auth = { email: true, ...r.auth }; // a server from before this field has e-mail sign-in open
    setUser(r.user);
  } catch {
    setUser(null); // the service is away: a guest
  }
  return user;
}

/** Send the single-use sign-in link to this address. `next` is the place in the app to come back to (a hash). */
export async function signInByEmail(email: string, next?: string): Promise<{ sent: boolean; minutes: number; dev_link?: string }> {
  return api('/api/auth/email', { method: 'POST', body: { email, next } });
}

/** Where the "Continue with Google" button goes; null when Google is not set up. */
export function googleSignInUrl(next?: string): string | null {
  return auth.google ? `/api/auth/google${next ? `?next=${encodeURIComponent(next)}` : ''}` : null;
}

export async function signOut(): Promise<void> {
  await api('/api/auth/logout', { method: 'POST' });
  setUser(null);
}

export async function renameUser(name: string): Promise<User | null> {
  const r = await api<{ user: User | null }>('/api/me', { method: 'PATCH', body: { name } });
  setUser(r.user);
  return r.user;
}

/** The user for a component: re-renders on sign-in and sign-out. `ready` is false until the server has answered. */
export function useUser(): { user: User | null; ready: boolean; auth: AuthInfo } {
  const [, tick] = useState(0);
  useEffect(() => onUser(() => tick((n) => n + 1)), []);
  return { user, ready: loaded, auth };
}

// ----------------------------------------------------------------------------------------------------------- notifications

let unread = 0;
const unreadListeners = new Set<() => void>();
let unreadTimer: ReturnType<typeof setInterval> | undefined;

/** Set the number of unread notifications (the cabinet knows it after reading them), or ask the server for it. */
export function refreshUnread(known?: number): void {
  const set = (n: number) => {
    if (n === unread) return;
    unread = n;
    for (const f of unreadListeners) f();
  };
  if (known !== undefined) return set(known);
  if (!user) return set(0);
  void api<{ unread: number }>('/api/me/notifications').then(
    (r) => set(r.unread),
    () => undefined,
  );
}

/** The number of unread notifications, for the badge on the Cabinet tab of the nav (0 for a guest). Asked once a minute. */
export function useUnread(): number {
  const [, tick] = useState(0);
  useEffect(() => {
    const f = () => tick((n) => n + 1);
    unreadListeners.add(f);
    return () => void unreadListeners.delete(f);
  }, []);
  return unread;
}

// ------------------------------------------------------------------------------------------------------------------- marks

/** The account's marks behind the sync hook of lib/marks.ts. */
export const marksRemote: AccountMarksRemote = {
  list: async () => (await api<{ marks: AccountMark[] }>('/api/me/marks')).marks,
  put: (m) => api<AccountMark>('/api/me/marks', { method: 'POST', body: m }),
  remove: async (id) => {
    try {
      await api(`/api/me/marks/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e; // already gone is fine
    }
  },
};

// lib/marks.ts belongs to the lab stream; it is found at build time when it is there, so this file also builds without it.
const marksModules = import.meta.glob<{ setMarksSync?: (r: AccountMarksRemote | null) => unknown }>('./marks.ts');

async function syncMarks(): Promise<void> {
  const load = marksModules['./marks.ts'];
  if (!load) return;
  try {
    const m = await load();
    await m.setMarksSync?.(user ? marksRemote : null);
  } catch {
    /* the marks stay in the browser; lib/marks.ts shows its own status */
  }
}

// ------------------------------------------------------------------------------------------------------------------- tasks

/** Take the next task: a square to look through or a machine finding to check; `ref` asks for one square or finding by id. */
export async function nextTask(kind: 'cell' | 'finding', ref?: string): Promise<Task | null> {
  const q = new URLSearchParams({ kind });
  if (ref) q.set(kind === 'cell' ? 'cell_id' : 'finding_id', ref);
  else skipGiven(q);
  const t = (await api<{ task: Task | null }>(`/api/tasks/next?${q}`)).task;
  if (t) handTask(t);
  return t;
}

// A task taken here and opened in the lab by its id is handed over as it is: the lab does not ask the service for another one (one
// "Check this finding" is one task). Kept in memory and, for a reload of the same tab, in sessionStorage (the last HANDED_KEEP).
// The finds this tab was given, so that a guest (whose tasks are nobody's) is not handed the same one again; a person's are the
// service's to know, and it does not read this.
const given: string[] = [];
let lastLead = false; // the square this tab was given last came with a lead: the next one is a plain one
export function skipGiven(q: URLSearchParams): void {
  if (q.get('kind') === 'cell' && lastLead) q.set('plain', '1');
  if (q.get('kind') !== 'cell' && given.length) q.set('skip', given.slice(-60).join(','));
}

const HANDED_KEY = 'sky-handed-tasks';
const HANDED_KEEP = 20;
const handed = new Map<string, Task>();

function handedStore(): Record<string, Task> {
  try {
    const raw = globalThis.sessionStorage?.getItem(HANDED_KEY);
    return raw ? (JSON.parse(raw) as Record<string, Task>) : {};
  } catch {
    return {};
  }
}

/** Keep a task taken from the service for the lab that opens it next. */
export function handTask(t: Task): void {
  if (t.kind === 'cell') lastLead = !!t.why;
  if (t.finding_id && t.kind !== 'cell' && !given.includes(t.finding_id)) given.push(t.finding_id);
  handed.delete(t.id);
  handed.set(t.id, t);
  while (handed.size > HANDED_KEEP) handed.delete(handed.keys().next().value as string);
  try {
    const all = { ...handedStore(), [t.id]: t };
    const ids = Object.keys(all).slice(-HANDED_KEEP);
    globalThis.sessionStorage?.setItem(HANDED_KEY, JSON.stringify(Object.fromEntries(ids.map((i) => [i, all[i]]))));
  } catch {
    /* the memory copy is enough within the page */
  }
}

/** The task handed over under this id (taken by Findings, the globe or the cabinet), or null. */
export function handedTask(id: string): Task | null {
  return handed.get(id) ?? handedStore()[id] ?? null;
}

/** Close a task (needs a signed-in user). result: nothing | marks for a square; moves | artefact | known for a finding. */
export async function taskDone(id: string, result: string, markIds: string[] = []): Promise<Task> {
  return (
    await api<{ task: Task }>(`/api/me/tasks/${encodeURIComponent(id)}/done`, { method: 'POST', body: { result, mark_ids: markIds } })
  ).task;
}

/** The place in the lab where a task is done. */
/** A square given for a lead in it (task.why): the lead, the kind of its question series and its place, for the lab's address. */
export function leadParams(why: NonNullable<Task['why']>): Record<string, string> {
  const id = why.ask?.finding_id ?? why.finding_id;
  if (!id) return {};
  return {
    lead: id,
    ...(why.ask?.kind ? { lead_kind: why.ask.kind } : {}),
    ...(why.text ? { lead_text: why.text } : {}),
    ...(why.ra != null && why.dec != null ? { lead_ra: why.ra.toFixed(5), lead_dec: why.dec.toFixed(5) } : {}),
  };
}

/** A track task opens straight in the Days scale on its search's own film (`film`): the one the service warmed for it. `film` beside
 *  the task: the card's own date, when the service's task does not say it. */
export function taskHash(t: Task, film?: number | null): string {
  if (t.kind === 'finding' && t.finding_id && (t.ra == null || t.dec == null)) return `#/findings?lead=${encodeURIComponent(t.finding_id)}`;
  const q = new URLSearchParams();
  if (t.ra != null && t.dec != null) {
    q.set('ra', t.ra.toFixed(5));
    q.set('dec', t.dec.toFixed(5));
  }
  // every task opens at 10′ (a square a quarter at a time); a time-lapse keeps its own size
  // the service's own size when it gives one (a finding task: the smallest field that holds the whole track); else 10′, a time-lapse 60′
  q.set('fov', t.fov ? String(t.fov) : t.kind === 'cell' ? '10' : t.finding_id?.startsWith('film:') ? '60' : '10');
  q.set('task', t.id);
  q.set('kind', t.kind); // the lab of a guest cannot look a task up by its id
  if (t.kind === 'cell' && t.cell_id) q.set('cell', t.cell_id);
  if (t.kind === 'cell' && t.quarter) q.set('quarter', t.quarter);
  // a square given for a lead in it: the lab selects that find and asks its questions before the quarter's look
  if (t.kind === 'cell' && t.why) for (const [k, v] of Object.entries(leadParams(t.why))) q.set(k, v);
  if (['finding', 'moves', 'red', 'anomaly'].includes(t.kind) && t.finding_id) q.set('finding', t.finding_id);
  if (t.find_ra != null && t.find_dec != null) {
    q.set('find_ra', t.find_ra.toFixed(6));
    q.set('find_dec', t.find_dec.toFixed(6));
  }
  const f = t.film ?? film;
  if (t.kind === 'finding' && t.finding_id?.startsWith('film:') && f != null && Number.isFinite(f)) q.set('film', String(f));
  return `#/lab?${q}`;
}

// ----------------------------------------------------------------------------------------------------------------- history

export type HistoryKind = 'lab' | 'cell' | 'finding' | 'globe';

/** Remember that the user opened a place (a no-op for a guest). `ref` is the hash that opens it again; `key` names the place when
 *  its hash changes while the user works there. */
export function recordHistory(kind: HistoryKind, ref: string = location.hash, title = '', key?: string): void {
  if (!user || !ref.startsWith('#/')) return;
  void api('/api/me/history', { method: 'POST', body: { kind, ref: ref.slice(0, 600), title: title.slice(0, 120), key } }).catch(
    () => undefined,
  );
}

/** What a hash of the app is, for the history: null for a place not worth remembering (a bare screen). */
/** A find's kind in words, for History and other titles (the id's kind prefix is not shown). */
const FIND_NAME: Record<string, string> = {
  mover: 'Moving object',
  browndwarf: 'Brown dwarf candidate',
  red: 'Red source',
  anomaly: 'Odd spectrum',
  pair: 'Pair changing',
  film: 'Time-lapse track',
  peak: 'Planet X peak',
};

export function describeHash(hash: string): { kind: HistoryKind; title: string; key: string } | null {
  const [name = '', query = ''] = hash.replace(/^#\/?/, '').split('?');
  const p = new URLSearchParams(query);
  if (name === 'lab' && p.get('ra') && p.get('dec')) {
    const ra = Number(p.get('ra'));
    const dec = Number(p.get('dec'));
    if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
    const fov = p.get('fov') ?? '10';
    const place = `RA ${ra.toFixed(3)}° Dec ${dec >= 0 ? '+' : ''}${dec.toFixed(3)}° · ${fov}′`;
    return { kind: 'lab', title: `Lab · ${place}`, key: `lab:${ra.toFixed(3)},${dec.toFixed(3)},${fov}` };
  }
  if (name === 'findings' && p.get('lead')) {
    const id = p.get('lead')!;
    const [k = '', h = ''] = id.includes(':') ? id.split(':', 2) : ['', id];
    const what = FIND_NAME[k] ?? 'A find';
    return { kind: 'finding', title: h ? `${what} · ${h.slice(0, 6)}` : what, key: `finding:${id}` };
  }
  if ((name === 'explore' || name === 'globe') && p.get('cell')) {
    const id = p.get('cell')!;
    return { kind: 'cell', title: `Square ${id}`, key: `cell:${id}` };
  }
  return null;
}

let historyTimer: ReturnType<typeof setTimeout> | undefined;

/** A place counts as opened when the user stays there for a moment; the lab's passing states are not recorded one by one. */
function trackPlace(): void {
  clearTimeout(historyTimer);
  historyTimer = setTimeout(() => {
    const d = describeHash(location.hash);
    if (d) recordHistory(d.kind, location.hash, d.title, d.key);
  }, 2500);
}

if (typeof window !== 'undefined' && typeof location !== 'undefined') {
  window.addEventListener('hashchange', trackPlace);
  onUser((u) => u && trackPlace());
  onUser(() => {
    refreshUnread();
    clearInterval(unreadTimer);
    if (user) unreadTimer = setInterval(() => pageVisible() && refreshUnread(), 60000); // a hidden tab asks nothing
  });
  void loadUser();
}
