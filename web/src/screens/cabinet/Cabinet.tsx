// The cabinet (product spec §5): my squares, marks, findings, investigations, tasks, view history and notifications; a sign-in
// panel for a guest. Everything shown is read from the account API (lib/account.ts).

import { useCallback, useEffect, useState } from 'preact/hooks';
import { api, describeHash, nextTask, refreshUnread, taskHash, useUser, type AccountMark, type Task, type User } from '../../lib/account';
import { InvestigationPage } from './Investigation';
import { ReviewPanel } from './Review';
import { SignInCard } from '../../ui/SignInCard';
import { lastLab } from '../../lib/place';
import { Pic } from './pic';
import { Badge, ScorePanel, badgeSrc, type MyScore } from './Score';
import { AccountMenu, Bell, type Notice } from './Corner';
import type { Route } from '../../lib/route';
import { formatDec, formatRA } from '../../lib/sky';
import { icon } from '../../ui/icons';
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
interface Mine {
  marks: AccountMark[];
  tasks: Task[];
  findings: MyFinding[];
  investigations: Investigation[];
  history: HistoryRow[];
  notifications: Notice[];
  score: MyScore | null;
}

/** "My work": one switch over what the person has done. */
const WORK = ['squares', 'marks', 'findings', 'history'] as const;
type Work = (typeof WORK)[number];
const WORK_NAME: Record<Work, string> = { squares: 'Squares', marks: 'Marks', findings: 'Findings', history: 'History' };
const RESULT: Record<string, string> = { nothing: 'Nothing here', marks: 'Done, with marks', moves: 'It moves', artefact: 'Artefact', known: 'Known object' };
const ROLE: Record<string, string> = { submitted: 'Submitted by me', checked: 'Checked by me', marked: 'Marked by me' };

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

function useMine(user: User | null, view: string, inv: string): { mine: Mine | null; error: string; reload: () => void } {
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
    const score = api<MyScore>('/api/me/score', { signal: stop.signal }).catch(() => null);
    Promise.all([get('marks'), get('tasks'), get('findings'), get('investigations'), get('history'), get('notifications'), score]).then(
      ([marks, tasks, findings, investigations, history, notifications, score]) => {
        setMine({ marks, tasks, findings, investigations, history, notifications, score });
        setError('');
        refreshUnread(notifications.filter((x) => !x.read).length);
      },
      (e: Error) => !stop.signal.aborted && setError(`Not loaded: ${e.message}`),
    );
    return () => stop.abort();
  }, [user?.id, n, view, inv]);
  return { mine, error, reload: useCallback(() => setN((x) => x + 1), []) };
}

