// The cabinet (product spec §5): my squares, marks, findings, investigations, tasks, view history and notifications; a sign-in
// panel for a guest. Everything shown is read from the account API (lib/account.ts).

import { useCallback, useEffect, useState } from 'preact/hooks';
import { api, nextTask, renameUser, signInByEmail, signOut, taskHash, useUser, googleSignInUrl, type AccountMark, type Task, type User } from '../../lib/account';
import type { Route } from '../../lib/route';
import { formatDec, formatRA } from '../../lib/sky';
import './cabinet.css';

interface Check {
  id: string;
  state: string;
  detail?: string;
}
interface Investigation {
  id: string;
  title: string;
  finding_id: string | null;
  mark_ids: string[];
  notes: { text: string; at: string }[];
  frames: { ref: string; title: string; note: string; thumb: string | null; at: string }[];
  checks_job: string | null;
  submitted: string | null;
  checks: { state: string; checks: Check[] | null; can_submit: boolean } | null;
  updated: string;
}
interface MyFinding {
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

const TABS = ['squares', 'marks', 'findings', 'investigations', 'tasks', 'history', 'notifications'] as const;
type Tab = (typeof TABS)[number];
const TAB_NAME: Record<Tab, string> = {
  squares: 'My squares',
  marks: 'My marks',
  findings: 'My findings',
  investigations: 'Investigations',
  tasks: 'Tasks',
  history: 'View history',
  notifications: 'Notifications',
};
const RESULT: Record<string, string> = { nothing: 'Nothing here', marks: 'Done, with marks', moves: 'It moves', artefact: 'Artefact', known: 'Known object' };
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

function useMine(user: User | null): { mine: Mine | null; error: string; reload: () => void } {
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
      },
      (e: Error) => !stop.signal.aborted && setError(`Not loaded: ${e.message}`),
    );
    return () => stop.abort();
  }, [user?.id, n]);
  return { mine, error, reload: useCallback(() => setN((x) => x + 1), []) };
}

