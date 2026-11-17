// The Task panel of the lab (product spec §5) and the checks before submit (§6). The panel is shown only when the lab was opened
// with a task: `task=<task id>&kind=cell|finding|submit` (+ `cell=` / `finding=`), or `task=cell|finding|submit` with the same ids,
// in which case the lab takes the task itself. Without a task the lab is as it always was.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { Mark } from '../../lib/marks';

export type TaskKind = 'cell' | 'finding' | 'submit' | 'moves' | 'red' | 'anomaly';
const KINDS: TaskKind[] = ['cell', 'finding', 'submit', 'moves', 'red', 'anomaly'];

/** The kinds of task (product spec §5, §5b): the panel's title, its one question, and the answers ([result sent, button text, tooltip]). */
export const TASKS: Record<TaskKind, { title: string; ask: string; answers: [string, string, string?][] }> = {
  cell: { title: 'Look through a square', ask: 'Mark everything that moves and has no ring', answers: [['nothing', 'Nothing here'], ['marks', 'Done, with marks']] },
  moves: {
    title: 'Does it really move?',
    ask: 'The machine says this moves. Does it?',
    answers: [['moves', 'It moves'], ['still', 'It stands still'], ['artefact', 'It is an artefact'], ['cant_tell', 'Can’t tell']],
  },
  finding: {
    title: 'Check a machine finding',
    ask: 'Is this a real object?',
    answers: [['moves', 'It moves'], ['artefact', 'Artefact'], ['known', 'Known object'], ['cant_tell', 'Can’t tell']],
  },
  red: {
    title: 'What is this red source?',
    ask: 'Why is it red?',
    answers: [
      ['brown_dwarf', 'Cold brown dwarf', 'Methane and water dips'],
      ['dusty_star', 'Dusty or reddened star'],
      ['galaxy', 'Galaxy or extended'],
      ['artefact', 'Artefact'],
      ['cant_tell', 'Can’t tell'],
    ],
  },
  anomaly: {
    title: 'What is this spectral anomaly?',
    ask: 'Is this feature real?',
    answers: [['real', 'Real feature'], ['detector', 'Detector edge or ghost'], ['cosmic_ray', 'Cosmic ray or one bad exposure'], ['cant_tell', 'Can’t tell']],
  },
  submit: { title: 'Bring a finding to submit', ask: 'Checks before submit', answers: [] },
};

/** A task about one object: that object alone gets the bright ring and its label, the rest is dimmed (spec §5b). */
export const focusKind = (k: TaskKind): boolean => k !== 'cell';

export interface LabTask {
  id: string | null;
  kind: TaskKind;
  cell_id: string | null;
  finding_id: string | null;
  /** the investigation a submit task belongs to */
  investigation_id: string | null;
}

/** The task of a lab address, or null when the lab was opened without one. */
export function taskOfParams(p: Record<string, string>): LabTask | null {
  const t = p['task'];
  if (!t) return null;
  const named = (KINDS as string[]).includes(t);
  const finding = p['finding'] ?? null;
  const kind = (KINDS as string[]).includes(p['kind'] ?? '')
    ? (p['kind'] as TaskKind)
    : named
      ? (t as TaskKind)
      : finding || p['fov'] === '5'
        ? 'finding' // the cabinet's link gives the task id only: a finding is looked at in a 5′ field, a square in 20′
        : 'cell';
  return { id: named ? null : t, kind, cell_id: p['cell'] ?? null, finding_id: finding, investigation_id: p['inv'] ?? null };
}

export const CHECK_IDS = ['two_passes', 'straight_line', 'gaia', 'jpl', 'simbad', 'edge_bright', 'above_bar', 'neowise'] as const;
export const CHECK_WORD: Record<string, string> = {
  two_passes: 'Seen in two passes',
  straight_line: 'Shifts on one line',
  gaia: 'Not a Gaia star',
  jpl: 'Not a known Solar System body',
  simbad: 'Not in SIMBAD',
  edge_bright: 'Not at the edge or a bright star',
  above_bar: 'Above this field’s bar',
  neowise: 'NEOWISE archive',
};
export type CheckState = 'pass' | 'warn' | 'fail' | 'no_answer' | 'waiting';
export interface ChecksView {
  /** idle: not asked · running · done · failed: the service did not answer */
  state: 'idle' | 'running' | 'done' | 'failed';
  job: string | null;
  checks: { id: string; state: CheckState; detail: string }[];
  can_submit: boolean;
}
export const CHECKS_IDLE: ChecksView = { state: 'idle', job: null, checks: [], can_submit: false };