/** The guest's Cabinet: the site's one sign-in card in the middle of the page. */
function SignIn({ expired }: { expired: string | undefined }) {
  const error = expired === 'expired' ? 'This link was already used or has expired · send a new one' : expired === 'google' ? 'Google sign-in did not finish · try again' : '';
  return (
    <div class="sx-page">
      <SignInCard
        // coming from the lab: the link brings the person back to that very place (field, task, pass), not to the Cabinet
        next={lastLab() || '#/me'}
        reason={lastLab() ? 'Then back to your place in the lab' : 'To keep your squares, marks and findings'}
        error={error}
      />
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p class="cb-empty">{text}</p>;
}

const num = (n: number) => n.toLocaleString('en-US');
const ago = (iso: string | null) => {
  const s = iso ? (Date.now() - Date.parse(iso)) / 1000 : NaN;
  if (!Number.isFinite(s)) return '';
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return iso!.slice(0, 10);
};

/** What came of an investigation, in one word and one status line; `tone` colours both. */
export function verdictOf(inv: Investigation, finding: MyFinding | null): { word: string; line: string; tone: 'good' | 'warn' | 'bad' | 'run' | 'idle' } {
  const rows = inv.checks?.checks ?? [];
  const failed = rows.find((c) => c.state === 'fail');
  if (inv.submitted) {
    const st = finding?.status ?? 'submitted';
    const word: Record<string, string> = { submitted: 'Submitted', in_review: 'In review', candidate: 'Candidate', confirmed: 'Confirmed', known: 'Known object', artefact: 'Artefact' };
    return { word: word[st] ?? words(st), line: `finding ${inv.submitted}`, tone: st === 'candidate' || st === 'confirmed' ? 'good' : st === 'artefact' || st === 'known' ? 'bad' : 'warn' };
  }
  if (inv.state === 'checking') return { word: 'Checking', line: inv.progress || 'checks running', tone: 'run' };
  if (inv.state === 'needs_comment') return { word: 'Comment needed', line: 'every check has answered · a check warns', tone: 'warn' };
  if (inv.state === 'blocked') return { word: 'Not submitted', line: failed ? `${words(failed.id)}: ${failed.detail ?? 'failed'}` : 'a check failed', tone: 'bad' };
  if (inv.checks?.can_submit) return { word: 'Ready to submit', line: 'every check passed', tone: 'good' };
  const n = (inv.items ?? []).length;
  return { word: 'Gathering', line: `${n} ${n === 1 ? 'object' : 'objects'} · ${inv.mark_ids.length} marks · ${inv.notes.length} notes`, tone: 'idle' };
}

function InvCard({ inv, marks, finding }: { inv: Investigation; marks: AccountMark[]; finding: MyFinding | null }) {
  const first = (inv.items ?? [])[0];
  const m = marks.find((x) => inv.mark_ids.includes(x.id));
  const v = verdictOf(inv, finding);
  const rows = inv.checks?.checks ?? [];
  return (
    <a class={`cb-inv2 tone-${v.tone}`} href={`#/me?inv=${encodeURIComponent(inv.id)}`} data-testid="investigation">
      <Pic findingId={inv.submitted ?? inv.finding_id} ra={first?.ra ?? m?.ra} dec={first?.dec ?? m?.dec} thumb={first?.thumb ?? inv.frames[0]?.thumb} size={84} label={inv.title} />
      <span class="cb-invbody">
        <span class="cb-invtop">
          <b>{inv.title}</b>
          <span class="mono-s">{ago(inv.updated)}</span>
        </span>
        <span class="cb-verdict">
          <span class="cb-vword">{v.word}</span>
          <span class="mono-s">{v.line}</span>
        </span>
        {rows.length > 0 && (
          <span class="cb-dots" aria-label={`${rows.filter((c) => ['pass', 'warn', 'fail'].includes(c.state)).length} of ${rows.length} checks answered`}>
            {rows.map((c) => (
              <i key={c.id} class={`d-${c.state}`} title={`${words(c.id)}: ${c.state}${c.detail ? ` · ${c.detail}` : ''}`} />
            ))}
          </span>
        )}
      </span>
    </a>
  );
}

/** a mark's verdict in a word on its badge (the full text on hover) */
const VERDICT_WORD: Record<string, string> = {
  known: 'known',
  no_match: 'no match',
  needs_another_pass: 'one more pass',
  waiting: 'waiting',
  checking: 'checking',
  comment: 'comment',
};

/** The open square task the person used last: the newest lab visit (view history) on an open square's task, or on its square and
 *  quarter; else the newest open square taken. */
function lastSquare(mine: Pick<Mine, 'tasks' | 'history'>): Task | undefined {
  // a square task left open for hours is stale (the service says so): Continue offers the ones in use
  const open = mine.tasks
    .filter((t) => t.kind === 'cell' && t.state === 'open' && !(t as Task & { stale?: boolean }).stale)
    .sort((a, b) => b.created.localeCompare(a.created));
  if (open.length < 2) return open[0];
  const visits = [...mine.history].sort((a, b) => b.at.localeCompare(a.at));
  for (const h of visits) {
    const [name = '', query = ''] = h.ref.replace(/^#\/?/, '').split('?');
    if (name !== 'lab') continue;
    const q = new URLSearchParams(query);
    const hit = open.find((t) => t.id === q.get('task')) ?? open.find((t) => !!q.get('cell') && t.cell_id === q.get('cell') && (t.quarter ?? '') === (q.get('quarter') ?? ''));
    if (hit) return hit;
  }
  return open[0];
}

/** Name, level and points, and the one next thing to do. */
function Hero({ mine, take }: { mine: Mine; take: (k: 'cell' | 'finding') => void }) {
  const s = mine.score;
  const cur = s ? ([...s.levels].reverse().find((l) => s.points >= l.at) ?? s.levels[0]!) : null;
  const toNext = s && cur && s.next_level_at != null ? (s.points - cur.at) / (s.next_level_at - cur.at) : 1;
  const nextName = s?.levels.find((l) => l.at === s.next_level_at)?.name;
  const openCell = lastSquare(mine);
  const openCheck = mine.tasks.find((t) => t.kind !== 'cell' && t.state === 'open');
  const quarters = (s as (MyScore & { quarters?: number }) | null)?.quarters ?? mine.tasks.filter((t) => t.kind === 'cell' && t.state === 'done').length;
  return (
    <div class="card glass cb-hero" data-testid="hero">
      <div class="cb-me">
        {s && <img class="cb-levelbadge" src={badgeSrc(`level_${s.level.toLowerCase()}`)} alt="" width={64} height={64} data-testid="level-badge" />}
        <div class="cb-melines">
          <span class="cb-lvl" data-testid="score-level">
            {s?.level ?? '—'}
            <span class="cb-pts2" data-testid="score-points">
              {num(s?.points ?? 0)} points
            </span>
          </span>
          <span class="cb-bar" role="progressbar" aria-valuenow={Math.round(toNext * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Progress to the next level">
            <i style={{ width: `${Math.round(toNext * 100)}%` }} />
          </span>
          <span class="mono-s">{!s ? '' : s.next_level_at == null ? 'the highest level' : `${num(s.next_level_at - s.points)} to ${nextName}`}</span>
          {s && s.waiting > 0 && (
            // points for an answer come when the finding is settled and the answer matches the others': say they are on their way
            <span class="mono-s cb-waiting" data-testid="hero-waiting" title="An answer earns points when enough people have answered the same finding and yours matches theirs">
              {s.waiting} {s.waiting === 1 ? 'answer waits' : 'answers wait'} for other people’s answers
            </span>
          )}
        </div>
      </div>
      <div class="cb-stats">
        <span title="Quarters of squares you have looked through. A square is 20′ and is looked through a quarter (10′) at a time; Squares below lists every one you took">
          <b>{quarters}</b>looked through
        </span>
        <span>
          <b>{mine.marks.length}</b>marks
        </span>
        <span>
          <b>{mine.findings.length}</b>findings
        </span>
        {s && s.answered > 0 && s.accuracy != null && (
          <span data-testid="score-accuracy" title={`${s.right} of ${s.answered} settled answers`}>
            <b>{Math.round(s.accuracy * 100)}%</b>answers right
          </span>
        )}
      </div>
      <div class="cb-next2">
        {openCell ? (
          <a class="btn primary big" href={taskHash(openCell)} title={`The square you used last · taken ${ago(openCell.created)}`} data-testid="next-square">
            Continue · square {openCell.cell_id}
            {openCell.quarter ? ` ${openCell.quarter}` : ''}
          </a>
        ) : (
          <button class="btn primary big" onClick={() => take('cell')} data-testid="next-square">
            Give me a square
          </button>
        )}
        {openCheck ? (
          <a class="btn" href={taskHash(openCheck)} data-testid="next-check">
            Check a finding
          </a>
        ) : (
          <button class="btn" onClick={() => take('finding')} data-testid="next-check">
            Check a finding
          </button>
        )}
      </div>
    </div>
  );
}

/** The badges as one strip: the earned ones, the closest next one, and the full list behind a button. */
function BadgeStrip({ score }: { score: MyScore }) {
  const all = score.achievements;
  const earned = all.filter((a) => a.earned_at).sort((a, b) => (b.earned_at ?? '').localeCompare(a.earned_at ?? ''));
  const next = all.filter((a) => !a.earned_at && !a.hidden).sort((a, b) => b.progress - a.progress)[0];
  const tiers = (fam: string) => all.filter((a) => a.family === fam).length;
  return (
    <a class="card glass cb-strip" href="#/me?view=badges" data-testid="badge-strip">
      <span class="cb-striphead">
        <b>Badges</b>
        <span class="mono-s">
          {earned.length} of {all.length}
        </span>
      </span>
      <span class="cb-earned">
        {earned.slice(0, 9).map((a) => (
          <span key={a.id} title={a.name}>
            <Badge a={a} tiers={tiers(a.family)} size={40} />
          </span>
        ))}
        {earned.length === 0 && <span class="mono-s">none yet</span>}
      </span>
      {next && (
        <span class="cb-stripnext" data-testid="next">
          <Badge a={next} tiers={tiers(next.family)} size={40} />
          <span class="cb-stripnexttext">
            <span>
              Next: <b>{next.name}</b>
            </span>
            <span class="cb-bar">
              <i style={{ width: `${Math.round(next.progress * 100)}%` }} />
            </span>
            <span class="mono-s">
              {next.have} of {next.need}
            </span>
          </span>
        </span>
      )}
      <span class="cb-stripgo">All badges →</span>
    </a>
  );
}

function SmallRow({ href, pic, title, line, side, testid }: { href?: string; pic: { findingId?: string | null; ra?: number | null; dec?: number | null; label: string; icon?: string }; title: string; line: string; side?: preact.ComponentChildren; testid: string }) {
  const body = (
    <>
      {pic.icon ? <span class="cb-pic cb-icontile" style={{ width: '52px', height: '52px' }} dangerouslySetInnerHTML={{ __html: icon(pic.icon, 24) }} /> : <Pic {...pic} size={52} />}
      <span class="cb-main">
        <b>{title}</b>
        <span class="mono-s">{line}</span>
      </span>
      <span class="cb-side">{side}</span>
    </>
  );
  return href ? (
    <a class="cb-row2" href={href} data-testid={testid}>
      {body}
    </a>
  ) : (
    <div class="cb-row2" data-testid={testid}>
      {body}
    </div>
  );
}

const SCREEN_NAME: Record<string, [string, string]> = {
  explore: ['Globe', 'globe'],
  globe: ['Globe', 'globe'],
  findings: ['Findings', 'sparkle'],
  lab: ['Lab', 'chart-line-up'],
  planetx: ['Planet X', 'planet-x'],
  method: ['Method', 'sigma'],
  about: ['About', 'info'],
  index: ['Index', 'database'],
};

/** A history row: the place's own title, else what the address names; the place's picture, else the screen's icon. */
function HistoryItem({ h }: { h: HistoryRow }) {
  const pl = placeOf(h.ref);
  const screen = h.ref.replace(/^#\/?/, '').split(/[?/]/)[0] ?? '';
  const [name, ic] = SCREEN_NAME[screen] ?? ['This place', 'compass'];
  const title = describeHash(h.ref)?.title || h.title || name; // said again from the address: older rows kept the raw find id
  const has = pl.findingId || pl.ra != null;
  return <SmallRow testid="history-row" href={h.ref} pic={has ? { ...pl, label: title } : { label: title, icon: ic }} title={title} line={ago(h.at)} />;
}

function WorkPanel({ mine, work, run }: { mine: Mine; work: Work; run: (p: Promise<unknown>) => void }) {
  const squares = mine.tasks.filter((t) => t.kind === 'cell');
  const count: Record<Work, number> = { squares: squares.length, marks: mine.marks.length, findings: mine.findings.length, history: mine.history.length };
  const set = (w: Work) => (location.hash = w === 'squares' ? '#/me' : `#/me?work=${w}`);
  return (
    <section class="card glass cb-work" data-testid={`panel-${work}`}>
      <div class="cb-secthead">
        <h3>My work</h3>
        {work === 'history' && mine.history.length > 0 && (
          <button class="cb-link" onClick={() => confirm('Clear the view history?') && run(api('/api/me/history', { method: 'DELETE' }))}>
            Clear
          </button>
        )}
      </div>
      <div class="seg sm cb-seg" role="tablist">
        {WORK.map((w) => (
          <button key={w} role="tab" aria-selected={work === w} class={work === w ? 'on' : ''} onClick={() => set(w)} data-testid={`tab-${w}`}>
            {WORK_NAME[w]}
            <span class="cb-n">{count[w]}</span>
          </button>
        ))}
      </div>
      <div class="cb-worklist">
        {work === 'squares' &&
          (squares.length === 0 ? (
            <Empty text="No squares yet" />
          ) : (
            <>
              {squares.map((t) => (
                <SmallRow
                  key={t.id}
                  testid="task-row"
                  href={taskHash(t)}
                  pic={{ ra: t.ra, dec: t.dec, label: `square ${t.cell_id}` }}
                  title={`${t.cell_id}${t.quarter ? ` · ${t.quarter}` : ''}`}
                  // an open square earns its points once it is looked through: a minute with every pass shown
                  line={`${t.done_at ? `closed ${ago(t.done_at)}` : `given ${ago(t.created)} · not looked through yet`}${t.mark_ids.length ? ` · ${t.mark_ids.length} marks` : ''}`}
                  side={<span class={`badge st-${t.state}`}>{t.state === 'done' ? (RESULT[t.result ?? ''] ?? 'done') : 'open'}</span>}
                />
              ))}
            </>
          ))}
        {work === 'marks' &&
          (mine.marks.length === 0 ? (
            <Empty text="No marks yet" />
          ) : (
            mine.marks.map((m) => (
              <SmallRow
                key={m.id}
                testid="mark-row"
                pic={{ ra: m.ra, dec: m.dec, label: m.label || 'mark' }}
                title={m.label || 'Mark'}
                line={`${place(m.ra, m.dec)}${m.pass != null ? ` · pass ${m.pass + 1}` : ''}${m.note ? ` · ${m.note}` : ''}`}
                side={
                  <>
                    {/* what the service concluded about the mark (known: 12304 …, no catalogue match, waiting for JPL), else its level */}
                    {m.verdict?.text ? (
                      <span class={`badge v-${m.verdict.state}`} title={m.verdict.text}>
                        {VERDICT_WORD[m.verdict.state] ?? m.verdict.state}
                      </span>
                    ) : (
                      <span class={`badge st-${m.level}`}>{m.level}</span>
                    )}
                    <a class="btn sm" href={markHash(m)}>
                      Lab
                    </a>
                    <button class="cb-x" title="Delete this mark" aria-label="Delete this mark" onClick={() => confirm('Delete this mark?') && run(api(`/api/me/marks/${encodeURIComponent(m.id)}`, { method: 'DELETE' }))}>
                      ×
                    </button>
                  </>
                }
              />
            ))
          ))}
        {work === 'findings' &&
          (mine.findings.length === 0 ? (
            <Empty text="No findings yet" />
          ) : (
            mine.findings.map((f) => (
              <SmallRow
                key={`${f.finding_id}${f.role}`}
                testid="finding-row"
                href={f.role === 'submitted' ? `#/findings/${f.finding_id}/story` : `#/findings?lead=${encodeURIComponent(f.finding_id)}`}
                pic={{ findingId: f.finding_id, ra: f.ra, dec: f.dec, label: f.title || `finding ${f.finding_id}` }}
                title={f.title || `Finding ${f.finding_id}`}
                line={`${ROLE[f.role] ?? f.role}${f.result ? `: ${RESULT[f.result] ?? f.result}` : ''} · ${ago(f.created)}`}
                side={<span class={`badge st-${f.status}`}>{words(f.status)}</span>}
              />
            ))
          ))}
        {work === 'history' &&
          (mine.history.length === 0 ? (
            <Empty text="Nothing opened yet" />
          ) : (
            mine.history.map((h) => (
              <HistoryItem key={h.id} h={h} />
            ))
          ))}
      </div>
    </section>
  );
}

export function Cabinet({ route }: { route: Route }) {
  const { user, ready } = useUser();
  const [status, setStatus] = useState('');
  const p = route.params;
  // older addresses (#/me?tab=…) still land on the right place
  const tab = p['tab'] ?? '';
  const invId = p['inv'] ?? '';
  const view = p['view'] === 'badges' || tab === 'score' ? 'badges' : p['view'] === 'review' ? 'review' : invId ? 'inv' : 'home';
  const work: Work = (WORK as readonly string[]).includes(p['work'] ?? '') ? (p['work'] as Work) : tab === 'tasks' ? 'squares' : (WORK as readonly string[]).includes(tab) ? (tab as Work) : 'squares';
  const { mine, error, reload } = useMine(user, `${view}${work}`, invId);
  const stale = p['signin'] === 'expired' && !!user;
  const opened = invId ? (mine?.investigations.find((i) => i.id === invId) ?? null) : null;
  const run = (job: Promise<unknown>) => void job.then(reload, (e: Error) => setStatus(e.message));
  const take = (kind: 'cell' | 'finding') =>
    void nextTask(kind).then(
      (t) => (t ? (location.hash = taskHash(t)) : setStatus(kind === 'cell' ? 'No searched square is waiting for a look' : 'No finding is waiting for a check')),
      (e: Error) => setStatus(e.message),
    );
  const findingOf = (inv: Investigation) => mine?.findings.find((f) => f.finding_id === inv.submitted) ?? null;
  const RANK: Record<string, number> = { checking: 0, needs_comment: 1, blocked: 2, open: 3, submitted: 4 };
  const invs = [...(mine?.investigations ?? [])].sort((a, b) => (RANK[a.state] ?? 9) - (RANK[b.state] ?? 9) || b.updated.localeCompare(a.updated));
  const line = error || status || (stale ? 'This link was already used or has expired · you are signed in' : user && !mine ? 'Loading…' : '');
  return (
    <div class="screen">
      <div class="ipage cb2" data-testid="cabinet">
        <div class="cb-top">
          <h1>Cabinet</h1>
          <span class={`cb-status ${error || status ? 'bad' : ''}`} role="status" data-testid="cabinet-status">
            {line}
          </span>
          {user && (
            <div class="cb-corner">
              {user.reviewer && (
                <a class="btn sm" href="#/me?view=review" data-testid="cabinet-review">
                  Review
                </a>
              )}
              <Bell notices={mine?.notifications ?? null} initiallyOpen={tab === 'notifications'} reload={reload} />
              <AccountMenu user={user} initiallyOpen={tab === 'account'} />
            </div>
          )}
        </div>
        {!ready ? (
          <div class="mono-s">Loading…</div>
        ) : !user ? (
          <SignIn expired={p['signin']} />
        ) : !mine ? null : view === 'badges' ? (
          <div class="card glass cb-card" data-testid="panel-score">
            <a class="btn sm cb-back" href="#/me">
              ← Cabinet
            </a>
            <ScorePanel />
          </div>
        ) : view === 'review' ? (
          <div class="card glass cb-card">
            <a class="btn sm cb-back" href="#/me">
              ← Cabinet
            </a>
            <ReviewPanel />
          </div>
        ) : view === 'inv' ? (
          <div class="card glass cb-card" data-testid="panel-investigations">
            {opened ? <InvestigationPage inv={opened} marks={mine.marks} finding={findingOf(opened)} reload={reload} /> : <Empty text="No such investigation" />}
          </div>
        ) : (
          <>
            <Hero mine={mine} take={take} />
            {mine.score && <BadgeStrip score={mine.score} />}
            <div class="cb-cols">
              <section class="card glass cb-invs" data-testid="panel-investigations">
                <div class="cb-secthead">
                  <h3>
                    Investigations <span class="cb-n">{invs.length}</span>
                  </h3>
                  <a class="cb-link" href="#/lab">
                    Open the lab
                  </a>
                </div>
                {invs.length === 0 ? (
                  <Empty text="No investigations yet. An investigation keeps the objects you follow together: in the lab, select an object and Add to investigation" />
                ) : (
                  <div class="cb-invlist">
                    {invs.map((inv) => (
                      <InvCard key={inv.id} inv={inv} marks={mine.marks} finding={findingOf(inv)} />
                    ))}
                  </div>
                )}
              </section>
              <WorkPanel mine={mine} work={work} run={run} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
