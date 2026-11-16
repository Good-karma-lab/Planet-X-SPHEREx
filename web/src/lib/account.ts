// The account side of the product flow (product spec §8): who is signed in, sign in and out, the marks sync for lib/marks.ts,
// tasks, and the view history. A guest uses the whole site without it; every call here that needs a user is a no-op for a guest.

import { useEffect, useState } from 'preact/hooks';

export interface User {
  id: string;
  name: string;
  email: string;
}
export interface AuthInfo {
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
  finding_id: string | null;
  field_key: string | null;
  ra: number | null;
  dec: number | null;
  state: 'open' | 'done';
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
let auth: AuthInfo = { google: false, dev: false };
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
    const r = await api<{ user: User | null; auth?: AuthInfo }>('/api/me');
    if (r.auth) auth = r.auth;
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
  return (await api<{ task: Task | null }>(`/api/tasks/next?${q}`)).task;
}

/** Close a task (needs a signed-in user). result: nothing | marks for a square; moves | artefact | known for a finding. */
export async function taskDone(id: string, result: string, markIds: string[] = []): Promise<Task> {
  return (await api<{ task: Task }>(`/api/me/tasks/${encodeURIComponent(id)}/done`, { method: 'POST', body: { result, mark_ids: markIds } })).task;
}

/** The place in the lab where a task is done. */
export function taskHash(t: Task): string {
  if (t.kind === 'finding' && t.finding_id && (t.ra == null || t.dec == null)) return `#/findings?lead=${encodeURIComponent(t.finding_id)}`;
  const q = new URLSearchParams();
  if (t.ra != null && t.dec != null) {
    q.set('ra', t.ra.toFixed(5));
    q.set('dec', t.dec.toFixed(5));
  }
  q.set('fov', t.kind === 'cell' ? '20' : '5');
  q.set('task', t.id);
  return `#/lab?${q}`;
}

// ----------------------------------------------------------------------------------------------------------------- history

export type HistoryKind = 'lab' | 'cell' | 'finding' | 'globe';

/** Remember that the user opened a place (a no-op for a guest). `ref` is the hash that opens it again; `key` names the place when
 *  its hash changes while the user works there. */
export function recordHistory(kind: HistoryKind, ref: string = location.hash, title = '', key?: string): void {
  if (!user || !ref.startsWith('#/')) return;
  void api('/api/me/history', { method: 'POST', body: { kind, ref: ref.slice(0, 600), title: title.slice(0, 120), key } }).catch(() => undefined);
}

/** What a hash of the app is, for the history: null for a place not worth remembering (a bare screen). */
export function describeHash(hash: string): { kind: HistoryKind; title: string; key: string } | null {
  const [name = '', query = ''] = hash.replace(/^#\/?/, '').split('?');
  const p = new URLSearchParams(query);
  if (name === 'lab' && p.get('ra') && p.get('dec')) {
    const ra = Number(p.get('ra'));
    const dec = Number(p.get('dec'));
    if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
    const fov = p.get('fov') ?? '5';
    const place = `RA ${ra.toFixed(3)}° Dec ${dec >= 0 ? '+' : ''}${dec.toFixed(3)}° · ${fov}′`;
    return { kind: 'lab', title: `Lab · ${place}`, key: `lab:${ra.toFixed(3)},${dec.toFixed(3)},${fov}` };
  }
  if (name === 'findings' && p.get('lead')) {
    const id = p.get('lead')!;
    return { kind: 'finding', title: `Finding ${id}`, key: `finding:${id}` };
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
  void loadUser();
}