/** The reply of `GET /api/checks/{job}` made safe: every check of the list has a row, in the list's order. */
export function parseChecks(raw: unknown, job: string | null): ChecksView {
  const r = (raw ?? {}) as { state?: string; checks?: { id?: string; state?: string; detail?: unknown }[]; can_submit?: boolean };
  const rows = new Map((Array.isArray(r.checks) ? r.checks : []).map((c) => [String(c.id), c]));
  const checks = CHECK_IDS.map((id) => {
    const c = rows.get(id);
    const s = c?.state;
    const state: CheckState = s === 'pass' || s === 'warn' || s === 'fail' || s === 'no_answer' ? s : 'waiting';
    return { id, state, detail: typeof c?.detail === 'string' ? c.detail : '' };
  });
  const done = r.state === 'done' || r.state === 'error';
  return { state: done ? 'done' : 'running', job, checks, can_submit: r.can_submit === true };
}

const POLL_MS = 3000;

/** Runs the checks of a place and its marks (`POST /api/checks`), then follows the job until every check has answered. */
export function useChecks(): {
  view: ChecksView;
  /** starts the checks; resolves with the job id once the service has taken them (null when it did not) */
  start: (fieldKey: string, ra: number, dec: number, marks: Mark[]) => Promise<string | null>;
  reset: () => void;
} {
  const [view, setView] = useState<ChecksView>(CHECKS_IDLE);
  const run = useRef(0);
  useEffect(() => () => void run.current++, []);
  const start = (fieldKey: string, ra: number, dec: number, marks: Mark[]) => {
    const me = ++run.current;
    setView({ ...parseChecks(null, null), state: 'running' });
    let taken: (job: string | null) => void = () => {};
    const jobId = new Promise<string | null>((res) => (taken = res));
    void (async () => {
      try {
        const r = await fetch('/api/checks', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            field_key: fieldKey,
            ra,
            dec,
            marks: marks
              .filter((m) => m.pass != null && m.level !== 'artefact')
              .map((m) => ({ pass: m.pass, ra: m.ra, dec: m.dec, ...(m.mjd != null ? { mjd: m.mjd } : {}) })),
          }),
        });
        if (!r.ok) throw new Error(String(r.status));
        const job = String(((await r.json()) as { job?: string }).job ?? '');
        taken(job || null);
        for (let fails = 0; run.current === me; ) {
          const g = await fetch(`/api/checks/${encodeURIComponent(job)}`).catch(() => null);
          if (run.current !== me) return;
          if (g?.ok) {
            const v = parseChecks(await g.json(), job);
            setView(v);
            // a check with no answer is asked again by the service: the list is followed until all have answered
            if (v.state === 'done' && v.checks.every((c) => c.state !== 'no_answer' && c.state !== 'waiting')) return;
            fails = 0;
          } else if (++fails > 5) throw new Error('no answer');
          await new Promise((res) => setTimeout(res, POLL_MS));
        }
      } catch {
        taken(null);
        if (run.current === me) setView((v) => ({ ...v, state: 'failed' }));
      }
    })();
    return jobId;
  };
  return {
    view,
    start,
    reset: () => {
      run.current++;
      setView(CHECKS_IDLE);
    },
  };
}

const STATE_WORD: Record<CheckState, string> = { pass: 'pass', warn: 'warn', fail: 'fail', no_answer: 'no answer', waiting: '…' };

/** The list of checks with their state: always eight rows, so the panel keeps its height while they answer. */
export function ChecksList({ view }: { view: ChecksView }) {
  const rows = view.checks.length ? view.checks : CHECK_IDS.map((id) => ({ id, state: 'waiting' as CheckState, detail: '' }));
  return (
    <div class="checks" data-testid="checks-list">
      {rows.map((c) => (
        <div key={c.id} class={`chk ${view.state === 'idle' ? 'idle' : c.state}`} title={c.detail}>
          <i />
          <span>{CHECK_WORD[c.id] ?? c.id}</span>
          <em>{view.state === 'idle' ? '' : view.state === 'failed' && c.state === 'waiting' ? 'no answer' : STATE_WORD[c.state]}</em>
        </div>
      ))}
    </div>
  );
}

