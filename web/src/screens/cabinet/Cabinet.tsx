// The cabinet (product spec §5): my squares, marks, findings, investigations, tasks, view history and notifications; a sign-in
// panel for a guest. Everything shown is read from the account API (lib/account.ts).

import { useCallback, useEffect, useState } from 'preact/hooks';
import { api, loadUser, nextTask, refreshUnread, renameUser, signInByEmail, signOut, taskHash, useUser, googleSignInUrl, type AccountMark, type Task, type User } from '../../lib/account';
import { InvestigationPage } from './Investigation';
import { Pic } from './pic';
import { ScorePanel } from './Score';
import type { Route } from '../../lib/route';
import { formatDec, formatRA } from '../../lib/sky';
import './cabinet.css';

interface Check {
  id: string;
  state: string;
  detail?: string;
}
/** An object the lab added to an investigation: where it is, its marks, the ruler's result, the catalogue match. */
export interface InvItem {
  id: string;
  label: string;
  ra: number;
  dec: number;
  field_key?: string;
  finding_id?: string;
  mark_ids: string[];
  ruler?: { rate: number; catalogue_rate?: number; pa?: number; shift?: number; object?: string };
  catalogue?: { source: string; name: string; sep?: number; type?: string };
  ref?: string;
  thumb?: string;
  at: string;
}
export interface Investigation {
  id: string;
  title: string;
  items: InvItem[];
  finding_id: string | null;
  mark_ids: string[];
  notes: { text: string; at: string }[];
  frames: { ref: string; title: string; note: string; thumb: string | null; at: string }[];
  checks_job: string | null;
  submitted: string | null;
  checks: { state: string; checks: Check[] | null; can_submit: boolean } | null;
  /** open | checking | needs_comment | blocked | submitted: a declared submit finishes by itself on the server */
  state: string;
  /** what the running checks say they are doing */
  progress: string;
  updated: string;
}
export interface MyFinding {
  finding_id: string;
  role: string;
  result: string | null;
  status: string;
  title: string;
  ra: number | null;
  dec: number | null;
  created: string;
}
interface HistoryRow {
  id: number;
  kind: string;
  ref: string;
  title: string;
  at: string;
}
interface Notice {
  id: number;
  kind: string;
  text: string;
  ref: string | null;
  created: string;
  read: boolean;
}
interface Mine {
  marks: AccountMark[];
  tasks: Task[];
  findings: MyFinding[];
  investigations: Investigation[];
  history: HistoryRow[];
  notifications: Notice[];
}

const TABS = ['squares', 'marks', 'findings', 'investigations', 'tasks', 'history', 'notifications', 'score', 'account'] as const;
type Tab = (typeof TABS)[number];
const TAB_NAME: Record<Tab, string> = {
  squares: 'My squares',
  marks: 'My marks',
  findings: 'My findings',
  investigations: 'Investigations',
  tasks: 'Tasks',
  history: 'View history',
  notifications: 'Notifications',
  score: 'Score',
  account: 'Account',
};
const RESULT: Record<string, string> = { nothing: 'Nothing here', marks: 'Done, with marks', moves: 'It moves', artefact: 'Artefact', known: 'Known object' };
const INV_STATE: Record<string, string> = { open: 'not submitted', checking: 'checking', needs_comment: 'comment needed', blocked: 'not submitted: a check failed', submitted: 'submitted' };
const ROLE: Record<string, string> = { submitted: 'Submitted by me', checked: 'Checked by me', marked: 'Marked by me' };

const when = (iso: string | null) => (iso ? `${iso.slice(0, 16).replace('T', ' ')} UTC` : '');
const words = (s: string) => s.replace(/_/g, ' ');
const place = (ra: number | null, dec: number | null) => (ra == null || dec == null ? '' : `${formatRA(ra, true)} ${formatDec(dec, true)}`);