function SignIn({ expired, google }: { expired: string | undefined; google: string | null }) {
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
  return (
    <div class="card glass cb-signin" data-testid="signin">
      <h3>Sign in</h3>
      <p class="hint">Sign in to keep your marks, squares and findings under your name. No password: we send a link to your e-mail.</p>
      <form onSubmit={send}>
        <input class="inp" type="email" name="email" autocomplete="email" required placeholder="you@example.org" aria-label="E-mail address" value={email} onInput={(e) => setEmail((e.target as HTMLInputElement).value)} />
        <button class="btn primary" type="submit" disabled={busy}>
          {busy ? 'Sending…' : 'Send me a sign-in link'}
        </button>
      </form>
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
    <a class="cb-row" href={taskHash(t)} data-testid="task-row">
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

function InvestigationItem({ inv, reload }: { inv: Investigation; reload: () => void }) {
  const [note, setNote] = useState('');
  const [status, setStatus] = useState('');
  const run = (job: Promise<unknown>) => job.then(reload, (e: Error) => setStatus(e.message));
  const add = (e: Event) => {
    e.preventDefault();
    if (!note.trim()) return;
    void run(api(`/api/me/investigations/${inv.id}/notes`, { method: 'POST', body: { text: note.trim() } }).then(() => setNote('')));
  };
  const checks = inv.checks;
  return (
    <details class="cb-inv" data-testid="investigation">
      <summary>
        <span class="cb-row">
          <span class="cb-main">
            <b>{inv.title}</b>
            <span class="mono-s">
              {inv.notes.length} notes · {inv.frames.length} saved frames · {inv.mark_ids.length} marks · changed {when(inv.updated)}
            </span>
          </span>
          <span class="cb-side">
            {inv.submitted ? (
              <span class="badge st-submitted">submitted</span>
            ) : checks ? (
              <span class={`badge ${checks.can_submit ? 'st-pass' : checks.state === 'done' ? 'st-fail' : 'st-open'}`}>{checks.can_submit ? 'ready to submit' : checks.state === 'done' ? 'checks block submit' : 'checks running'}</span>
            ) : (
              <span class="badge">{inv.checks_job ? 'checks not answering' : 'not checked'}</span>
            )}
          </span>
        </span>
      </summary>
      <div class="cb-inv-body">
        {checks?.checks && checks.checks.length > 0 && (
          <>
            <h4>Checks</h4>
            <div class="cb-checks">
              {checks.checks.map((c) => (
                <span key={c.id} class={`badge st-${c.state}`} title={c.detail ?? ''}>
                  {words(c.id)}: {words(c.state)}
                </span>
              ))}
            </div>
          </>
        )}
        <h4>Notes</h4>
        {inv.notes.length === 0 && <span class="mono-s">No notes yet</span>}
        {inv.notes.map((n, i) => (
          <div key={i} class="cb-note">
            {n.text} <span class="mono-s">{when(n.at)}</span>
          </div>
        ))}
        <form class="cb-addnote" onSubmit={add}>
          <input class="inp" placeholder="Add a note" aria-label="Add a note" maxLength={4000} value={note} onInput={(e) => setNote((e.target as HTMLInputElement).value)} />
          <button class="btn sm" type="submit">
            Add
          </button>
        </form>
        {inv.frames.length > 0 && (
          <>
            <h4>Saved frames</h4>
            <div class="cb-frames">
              {inv.frames.map((f, i) => (
                <a key={i} href={f.ref} title="Open this place">
                  {f.thumb ? <img src={f.thumb} alt={f.title || 'Saved frame'} /> : <span class="cb-noimg" />}
                  <span>{f.title || 'Frame'}</span>
                  {f.note && <span class="mono-s">{f.note}</span>}
                </a>
              ))}
            </div>
          </>
        )}
        <div class="cb-side" style={{ justifyContent: 'flex-start' }}>
          {inv.finding_id && (
            <a class="btn sm" href={`#/findings?lead=${encodeURIComponent(inv.finding_id)}`}>
              Open the finding
            </a>
          )}
          {!inv.submitted && (
            <button class="btn sm" onClick={() => confirm(`Delete the investigation “${inv.title}”?`) && void run(api(`/api/me/investigations/${inv.id}`, { method: 'DELETE' }))}>
              Delete
            </button>
          )}
          <span class="cb-status bad">{status}</span>
        </div>
      </div>
    </details>
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
  const { mine, error, reload } = useMine(user);
  const [status, setStatus] = useState('');
  const tab: Tab = (TABS as readonly string[]).includes(route.params['tab'] ?? '') ? (route.params['tab'] as Tab) : 'squares';
  const go = (t: Tab) => (location.hash = t === 'squares' ? '#/me' : `#/me?tab=${t}`);
  const run = (job: Promise<unknown>) => job.then(reload, (e: Error) => setStatus(e.message));
  const take = (kind: 'cell' | 'finding') =>
    nextTask(kind).then(
      (t) => (t ? (location.hash = taskHash(t)) : setStatus(kind === 'cell' ? 'No searched square is waiting for a look' : 'No finding is waiting for a check')),
      (e: Error) => setStatus(e.message),
    );
  const squares = mine?.tasks.filter((t) => t.kind === 'cell') ?? [];
  const unread = mine?.notifications.filter((n) => !n.read).length ?? 0;
  const count: Record<Tab, number> = {
    squares: squares.length,
    marks: mine?.marks.length ?? 0,
    findings: mine?.findings.length ?? 0,
    investigations: mine?.investigations.length ?? 0,
    tasks: mine?.tasks.filter((t) => t.state === 'open').length ?? 0,
    history: mine?.history.length ?? 0,
    notifications: unread,
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
          <SignIn expired={route.params['signin']} google={auth.google ? googleSignInUrl('#/me') : null} />
        ) : (
          <>
            <div class="cb-tabs" role="tablist">
              {TABS.map((t) => (
                <button key={t} role="tab" aria-selected={tab === t} class={tab === t ? 'on' : ''} onClick={() => go(t)} data-testid={`tab-${t}`}>
                  {TAB_NAME[t]}
                  <span>{count[t]}</span>
                </button>
              ))}
            </div>
            <p class={`cb-status ${error || status ? 'bad' : ''}`} role="status">
              {error || status || (!mine ? 'Loading…' : '')}
            </p>
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
                        <div key={m.id} class="cb-row" data-testid="mark-row">
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
                        <a key={`${f.finding_id}${f.role}`} class="cb-row" href={`#/findings?lead=${encodeURIComponent(f.finding_id)}`} data-testid="finding-row">
                          <span class="cb-main">
                            <b>{f.title || `Finding ${f.finding_id}`}</b>
                            <span class="mono-s">
                              {ROLE[f.role] ?? f.role}
                              {f.result ? `: ${RESULT[f.result] ?? f.result}` : ''} · {place(f.ra, f.dec)} {when(f.created)}
                            </span>
                          </span>
                          <span class="cb-side">
                            <span class={`badge st-${f.status}`}>{words(f.status)}</span>
                          </span>
                        </a>
                      ))}
                    </div>
                  </>
                )}
                {tab === 'investigations' && (
                  <>
                    <h3>Investigations</h3>
                    {mine.investigations.length === 0 && <Empty text="No investigations yet" />}
                    {mine.investigations.map((inv) => (
                      <InvestigationItem key={inv.id} inv={inv} reload={reload} />
                    ))}
                  </>
                )}
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
                    {mine.tasks.length === 0 && <Empty text="No tasks yet" />}
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
                        <a key={h.id} class="cb-row" href={h.ref} data-testid="history-row">
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
                        <a key={n.id} class={`cb-row cb-wrap ${n.read ? '' : 'unread'}`} href={n.ref ? `#/findings?lead=${encodeURIComponent(n.ref)}` : '#/me?tab=notifications'} data-testid="notice-row">
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
