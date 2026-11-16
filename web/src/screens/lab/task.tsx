// The Task panel of the lab (product spec §5) and the checks before submit (§6). The panel is shown only when the lab was opened
// with a task: `task=<task id>&kind=cell|finding|submit` (+ `cell=` / `finding=`), or `task=cell|finding|submit` with the same ids,
// in which case the lab takes the task itself. Without a task the lab is as it always was.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { Mark } from '../../lib/marks';

export type TaskKind = 'cell' | 'finding' | 'submit';
const KINDS: TaskKind[] = ['cell', 'finding', 'submit'];

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
export function useChecks(): { view: ChecksView; start: (fieldKey: string, ra: number, dec: number, marks: Mark[]) => void; reset: () => void } {
  const [view, setView] = useState<ChecksView>(CHECKS_IDLE);
  const run = useRef(0);
  useEffect(() => () => void run.current++, []);
  const start = (fieldKey: string, ra: number, dec: number, marks: Mark[]) => {
    const me = ++run.current;
    setView({ ...parseChecks(null, null), state: 'running' });
    void (async () => {
      try {
        const r = await fetch('/api/checks', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            field_key: fieldKey,
            ra,
            dec,
            marks: marks.filter((m) => m.pass != null && m.level !== 'artefact').map((m) => ({ pass: m.pass, ra: m.ra, dec: m.dec })),
          }),
        });
        if (!r.ok) throw new Error(String(r.status));
        const job = String(((await r.json()) as { job?: string }).job ?? '');
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
        if (run.current === me) setView((v) => ({ ...v, state: 'failed' }));
      }
    })();
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

interface NextTask {
  id: string;
  kind: 'cell' | 'finding';
  cell_id: string | null;
  finding_id: string | null;
  ra: number | null;
  dec: number | null;
}

async function takeTask(kind: 'cell' | 'finding', ref?: string | null): Promise<NextTask | null | 'failed'> {
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

/** The lab address of a task: its place, the size its kind is looked at (a square 20′, a finding 5′), and the task itself. */
export function taskParams(t: NextTask): Record<string, string> | null {
  if (t.ra == null || t.dec == null) return null;
  return {
    ra: t.ra.toFixed(5),
    dec: t.dec.toFixed(5),
    fov: t.kind === 'cell' ? '20' : '5',
    task: t.id,
    kind: t.kind,
    ...(t.cell_id ? { cell: t.cell_id } : {}),
    ...(t.finding_id ? { finding: t.finding_id } : {}),
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
    void takeTask(task.kind, task.kind === 'cell' ? task.cell_id : task.finding_id).then((t) => {
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
  // a finding to check, a find to submit: the checks of the place start as soon as the field is known
  const wantChecks = !!task && task.kind === 'finding' && !!a.fieldKey;
  useEffect(() => {
    if (wantChecks && a.fieldKey) checks.start(a.fieldKey, a.ra, a.dec, a.marksHere);
  }, [wantChecks, a.fieldKey, key]);
  if (!task) return null;

  const close = async (result: string) => {
    if (busy) return;
    setBusy(true);
    setLine('saving…');
    const markIds = a.marksHere.map((m) => m.id);
    if (task.kind === 'cell' && refs.cell) await post(`/api/grid/cell/${encodeURIComponent(refs.cell)}/viewed`, { result });
    // closing needs an account; a guest's answer still counts the square as viewed, and the next task opens
    if (id) await post(`/api/me/tasks/${encodeURIComponent(id)}/done`, { result, mark_ids: markIds });
    const next = await takeTask(task.kind as 'cell' | 'finding');
    setBusy(false);
    const to = next && next !== 'failed' && next.id !== id ? taskParams(next) : null;
    if (to) a.open(to);
    else setLine(next === 'failed' ? 'Tasks did not answer' : 'No more tasks of this kind');
  };
  const submit = async () => {
    if (!checks.view.can_submit || !checks.view.job || busy) return;
    if (!task.investigation_id) return setLine('Save to an investigation first');
    setBusy(true);
    const r = await post('/api/findings/submit', { investigation_id: task.investigation_id, checks_job: checks.view.job });
    setBusy(false);
    const fid = (r.json as { finding_id?: string } | null)?.finding_id;
    setLine(r.ok && fid ? `Submitted: ${fid}` : r.status === 401 ? 'Sign in to submit' : 'Submit did not answer');
  };

  const btn = (text: string, onClick: () => void, off = false, primary = false, testid = '') => (
    <button class={`btn sm${primary ? ' primary' : ''}`} disabled={busy || off} onClick={onClick} data-testid={testid}>
      {text}
    </button>
  );
  const n = a.marksHere.length;
  return (
    <div class={`taskpanel k-${task.kind}`} data-testid="task-panel">
      <div class="tp-head">
        <b>Task</b>
        <span>{task.kind === 'cell' ? 'Look through a square' : task.kind === 'finding' ? 'Check a machine finding' : 'Bring a finding to submit'}</span>
      </div>
      <div class="tp-ask">
        {task.kind === 'cell' ? 'Mark everything that moves and has no ring' : task.kind === 'finding' ? 'Is this a real object?' : 'Checks before submit'}
      </div>
      {task.kind !== 'cell' && <ChecksList view={checks.view} />}
      <div class="tp-line" data-testid="task-line">
        {line || (task.kind === 'cell' ? `${n} mark${n === 1 ? '' : 's'} in this square` : checksStatus(checks.view))}
      </div>
      <div class="tp-btns">
        {task.kind === 'cell' && (
          <>
            {btn('Nothing here', () => void close('nothing'), false, false, 'task-nothing')}
            {btn('Done, with marks', () => void close('marks'), n === 0, true, 'task-marks')}
          </>
        )}
        {task.kind === 'finding' && (
          <>
            {btn('It moves', () => void close('moves'), false, true, 'task-moves')}
            {btn('Artefact', () => void close('artefact'), false, false, 'task-artefact')}
            {btn('Known object', () => void close('known'), false, false, 'task-known')}
          </>
        )}
        {task.kind === 'submit' &&
          (checks.view.can_submit
            ? btn('Submit', () => void submit(), false, true, 'task-submit')
            : btn(
                'Check and submit',
                () => a.fieldKey && checks.start(a.fieldKey, a.ra, a.dec, a.marksHere),
                !a.fieldKey || checks.view.state === 'running',
                true,
                'task-check',
              ))}
      </div>
    </div>
  );
}