/** The lab at a mark: its field when the key carries the place ("ra,dec,fov" of lib/marks.ts), else the mark's own position. */
export function markHash(m: Pick<AccountMark, 'field_key' | 'ra' | 'dec'>): string {
  const k = /^(-?\d+(?:\.\d+)?),([+-]?\d+(?:\.\d+)?),(\d+(?:\.\d+)?)$/.exec(m.field_key);
  const [ra, dec, fov] = k ? [k[1]!, String(Number(k[2])), k[3]!] : [m.ra.toFixed(5), m.dec.toFixed(5), '5'];
  return `#/lab?${new URLSearchParams({ ra, dec, fov })}`;
}

/** Everything of the user, read again whenever the cabinet or one of its tabs is opened. */
/** What a history row shows a picture of: the lab's place or the finding. */
function placeOf(ref: string): { findingId?: string; ra?: number; dec?: number } {
  const [name = '', query = ''] = ref.replace(/^#\/?/, '').split('?');
  const p = new URLSearchParams(query);
  if (name === 'findings' && p.get('lead')) return { findingId: p.get('lead')! };
  const ra = Number(p.get('ra') ?? NaN);
  const dec = Number(p.get('dec') ?? NaN);
  return Number.isFinite(ra) && Number.isFinite(dec) ? { ra, dec } : {};
}

function useMine(user: User | null, tab: string, inv: string): { mine: Mine | null; error: string; reload: () => void } {
  const [mine, setMine] = useState<Mine | null>(null);
  const [error, setError] = useState('');
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!user) {
      setMine(null);
      return;
    }
    const stop = new AbortController();
    const get = <K extends keyof Mine>(k: K) => api<Record<K, Mine[K]>>(`/api/me/${k}`, { signal: stop.signal }).then((r) => r[k]);
    Promise.all([get('marks'), get('tasks'), get('findings'), get('investigations'), get('history'), get('notifications')]).then(
      ([marks, tasks, findings, investigations, history, notifications]) => {
        setMine({ marks, tasks, findings, investigations, history, notifications });
        setError('');
        refreshUnread(notifications.filter((x) => !x.read).length);
      },
      (e: Error) => !stop.signal.aborted && setError(`Not loaded: ${e.message}`),
    );
    return () => stop.abort();
  }, [user?.id, n, tab, inv]);
  return { mine, error, reload: useCallback(() => setN((x) => x + 1), []) };
}