/** One line under a checks list: what the list says as a whole. */
export function checksStatus(v: ChecksView): string {
  if (v.state === 'idle') return '';
  if (v.state === 'failed') return 'Checks did not answer';
  const n = v.checks.filter((c) => c.state === 'pass' || c.state === 'warn' || c.state === 'fail').length;
  if (v.checks.some((c) => c.state === 'fail')) return `${n} of ${v.checks.length} answered · a failed check blocks the submit`;
  if (n < v.checks.length) return `${n} of ${v.checks.length} answered`;
  return v.checks.some((c) => c.state === 'warn') ? 'All answered · a warning needs a comment' : 'All answered';
}

async function post(path: string, body: unknown): Promise<{ ok: boolean; status: number; json: unknown }> {
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    return { ok: r.ok, status: r.status, json: r.ok ? await r.json().catch(() => null) : null };
  } catch {
    return { ok: false, status: 0, json: null };
  }
}

/** An investigation as the account gives it back after "Check and submit" was declared. */
export interface DeclaredInv {
  id: string;
  state?: 'checking' | 'needs_comment' | 'blocked' | 'submitted' | string;
  progress?: string;
  submitted?: string | null;
}

/** The status of a declared submit: the slow checks run on the server, which submits by itself; the user may leave. */
export function declaredWord(inv: DeclaredInv | null, status = 200): string {
  if (!inv) return status === 401 ? 'Sign in to submit' : status === 404 ? 'No such investigation' : 'Submit did not answer';
  if (inv.state === 'submitted' || inv.submitted) return `Submitted${typeof inv.submitted === 'string' ? `: ${inv.submitted}` : ''}`;
  if (inv.state === 'blocked') return 'Not submitted: a check failed';
  if (inv.state === 'needs_comment') return 'Submit declared · a warning needs a comment';
  return `Submit declared · checking${inv.progress ? `: ${inv.progress}` : '…'}`;
}

/** The same state as the investigation chip says it: "checking 7 of 8", "submitted", "blocked". */
export function chipWord(inv: DeclaredInv | null): string {
  if (!inv) return '';
  if (inv.state === 'submitted' || inv.submitted) return 'submitted';
  if (inv.state === 'blocked') return 'a check failed';
  if (inv.state === 'needs_comment') return 'needs a comment';
  const m = (inv.progress ?? '').match(/(\d+) of (\d+)/);
  return m ? `checking ${m[1]} of ${m[2]}` : 'checking';
}

/** Declare the submit of an investigation with its checks job (`POST /api/me/investigations/{id}/submit`). */
export async function declareSubmit(invId: string, job: string, note = ''): Promise<{ inv: DeclaredInv | null; status: number }> {
  const r = await post(`/api/me/investigations/${encodeURIComponent(invId)}/submit`, { checks_job: job, ...(note ? { note } : {}) });
  return { inv: r.ok ? (r.json as DeclaredInv | null) : null, status: r.status };
}

/** Follow a declared submit while the server is still checking: `on` gets the investigation every few seconds. Returns the stop. */
export function followDeclared(invId: string, on: (inv: DeclaredInv) => void): () => void {
  let stop = false;
  void (async () => {
    while (!stop) {
      await new Promise((res) => setTimeout(res, 5000));
      if (stop) return;
      try {
        const r = await fetch('/api/me/investigations');
        if (!r.ok) return;
        const inv = ((await r.json()) as { investigations?: DeclaredInv[] }).investigations?.find((x) => x.id === invId);
        if (!inv) return;
        on(inv);
        if (inv.state !== 'checking') return;
      } catch {
        return;
      }
    }
  })();
  return () => {
    stop = true;
  };
}

/** What a closed task earned, said on the lab's status line for a while (it outlives the task: the next one opens at once). */
let award = { text: '', until: 0 };
export function awardLine(): string {
  return Date.now() < award.until ? award.text : '';
}

type Takeable = Exclude<TaskKind, 'submit'>;
interface NextTask {
  id: string;
  kind: Takeable;
  cell_id: string | null;
  finding_id: string | null;
  ra: number | null;
  dec: number | null;
  /** the field size the find was made in, arcminutes (when the service says it) */
  fov?: number | null;
}

async function takeTask(kind: Takeable, ref?: string | null): Promise<NextTask | null | 'failed'> {
  try {
    const q = new URLSearchParams({ kind });
    if (ref) q.set(kind === 'cell' ? 'cell_id' : 'finding_id', ref);
    const r = await fetch(`/api/tasks/next?${q.toString()}`);
    if (!r.ok) return 'failed';
    return ((await r.json()) as { task: NextTask | null }).task;
  } catch {
    return 'failed';
  }
}