function SignIn({ expired, google, email: emailOpen }: { expired: string | undefined; google: string | null; email: boolean }) {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<{ to: string; minutes: number; dev_link?: string } | null>(null);
  const [error, setError] = useState(expired === 'expired' ? 'This link has expired or was already used. Ask for a new one.' : expired === 'google' ? 'Google sign-in did not finish. Try again.' : '');
  const send = (e: Event) => {
    e.preventDefault();
    if (busy || !email.trim()) return;
    setBusy(true);
    setError('');
    signInByEmail(email.trim(), '#/me')
      .then((r) => setSent({ to: email.trim(), ...r }))
      .catch((x: Error) => setError(x.message))
      .finally(() => setBusy(false));
  };
  if (!emailOpen && !google)
    // no mail sender and no Google keys on this server yet: one status line, no form that would take an address in vain
    return (
      <div class="card glass cb-signin" data-testid="signin">
        <h3>Sign in</h3>
        <p class="cb-status" role="status" data-testid="signin-soon">
          Sign-in opens soon · your marks are kept in this browser
        </p>
      </div>
    );
  return (
    <div class="card glass cb-signin" data-testid="signin">
      <h3>Sign in</h3>
      <p class="hint">Sign in to keep your marks, squares and findings under your name.{emailOpen ? ' No password: we send a link to your e-mail.' : ''}</p>
      {emailOpen && (
        <form onSubmit={send}>
          <input class="inp" type="email" name="email" autocomplete="email" required placeholder="you@example.org" aria-label="E-mail address" value={email} onInput={(e) => setEmail((e.target as HTMLInputElement).value)} />
          <button class="btn primary" type="submit" disabled={busy}>
            {busy ? 'Sending…' : 'Send me a sign-in link'}
          </button>
        </form>
      )}
      <p class={`cb-status ${error ? 'bad' : sent ? 'good' : ''}`} role="status" data-testid="signin-status">
        {error || (sent ? `Link sent to ${sent.to}. It works once, for ${sent.minutes} minutes.` : '')}
      </p>
      {sent?.dev_link && (
        <p class="cb-status">
          Dev mode:{' '}
          <a href={sent.dev_link} data-testid="dev-link">
            open the sign-in link
          </a>
        </p>
      )}
      {google && (
        <div class="cb-or">
          <span class="mono-s">or</span>
          <a class="btn" href={google} data-testid="google">
            Continue with Google
          </a>
        </div>
      )}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p class="cb-empty">{text}</p>;
}

/** The whole sky, RA growing to the left as on the sky; a dot per square. */
function SquaresMap({ tasks }: { tasks: Task[] }) {
  const pts = tasks.filter((t) => t.ra != null && t.dec != null);
  return (
    <svg class="cb-map" viewBox="0 0 360 180" role="img" aria-label={`Sky map of my squares: ${pts.length} shown`}>
      {[60, 120, 180, 240, 300].map((x) => (
        <line class="g" x1={x} y1={0} x2={x} y2={180} />
      ))}
      {[30, 60, 90, 120, 150].map((y) => (
        <line class="g" x1={0} y1={y} x2={360} y2={y} />
      ))}
      <polyline class="ecl" points={Array.from({ length: 73 }, (_, i) => `${360 - i * 5},${(90 - 23.44 * Math.sin((i * 5 * Math.PI) / 180)).toFixed(1)}`).join(' ')} />
      <text x={3} y={8}>RA 24h</text>
      <text x={340} y={8}>0h</text>
      <text x={3} y={176}>Dec −90°</text>
      {pts.map((t) => (
        <circle key={t.id} class={t.state} cx={360 - t.ra!} cy={90 - t.dec!} r={2.2}>
          <title>{`${t.cell_id}: ${t.state === 'done' ? (RESULT[t.result ?? ''] ?? '') : 'open'}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function TaskRow({ t }: { t: Task }) {
  return (
    <a class="cb-row haspic" href={taskHash(t)} data-testid="task-row">
      <Pic findingId={t.finding_id} ra={t.ra} dec={t.dec} label={t.kind === 'cell' ? `square ${t.cell_id}` : `finding ${t.finding_id}`} />
      <span class="cb-main">
        <b>{t.kind === 'cell' ? `Square ${t.cell_id}` : `Finding ${t.finding_id}`}</b>
        <span class="mono-s">
          {place(t.ra, t.dec)} · taken {when(t.created)}
          {t.done_at ? ` · closed ${when(t.done_at)}` : ''}
        </span>
      </span>
      <span class="cb-side">
        <span class={`badge st-${t.state}`}>{t.state === 'done' ? (RESULT[t.result ?? ''] ?? 'done') : 'open'}</span>
        {t.mark_ids.length > 0 && <span class="mono-s">{t.mark_ids.length} marks</span>}
      </span>
    </a>
  );
}

/** "Hide me from the leaderboard": the points are still earned. */
function HideFromBoard() {
  const [hide, setHide] = useState<boolean | null>(null);
  const [status, setStatus] = useState('');
  useEffect(() => {
    void api<{ hide_from_board: boolean }>('/api/me/score').then((s) => setHide(s.hide_from_board), () => setHide(false));
  }, []);
  const set = (v: boolean) => {
    setHide(v);
    api('/api/me', { method: 'PATCH', body: { hide_from_board: v } }).then(
      () => setStatus(''),
      (e: Error) => setStatus(e.message),
    );
  };
  return (
    <div class="cb-line">
      <label class="chk">
        <input type="checkbox" checked={hide === true} disabled={hide === null} onChange={(e) => set((e.target as HTMLInputElement).checked)} data-testid="hide-board" />
        Hide me from the leaderboard
      </label>
      <span class="mono-s">points are still earned</span>
      {status && <span class="cb-status bad">{status}</span>}
    </div>
  );
}

/** The public page #/u/<slug>: off until the user turns it on (sharing an achievement turns it on too). */
function PublicProfile() {
  const [p, setP] = useState<{ slug: string; public: boolean } | null>(null);
  const [status, setStatus] = useState('');
  useEffect(() => {
    void api<{ slug: string; public: boolean }>('/api/me/profile').then(setP, () => undefined);
  }, []);
  const set = (v: boolean) => {
    if (p) setP({ ...p, public: v });
    api('/api/me', { method: 'PATCH', body: { public_profile: v } }).then(
      () => setStatus(''),
      (e: Error) => setStatus(e.message),
    );
  };
  return (
    <div class="cb-line">
      <label class="chk">
        <input type="checkbox" checked={p?.public === true} disabled={!p} onChange={(e) => set((e.target as HTMLInputElement).checked)} data-testid="public-profile" />
        Public profile page
      </label>
      {p && (
        <a class="btn sm" href={`#/u/${p.slug}`} data-testid="open-profile">
          Open my page
        </a>
      )}
      <span class="mono-s">name, points, badges and findings; never the e-mail</span>
      {status && <span class="cb-status bad">{status}</span>}
    </div>
  );
}

function Who({ user }: { user: User }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(user.name);
  const [status, setStatus] = useState('');
  const save = (e: Event) => {
    e.preventDefault();
    renameUser(name.trim() || user.name).then(
      () => setEditing(false),
      (x: Error) => setStatus(x.message),
    );
  };
  return (
    <div class="cb-who" data-testid="who">
      {editing ? (
        <form onSubmit={save} style={{ display: 'contents' }}>
          <input class="inp" aria-label="Name shown in the credit line" maxLength={60} value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
          <button class="btn sm" type="submit">
            Save
          </button>
        </form>
      ) : (
        <>
          <b data-testid="who-name">{user.name}</b>
          <span class="mono-s">{user.email}</span>
          <button class="btn sm" onClick={() => setEditing(true)} title="The name shown in the credit line of your findings">
            Change name
          </button>
        </>
      )}
      <button class="btn sm" data-testid="signout" onClick={() => void signOut().catch((x: Error) => setStatus(x.message))}>
        Sign out
      </button>
      {status && <span class="cb-status bad">{status}</span>}
    </div>
  );
}

export function Cabinet({ route }: { route: Route }) {
  const { user, ready, auth } = useUser();
  const [status, setStatus] = useState('');
  const tab: Tab = (TABS as readonly string[]).includes(route.params['tab'] ?? '') ? (route.params['tab'] as Tab) : 'squares';
  const invId = route.params['inv'] ?? '';
  const { mine, error, reload } = useMine(user, tab, invId);
  const opened = tab === 'investigations' && invId ? (mine?.investigations.find((i) => i.id === invId) ?? null) : null;
  const go = (t: Tab) => (location.hash = t === 'squares' ? '#/me' : `#/me?tab=${t}`);
  const run = (job: Promise<unknown>) => job.then(reload, (e: Error) => setStatus(e.message));
  const take = (kind: 'cell' | 'finding') =>
    nextTask(kind).then(
      (t) => (t ? (location.hash = taskHash(t)) : setStatus(kind === 'cell' ? 'No searched square is waiting for a look' : 'No finding is waiting for a check')),
      (e: Error) => setStatus(e.message),
    );
  const squares = mine?.tasks.filter((t) => t.kind === 'cell') ?? [];
  const inflight = mine?.investigations.filter((i) => ['checking', 'needs_comment', 'blocked'].includes(i.state)) ?? [];
  const unread = mine?.notifications.filter((n) => !n.read).length ?? 0;
  const count: Record<Tab, number> = {
    squares: squares.length,
    marks: mine?.marks.length ?? 0,
    findings: mine?.findings.length ?? 0,
    investigations: mine?.investigations.length ?? 0,
    tasks: (mine?.tasks.filter((t) => t.state === 'open').length ?? 0) + inflight.length,
    history: mine?.history.length ?? 0,
    notifications: unread,
    score: 0,
    account: 0,
  };
  return (
    <div class="screen">
      <div class="ipage" data-testid="cabinet">
        <div class="pg-h cb-head">
          <div>
            <h1>Cabinet</h1>
            <p class="hint">Your squares, marks, findings and investigations, kept under your name.</p>
          </div>
          {user && <Who user={user} />}
        </div>
        {!ready ? (
          <div class="mono-s">Loading…</div>
        ) : !user ? (
          <SignIn expired={route.params['signin']} google={auth.google ? googleSignInUrl('#/me') : null} email={auth.email} />
        ) : (
          <>
            <div class="cb-tabs" role="tablist">
              {TABS.map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} class={tab === t ? 'on' : ''} onClick={() => go(t)} data-testid={`tab-${t}`}>
                  {TAB_NAME[t]}
                  {t !== 'account' && t !== 'score' && <span>{count[t]}</span>}
                </button>
              ))}
              <span class={`cb-status ${error || status ? 'bad' : ''}`} role="status" data-testid="cabinet-status">
                {error || status || (!mine ? 'Loading…' : '')}
              </span>
            </div>
            {mine && (
              <div class="card glass cb-card" data-testid={`panel-${tab}`}>
                {tab === 'squares' && (
                  <>
                    <h3>
                      My squares
                      <span class="cb-actions">
                        <button class="btn sm" onClick={() => void take('cell')}>
                          Take a square
                        </button>
                      </span>
                    </h3>
                    {squares.length === 0 ? (
                      <Empty text="No squares yet" />
                    ) : (
                      <div class="cb-split">
                        <div class="cb-list">
                          {squares.map((t) => (
                            <TaskRow key={t.id} t={t} />
                          ))}
                        </div>
                        <SquaresMap tasks={squares} />
                      </div>
                    )}
                  </>
                )}
                {tab === 'marks' && (
                  <>
                    <h3>My marks</h3>
                    {mine.marks.length === 0 && <Empty text="No marks yet" />}
                    <div class="cb-list">
                      {mine.marks.map((m) => (
                        <div key={m.id} class="cb-row haspic" data-testid="mark-row">
                          <Pic ra={m.ra} dec={m.dec} label={m.label || 'mark'} />
                          <span class="cb-main">
                            <b>{m.label || 'Mark'}</b>
                            <span class="mono-s">
                              {place(m.ra, m.dec)}
                              {m.pass != null ? ` · pass ${m.pass + 1}` : ''}
                              {m.cell_id ? ` · square ${m.cell_id}` : ''} · {when(m.created)}
                            </span>
                            {m.note && <span class="cb-note">{m.note}</span>}
                          </span>
                          <span class="cb-side">
                            <span class={`badge st-${m.level}`}>{m.level}</span>
                            <a class="btn sm" href={markHash(m)}>
                              Open in the lab
                            </a>
                            <button class="btn sm" onClick={() => confirm('Delete this mark?') && void run(api(`/api/me/marks/${encodeURIComponent(m.id)}`, { method: 'DELETE' }))}>
                              Delete
                            </button>
                          </span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
                {tab === 'findings' && (
                  <>
                    <h3>
                      My findings
                      <span class="cb-actions">
                        <button class="btn sm" onClick={() => void take('finding')}>
                          Check a finding
                        </button>
                      </span>
                    </h3>
                    {mine.findings.length === 0 && <Empty text="No findings yet" />}
                    <div class="cb-list">
                      {mine.findings.map((f) => (
                        <a key={`${f.finding_id}${f.role}`} class="cb-row haspic" href={`#/findings?lead=${encodeURIComponent(f.finding_id)}`} data-testid="finding-row">
                          <Pic findingId={f.finding_id} ra={f.ra} dec={f.dec} label={f.title || `finding ${f.finding_id}`} />
                          <span class="cb-main">
                            <b>{f.title || `Finding ${f.finding_id}`}</b>
                            <span class="mono-s">
                              {ROLE[f.role] ?? f.role}
                              {f.result ? `: ${RESULT[f.result] ?? f.result}` : ''} · {place(f.ra, f.dec)} {when(f.created)}
                            </span>
                          </span>
                          <span class="cb-side">
                            {f.role === 'submitted' && (
                              <button class="btn sm" onClick={(e) => (e.preventDefault(), (location.hash = `#/findings/${f.finding_id}/story`))} data-testid="to-story">
                                Story and share
                              </button>
                            )}
                            <span class={`badge st-${f.status}`}>{words(f.status)}</span>
                          </span>
                        </a>
                      ))}
                    </div>
                  </>
                )}
                {tab === 'investigations' &&
                  (opened ? (
                    <InvestigationPage inv={opened} marks={mine.marks} finding={mine.findings.find((f) => f.finding_id === opened.submitted) ?? null} reload={reload} />
                  ) : (
                    <>
                      <h3>Investigations</h3>
                      {invId && <Empty text="No such investigation" />}
                      {mine.investigations.length === 0 && <Empty text="No investigations yet" />}
                      <div class="cb-list">
                        {mine.investigations.map((inv) => {
                          const first = mine.marks.find((m) => inv.mark_ids.includes(m.id));
                          const c = inv.checks;
                          return (
                            <a key={inv.id} class="cb-row haspic" href={`#/me?tab=investigations&inv=${inv.id}`} data-testid="investigation">
                              <Pic findingId={inv.finding_id} ra={first?.ra} dec={first?.dec} label={inv.title} />
                              <span class="cb-main">
                                <b>{inv.title}</b>
                                <span class="mono-s">
                                  {(inv.items ?? []).length} objects · {inv.mark_ids.length} marks · {inv.notes.length} notes · changed {when(inv.updated)}
                                </span>
                              </span>
                              <span class="cb-side">
                                <span class={`badge st-inv-${inv.state}`}>{INV_STATE[inv.state] ?? words(inv.state)}</span>
                                {c && !inv.submitted && inv.state === 'open' && <span class="mono-s">{c.can_submit ? 'checks passed' : c.state === 'done' ? 'checks block submit' : 'checks running'}</span>}
                              </span>
                            </a>
                          );
                        })}
                      </div>
                    </>
                  ))}
                {tab === 'tasks' && (
                  <>
                    <h3>
                      Tasks
                      <span class="cb-actions">
                        <button class="btn sm" onClick={() => void take('cell')}>
                          Take a square
                        </button>
                        <button class="btn sm" onClick={() => void take('finding')}>
                          Check a finding
                        </button>
                      </span>
                    </h3>
                    {mine.tasks.length === 0 && inflight.length === 0 && <Empty text="No tasks yet" />}
                    <div class="cb-list">
                      {inflight.map((inv) => (
                        <a key={inv.id} class="cb-row haspic" href={`#/me?tab=investigations&inv=${inv.id}`} data-testid="submit-row">
                          <Pic findingId={inv.finding_id} ra={mine.marks.find((m) => inv.mark_ids.includes(m.id))?.ra} dec={mine.marks.find((m) => inv.mark_ids.includes(m.id))?.dec} label={inv.title} />
                          <span class="cb-main">
                            <b>Submit: {inv.title}</b>
                            <span class="mono-s">{inv.state === 'checking' ? inv.progress || 'checks running' : inv.state === 'needs_comment' ? 'every check has answered · a comment is needed' : 'a check blocks the submit'}</span>
                          </span>
                          <span class="cb-side">
                            <span class={`badge st-inv-${inv.state}`}>{INV_STATE[inv.state] ?? words(inv.state)}</span>
                          </span>
                        </a>
                      ))}
                    </div>
                    <div class="cb-list">
                      {mine.tasks.map((t) => (
                        <TaskRow key={t.id} t={t} />
                      ))}
                    </div>
                  </>
                )}
                {tab === 'history' && (
                  <>
                    <h3>
                      View history
                      {mine.history.length > 0 && (
                        <span class="cb-actions">
                          <button class="btn sm" onClick={() => confirm('Clear the view history?') && void run(api('/api/me/history', { method: 'DELETE' }))}>
                            Clear
                          </button>
                        </span>
                      )}
                    </h3>
                    {mine.history.length === 0 && <Empty text="Nothing opened yet" />}
                    <div class="cb-list">
                      {mine.history.map((h) => (
                        <a key={h.id} class="cb-row haspic" href={h.ref} data-testid="history-row">
                          <Pic {...placeOf(h.ref)} label={h.title || 'this place'} />
                          <span class="cb-main">
                            <b>{h.title || h.ref}</b>
                            <span class="mono-s">{when(h.at)}</span>
                          </span>
                          <span class="cb-side">
                            <span class="badge">{h.kind}</span>
                          </span>
                        </a>
                      ))}
                    </div>
                  </>
                )}
                {tab === 'score' && (
                  <>
                    <h3>Score</h3>
                    <ScorePanel />
                  </>
                )}
                {tab === 'account' && (
                  <>
                    <h3>Account</h3>
                    <div class="cb-account">
                      <div class="cb-line">
                        <b>{user.name}</b>
                        <span class="mono-s">{user.email}</span>
                      </div>
                      <HideFromBoard />
                      <PublicProfile />
                      <div class="cb-line">
                        <a class="btn sm" href="/api/me/export" download="spherex-space-my-data.json" data-testid="download-data">
                          Download my data
                        </a>
                        <span class="mono-s">JSON: marks, squares, tasks, investigations, findings, history, notifications</span>
                      </div>
                      <div class="cb-line">
                        <button
                          class="btn sm danger"
                          data-testid="delete-account"
                          onClick={() =>
                            confirm('Delete your account and all your data? Marks, squares, investigations, history and notifications are removed and cannot be restored. Findings you submitted stay, without your name.') &&
                            void api('/api/me', { method: 'DELETE' }).then(() => loadUser(), (e: Error) => setStatus(e.message))
                          }
                        >
                          Delete my account and my data
                        </button>
                      </div>
                    </div>
                  </>
                )}
                {tab === 'notifications' && (
                  <>
                    <h3>
                      Notifications
                      {unread > 0 && (
                        <span class="cb-actions">
                          <button class="btn sm" onClick={() => void run(api('/api/me/notifications/read', { method: 'POST', body: {} }))}>
                            Mark all read
                          </button>
                        </span>
                      )}
                    </h3>
                    {mine.notifications.length === 0 && <Empty text="No notifications" />}
                    <div class="cb-list">
                      {mine.notifications.map((n) => (
                        <a key={n.id} class={`cb-row cb-wrap ${n.read ? '' : 'unread'}`} href={!n.ref ? '#/me?tab=notifications' : n.ref.startsWith('#/') ? n.ref : `#/findings?lead=${encodeURIComponent(n.ref)}`} data-testid="notice-row">
                          <span class="cb-main">
                            <b>{n.text}</b>
                            <span class="mono-s">{when(n.created)}</span>
                          </span>
                        </a>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