/** The lab address of a task: its place, the size it is looked at (the service's, else a square 20′ and a find 5′), and the task itself. */
export function taskParams(t: NextTask): Record<string, string> | null {
  if (t.ra == null || t.dec == null) return null;
  return {
    ra: t.ra.toFixed(5),
    dec: t.dec.toFixed(5),
    fov: String(t.fov && t.fov > 0 ? t.fov : t.kind === 'cell' ? 20 : 5),
    task: t.id,
    kind: t.kind,
    ...(t.cell_id ? { cell: t.cell_id } : {}),
    // the find is the lab's selected object (`find`): the one thing the task rings brightly
    ...(t.finding_id ? { finding: t.finding_id, find: t.finding_id } : {}),
  };
}

export interface TaskArgs {
  params: Record<string, string>;
  /** opens another lab address (the next task) */
  open: (params: Record<string, string>) => void;
  ra: number;
  dec: number;
  fieldKey: string | null;
  marksHere: Mark[];
  /** the pass on show (null in Days) and how many passes the field has: a square is looked through when every pass was shown */
  pass: number | null;
  passesTotal: number;
  /** what the lab shows for the task's question (the mover's blink and ruler, the red source's spectrum, the anomaly's bins) */
  extra?: ComponentChildren;
  /** the other overlays are on show (off by default in a task about one object); and the switch for it */
  others: boolean;
  setOthers: (on: boolean) => void;
}

/** The Task panel, or null when the lab has no task. */
export function useLabTask(a: TaskArgs): ComponentChildren {
  const task = taskOfParams(a.params);
  const key = task ? `${task.kind}|${task.id}|${task.cell_id}|${task.finding_id}` : '';
  const [id, setId] = useState<string | null>(task?.id ?? null);
  const [refs, setRefs] = useState<{ cell: string | null; finding: string | null }>({ cell: task?.cell_id ?? null, finding: task?.finding_id ?? null });
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState('');
  const checks = useChecks();
  // how the task was looked at: seconds with the lab visible, and which passes were on show
  const look = useRef({ seconds: 0, passes: new Set<number>() });
  if (a.pass != null) look.current.passes.add(a.pass);
  useEffect(() => {
    look.current = { seconds: 0, passes: new Set<number>() };
    const t = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') look.current.seconds++;
    }, 1000);
    return () => clearInterval(t);
  }, [key]);
  const followStop = useRef<() => void>(() => {});
  useEffect(() => () => followStop.current(), [key]);
  // a lab opened with the kind only (from Findings, from the globe): the lab takes the task itself
  useEffect(() => {
    setId(task?.id ?? null);
    setRefs({ cell: task?.cell_id ?? null, finding: task?.finding_id ?? null });
    setLine('');
    checks.reset();
    if (!task || task.kind === 'submit') return;
    let stop = false;
    if (task.id) {
      // a task opened by its id alone (the cabinet): its square or finding is in the user's own list of tasks
      if (task.cell_id || task.finding_id) return;
      void fetch('/api/me/tasks')
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { tasks?: NextTask[] } | null) => {
          const t = d?.tasks?.find((x) => x.id === task.id);
          if (t && !stop) setRefs({ cell: t.cell_id, finding: t.finding_id });
        })
        .catch(() => {});
      return () => {
        stop = true;
      };
    }
    void takeTask(task.kind as Takeable, task.kind === 'cell' ? task.cell_id : task.finding_id).then((t) => {
      if (stop) return;
      if (t === 'failed' || !t) setLine(t === 'failed' ? 'Tasks did not answer' : 'No task here');
      else {
        setId(t.id);
        setRefs({ cell: t.cell_id, finding: t.finding_id });
      }
    });
    return () => {
      stop = true;
    };
  }, [key]);
  // a finding to check: the checks need the user's marks of the object (they compare its places over the passes), so they run as
  // soon as there is a mark, and again when the marks change; without marks the list waits (it never shows a fail nobody earned)
  const real = a.marksHere.filter((m) => m.level !== 'artefact' && m.pass != null);
  const marksKey = real.map((m) => `${m.id}:${m.pass}:${m.ra.toFixed(6)}:${m.dec.toFixed(6)}`).join(';');
  const wantChecks = !!task && task.kind === 'finding' && !!a.fieldKey && real.length > 0;
  useEffect(() => {
    if (wantChecks && a.fieldKey) checks.start(a.fieldKey, real[0]!.ra, real[0]!.dec, real);
    else checks.reset();
  }, [wantChecks, a.fieldKey, key, marksKey]);
  if (!task) return null;

  const close = async (result: string) => {
    if (busy) return;
    setBusy(true);
    setLine('saving…');
    const markIds = a.marksHere.map((m) => m.id);
    if (task.kind === 'cell' && refs.cell) await post(`/api/grid/cell/${encodeURIComponent(refs.cell)}/viewed`, { result });
    // closing needs an account; a guest's answer still counts the square as viewed, and the next task opens
    if (id) {
      const r = await post(`/api/me/tasks/${encodeURIComponent(id)}/done`, {
        result,
        mark_ids: markIds,
        seconds: look.current.seconds,
        passes_shown: look.current.passes.size,
        passes_total: a.passesTotal,
        local_hour: new Date().getHours(),
      });
      // what the answer earned: one status item each, on the lab's own status line
      const got = (r.json ?? {}) as {
        points?: number;
        achievements?: { name?: string }[];
        agreement?: { answers?: number; needed?: number };
      };
      const items = [
        ...(got.achievements ?? []).map((x) => `Achievement: ${x.name ?? ''}`),
        ...(typeof got.points === 'number' && got.points > 0 ? [`+${got.points} points`] : []),
        // red sources and anomalies settle by agreement: how many answers the find has so far
        ...(got.agreement?.answers != null ? [`${got.agreement.answers} of ${got.agreement.needed ?? 3} answers`] : []),
      ];
      if (items.length) award = { text: items.join(' · '), until: Date.now() + 12000 };
    }
    const next = await takeTask(task.kind as Takeable);
    setBusy(false);
    const to = next && next !== 'failed' && next.id !== id ? taskParams(next) : null;
    if (to) a.open(to);
    else setLine(next === 'failed' ? 'Tasks did not answer' : 'No more tasks of this kind');
  };
  // "Check and submit", said once: the checks start, the submit is declared, the server finishes it; the user may leave
  const declare = async () => {
    if (!a.fieldKey || busy) return;
    if (!task.investigation_id) return setLine('Save to an investigation first');
    setBusy(true);
    setLine('starting the checks…');
    const job = await checks.start(a.fieldKey, a.ra, a.dec, a.marksHere);
    if (!job) {
      setBusy(false);
      return setLine('Checks did not answer');
    }
    const r = await declareSubmit(task.investigation_id, job);
    setBusy(false);
    setLine(declaredWord(r.inv, r.status));
    followStop.current();
    if (r.inv?.state === 'checking') followStop.current = followDeclared(task.investigation_id, (inv) => setLine(declaredWord(inv)));
  };

  const btn = (text: string, onClick: () => void, off = false, primary = false, testid = '') => (
    <button class={`btn sm${primary ? ' primary' : ''}`} disabled={busy || off} onClick={onClick} data-testid={testid}>
      {text}
    </button>
  );
  const n = a.marksHere.length;
  const T = TASKS[task.kind];
  return (
    <div class={`taskpanel k-${task.kind}`} data-testid="task-panel">
      <div class="tp-head">
        <b>Task</b>
        <span>{T.title}</span>
        {focusKind(task.kind) && (
          <label class="tp-others" title="Show the other rings and names of the field as usual (in a task only the object in question is bright)">
            <input type="checkbox" checked={a.others} onChange={(e) => a.setOthers((e.target as HTMLInputElement).checked)} data-testid="task-others" />
            <span>Other objects</span>
          </label>
        )}
      </div>
      <div class="tp-ask">{T.ask}</div>
      {(task.kind === 'finding' || task.kind === 'submit') && <ChecksList view={checks.view} />}
      {a.extra}
      <div class="tp-line" data-testid="task-line">
        {line ||
          (task.kind === 'cell'
            ? `${n} mark${n === 1 ? '' : 's'} in this square`
            : task.kind === 'finding' || task.kind === 'submit'
              ? checksStatus(checks.view) || (task.kind === 'finding' ? 'Checks wait for a mark on the object' : '')
              : '')}
      </div>
      <div class={`tp-btns n${T.answers.length}`}>
        {T.answers.map(([result, text, tip], k) => (
          <button
            key={result}
            class={`btn sm${(task.kind === 'cell' ? result === 'marks' : k === 0) ? ' primary' : ''}${result === 'cant_tell' ? ' quiet' : ''}`}
            disabled={busy || (task.kind === 'cell' && result === 'marks' && n === 0)}
            title={tip}
            onClick={() => void close(result)}
            data-testid={`task-${result}`}
          >
            {text}
          </button>
        ))}
        {task.kind === 'submit' && btn('Check and submit', () => void declare(), !a.fieldKey || checks.view.state === 'running', true, 'task-check')}
      </div>
    </div>
  );
}
