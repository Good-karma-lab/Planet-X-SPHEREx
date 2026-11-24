// The Task panel of the lab (product spec §5, §5b). The panel is shown only when the lab was opened with a task:
// `task=<task id>&kind=cell|finding|moves|red|anomaly` (+ `cell=` / `finding=`), or `task=<kind>` with the same ids, in which case
// the lab takes the task itself. Without a task the lab is as it always was. A task closes with its answer only: notes, the checks
// and the submit are in the Investigation page of the Cabinet, never in the lab.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { Mark } from '../../lib/marks';
import { handedTask, handTask, skipGiven, leadParams, type Task } from '../../lib/account';
import { clearDo, pendingDo, useSignIn } from './signin';

export type TaskKind = 'cell' | 'finding' | 'moves' | 'red' | 'anomaly' | 'find_again' | 'recentre' | 'bad_picture' | 'second_look';
const KINDS: TaskKind[] = ['cell', 'finding', 'moves', 'red', 'anomaly', 'find_again', 'recentre', 'bad_picture', 'second_look'];

/** The kinds of task (product spec §5, §5e): the panel's title, the question when there is no series, and its answers
 *  ([result sent, button text]). A find's task asks the question series of its kind instead (SERIES), one step at a time. */
export const TASKS: Record<TaskKind, { title: string; ask: string; answers: [string, string][] }> = {
  cell: { title: 'Look through a square', ask: 'Mark everything that moves and has no ring', answers: [['nothing', 'Nothing here'], ['marks', 'Done, with marks']] },
  finding: { title: 'Check a machine finding', ask: 'Is this a real object?', answers: [['moves', 'It moves'], ['artefact', 'Artefact'], ['known', 'Known object'], ['cant_tell', 'Can’t tell']] },
  moves: { title: 'Does it really move?', ask: 'The machine says this moves. Does it?', answers: [['moves', 'It moves'], ['still', 'It stands still'], ['artefact', 'It is an artefact'], ['cant_tell', 'Can’t tell']] },
  red: { title: 'A red source', ask: '', answers: [] },
  anomaly: { title: 'An odd spectrum', ask: '', answers: [] },
  find_again: { title: 'Find it again', ask: 'Tap it if you see it.', answers: [['seen', 'Done'], ['not_seen', 'I don’t see it'], ['cant_tell', 'Not sure']] },
  recentre: {
    title: 'Put the ring on it',
    ask: 'Tap the centre of the object in each picture where the ring sits off it.',
    answers: [['done', 'Done'], ['cant_tell', 'Not sure']],
  },
  bad_picture: {
    title: 'This picture is broken',
    ask: 'Does this picture look broken (a streak, cut off, glare)?',
    answers: [['broken', 'Yes, broken'], ['fine', 'No, it looks fine'], ['cant_tell', 'Not sure']],
  },
  second_look: { title: 'Second look', ask: 'Is this a real object?', answers: [['moves', 'It moves'], ['artefact', 'Artefact'], ['cant_tell', 'Can’t tell']] },
};

/** One step of a question series (spec §5e): the question, what the lab shows with it (`show`), whether the person taps pictures
 *  (`tap`), and the answers; an answer goes on to the next step or ends the series with a result the service acts on. */
export interface SeriesStep {
  id: string;
  question: string;
  /** blink (a track's visits) · pictures (tap where it is missing) · blink2 (two passes) · old_surveys · sums (a peak's two sums)
   *  · picture · passes · old_new · stamps (an anomaly's odd exposures) */
  show: string;
  tap?: boolean;
  answers: { id: string; label: string; next?: string; result?: string }[];
}
/** (kept for the earlier name) */
export type TreeStep = SeriesStep;

const NOT_SURE = { id: 'not_sure', label: 'Not sure', result: 'cant_tell' };
const s_ = (id: string, question: string, show: string, answers: SeriesStep['answers'], tap = false): SeriesStep => ({
  id,
  question,
  show,
  ...(tap ? { tap } : {}),
  answers: tap ? answers : [...answers, NOT_SURE],
});
/** The series as the service sends them in the task (service/accounts/people.py SERIES, the user's final wording), kept here too
 *  for a task whose body the lab does not have. The service turns the path into the result; the lab only walks the steps. */
export const SERIES: Record<string, SeriesStep[]> = {
  track: [
    s_('sum', 'It blinks: the visits added along the motion, then added without it. Is there a dot in the ring in the first only?', 'sums', [
      { id: 'yes', label: 'Yes', next: 'steps' },
      { id: 'no', label: 'No', result: 'no_dot' },
    ]),
    s_('steps', 'Does one dot step along the arrow?', 'blink', [
      { id: 'every', label: 'Yes, in every picture', next: 'shape' },
      { id: 'some', label: 'Only in some', next: 'missing' },
      { id: 'no', label: 'No', result: 'no_dot' },
    ]),
    s_('missing', 'Tap the pictures where it is missing.', 'pictures', [{ id: 'done', label: 'Done', next: 'shape' }], true),
    s_('shape', 'Is it a dot like the stars, or a streak?', 'blink', [
      { id: 'dot', label: 'Dot', result: 'moves' },
      { id: 'streak', label: 'Streak', result: 'streak' },
    ]),
  ],
  slow: [
    s_('shift', 'Does the dot in the ring shift while the stars stay put?', 'blink2', [
      { id: 'yes', label: 'Yes', next: 'blend' },
      { id: 'no', label: 'No', result: 'still' },
    ]),
    s_('blend', 'Is it one dot, or two touching?', 'blink2', [
      { id: 'one', label: 'One', next: 'old' },
      { id: 'two', label: 'Two', result: 'blend' },
    ]),
    s_('old', 'Old pictures (WISE 2010, 2MASS 2000): is there a dot along the arrow, further back?', 'old_surveys', [
      { id: 'yes', label: 'Yes', result: 'moves_old' },
      { id: 'no', label: 'No', result: 'moves' },
    ]),
  ],
  peak: [
    s_('left', 'It blinks: the visits added along the orbit, then one pass. Is there a dot in the ring in the sum only?', 'sums', [
      { id: 'yes', label: 'Yes', next: 'glow' },
      { id: 'no', label: 'No', result: 'no_peak' },
    ]),
    s_('glow', 'Is it inside a bright star’s glow or on its spike?', 'picture', [
      { id: 'yes', label: 'Yes', result: 'glow' },
      { id: 'no', label: 'No', result: 'candidate' },
    ]),
  ],
  red: [
    s_('shape', 'A sharp dot, or a fuzzy patch?', 'picture', [
      { id: 'sharp', label: 'A sharp dot', next: 'every' },
      { id: 'fuzzy', label: 'A fuzzy patch', result: 'galaxy' },
    ]),
    s_('every', 'Is it there every time?', 'passes', [
      { id: 'yes', label: 'Yes', next: 'glow' },
      { id: 'no', label: 'No', result: 'artefact' },
    ]),
    s_('glow', 'Is it on a bright star’s glow, a spike, or the picture edge?', 'picture', [
      { id: 'yes', label: 'Yes', result: 'artefact' },
      { id: 'no', label: 'No', next: 'moved' },
    ]),
    s_('moved', 'Old picture next to new: has it moved?', 'old_new', [
      { id: 'yes', label: 'Yes', result: 'brown_dwarf' },
      { id: 'no', label: 'No', result: 'still' },
    ]),
  ],
  anomaly: [
    s_('neighbour', 'In the odd pictures, does a neighbour star touch it?', 'stamps', [
      { id: 'yes', label: 'Yes', result: 'neighbour' },
      { id: 'no', label: 'No', next: 'broken' },
    ]),
    s_('broken', 'Do the odd pictures look broken (a streak, cut off, glare)?', 'stamps', [
      { id: 'yes', label: 'Yes', result: 'broken' },
      { id: 'no', label: 'No', result: 'real' },
    ]),
  ],
};
/** the series of a find, by the machine's kind of find (the prefix of its id) */
export const SERIES_OF_KIND: Record<string, string> = { film: 'track', mover: 'slow', browndwarf: 'slow', peak: 'peak', red: 'red', anomaly: 'anomaly' };
/** small examples beside an answer, where they help (series.step.answer → pictures in /examples) */
export const EXAMPLES: Record<string, string[]> = {
  'red.shape.sharp': ['star_point'],
  'red.shape.fuzzy': ['known_galaxy'],
  'red.glow.yes': ['bright_star_ghost', 'picture_edge'],
  'peak.glow.yes': ['bright_star_ghost'],
  'track.shape.streak': ['satellite_streak'],
  'anomaly.broken.yes': ['cosmic_ray', 'picture_edge'],
};

/** The step a path of answers has reached: the first step for an empty path; null when the path has ended or is not of the series. */
export function stepAt(series: SeriesStep[], path: string[]): SeriesStep | null {
  let step: SeriesStep | null = series[0] ?? null;
  for (const answer of path) {
    const a = step?.answers.find((x) => x.id === answer);
    if (!a || !a.next) return null;
    step = series.find((x) => x.id === a.next) ?? null;
  }
  return step;
}

/** A task about one object: that object alone gets the bright ring and its label, the rest is dimmed (spec §5b). */
/** A track's task on the picture: its place in each visit of the pass (the task's pictures) and the visits tapped as missing.
 *  The task writes it as it renders, the lab's overlay (flow.tsx) draws it: the path with its arrow, a dot per visit, the ring on
 *  the visit shown. Empty when the task is not about a track. */
export const taskTrack: { pts: { i: number; mjd: number; ra: number; dec: number }[]; missing: number[] } = { pts: [], missing: [] };

export const focusKind = (k: TaskKind): boolean => k !== 'cell' && k !== 'bad_picture';

export interface LabTask {
  id: string | null;
  kind: TaskKind;
  cell_id: string | null;
  finding_id: string | null;
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
  return { id: named ? null : t, kind, cell_id: p['cell'] ?? null, finding_id: finding };
}

async function post(path: string, body: unknown): Promise<{ ok: boolean; status: number; json: unknown }> {
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    return { ok: r.ok, status: r.status, json: r.ok ? await r.json().catch(() => null) : null };
  } catch {
    return { ok: false, status: 0, json: null };
  }
}

/** What a closed task earned, said on the lab's status line for a while (it outlives the task: the next one opens at once). */
let award = { text: '', until: 0 };
export function awardLine(): string {
  return Date.now() < award.until ? award.text : '';
}

type Takeable = TaskKind;

/** what a series' result does once enough people agree (service/accounts/people.py ACTIONS); the rest stay leads for the checks */
const ACTION_OF: Record<string, Record<string, string>> = {
  track: { no_dot: 'drop', streak: 'drop', partial: 'relink' },
  slow: { still: 'drop', blend: 'drop', moves_old: 'raise' },
  peak: { no_peak: 'drop', glow: 'drop', candidate: 'raise' },
  red: { brown_dwarf: 'bd_queue' },
  anomaly: { neighbour: 'leave_out' },
};
const ACTION_WORD: Record<string, string> = {
  drop: 'When people agree: the find is set aside',
  relink: 'When people agree: the track is linked again without those pictures',
  raise: 'When people agree: the find moves up for the checks',
  bd_queue: 'When people agree: it goes to the brown-dwarf search',
  leave_out: 'When people agree: those exposures leave its spectrum',
};
export const nextWord = (series: string, result: string) => ACTION_WORD[ACTION_OF[series]?.[result] ?? ''] ?? 'When people agree: it stays a lead for the checks';
/** A place tapped in a picture (find_again, recentre): the pass (Months) or the visit (Days) it was tapped on, and its date. */
export interface Tap {
  ra: number;
  dec: number;
  pass?: number | null;
  frame?: number | null;
  mjd?: number | null;
}
/** A task as the service gives it (GET /api/tasks/next; flow-accounts' shapes). */
export interface NextTask {
  id: string;
  kind: Takeable;
  cell_id: string | null;
  finding_id: string | null;
  ra: number | null;
  dec: number | null;
  /** the field size the find was made in, arcminutes (when the service says it) */
  fov?: number | null;
  /** a square task: the quarter of the square to look through (fov 10) */
  quarter?: string | null;
  /** a find's question series (spec §5e) */
  series?: { id: string; steps: SeriesStep[] } | null;
  /** a square given for a lead in it: which find */
  why?: { finding_id?: string; kind?: string; promise?: number; ra?: number; dec?: number; text?: string; ask?: { kind?: string; finding_id?: string; series?: string } } | null;
  /** a time-lapse track: the date (MJD) of the film its search ran on; the lab plays that film (warmed ahead) */
  film?: number | null;
  /** a track's pictures (its visits), to tap where it is missing */
  pictures?: { i: number; mjd?: number | null; ra?: number | null; dec?: number | null }[] | null;
  /** the other kinds: their question and answers */
  question?: string | null;
  answers?: { id: string; label: string }[] | null;
  /** bad_picture: the exposure in question, and a picture of it when the service gives one */
  exposure?: string | null;
  cutout?: string | null;
  /** find_again: where the lead should be in the next pass */
  predicted?: { mjd?: number | null; path?: [number, number][] | null; ra?: number | null; dec?: number | null } | null;
}

export async function takeTask(kind: Takeable, ref?: string | null, id?: string | null): Promise<NextTask | null | 'failed'> {
  try {
    const q = new URLSearchParams({ kind });
    if (ref) q.set(kind === 'cell' ? 'cell_id' : 'finding_id', ref);
    else skipGiven(q);
    if (id && ref) q.set('task_id', id); // a guest's own task again (the service gives it to nobody else's account)
    const r = await fetch(`/api/tasks/next?${q.toString()}`);
    if (!r.ok) return 'failed';
    const t = ((await r.json()) as { task: NextTask | null }).task;
    if (t) handTask(t as unknown as Task); // the lab opens it next by its id: the same task, not another one
    return t;
  } catch {
    return 'failed';
  }
}

/** The body of the lab's task. A task opened by its id is the one handed over (Findings, the globe and the cabinet took it a moment
 *  ago: no second task for one click). Else, as before: a bare id is looked up in my tasks; an id with its square or find (a link from
 *  another tab) and a task named by its kind are taken from the service. null: nothing to show. */
export async function taskBody(task: LabTask, exposure: string | null): Promise<{ body: NextTask } | { line: string } | null> {
  if (task.id) {
    const h = handedTask(task.id);
    if (h) return { body: h as unknown as NextTask };
  }
  const ref = task.kind === 'cell' ? task.cell_id : task.kind === 'bad_picture' ? exposure : task.finding_id;
  if (task.id && !ref) {
    try {
      const r = await fetch('/api/me/tasks');
      const d = r.ok ? ((await r.json()) as { tasks?: NextTask[] }) : null;
      const t = d?.tasks?.find((x) => x.id === task.id);
      return t ? { body: t } : null;
    } catch {
      return null;
    }
  }
  const t = await takeTask(task.kind as Takeable, ref, task.id);
  if (t === 'failed' || !t) return { line: t === 'failed' ? 'Tasks did not answer' : 'No task here' };
  return { body: t };
}

/** The lab address of a task: its place, the size it is looked at (the service's, else a square 20′ and a find 5′), and the task itself. */
export function taskParams(t: NextTask): Record<string, string> | null {
  if (t.ra == null || t.dec == null) return null;
  return {
    ra: t.ra.toFixed(5),
    dec: t.dec.toFixed(5),
    // the service's field size when it gives one (a whole track fits); else as lib/account.ts taskHash: a quarter 10′, a square 20′,
    // a time-lapse its own 60′ field, any other find 10′
    fov: String(t.fov && t.fov > 0 ? t.fov : t.kind === 'cell' ? (t.quarter ? 10 : 20) : t.finding_id?.startsWith('film:') ? 60 : 10),
    task: t.id,
    kind: t.kind,
    ...(t.cell_id ? { cell: t.cell_id } : {}),
    ...(t.quarter ? { quarter: t.quarter } : {}),
    ...(t.kind === 'cell' && t.why ? leadParams(t.why) : {}),
    // the find is the lab's selected object (`find`): the one thing the task rings brightly (an exposure is no find)
    ...(t.finding_id && t.kind !== 'bad_picture' ? { finding: t.finding_id, find: t.finding_id } : {}),
    ...(t.finding_id && t.kind === 'bad_picture' ? { exposure: t.finding_id } : {}),
    // a track opens straight in Days on its search's own film
    ...(t.film != null && t.finding_id?.startsWith('film:') ? { film: String(t.film) } : {}),
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
  /** a square task: whether a look can be counted now, why not, and the count itself (lab/square.tsx) */
  square?: { canCount: boolean; why: string; frac?: number; confirm: (result?: 'nothing' | 'marks') => Promise<boolean> } | null;
  /** what the lab shows with the task: for a series step its `show`, else null (the task's kind decides) */
  view?: (show: string | null, task: NextTask | null) => ComponentChildren;
  /** the other overlays are on show (off by default in a task about one object); and the switch for it */
  others: boolean;
  setOthers: (on: boolean) => void;
  /** "Mark something the machine missed": the Mark tool on the picture */
  markMissed: () => void;
  /** shows one of a track's pictures on the picture: the visit of the pass nearest its date (MJD) */
  showPicture: (mjd: number) => void;
  /** a date's visit number in the pass on show (the time line's numbering), null when not known */
  visitOf?: (mjd: number) => number | null;
  /** the picture is drawn: until then a question is shown but not answered (nobody can answer it honestly yet) */
  ready?: boolean;
  /** a lead's questions inside a square's task: no "Next" of its own (the square's look follows), no second Mark button */
  embedded?: boolean;
  /** the service's words for the lead (why.text: "a Planet X peak · promise 86") */
  leadText?: string;
}

/** The task as the lab works with it: the panel, the step on show, the service's body, and the taps. */
export interface LabTaskView {
  panel: ComponentChildren;
  kind: TaskKind | null;
  /** the `show` of the series step on show, null without a series */
  show: string | null;
  /** the series of the task's find (track, slow, peak, red, anomaly), null without one */
  series: string | null;
  body: NextTask | null;
  /** clicks on the picture are taps (find_again, recentre) */
  tapping: boolean;
  taps: Tap[];
  addTap: (t: Tap) => void;
  /** a task about one find is open and not answered yet: what the catalogues say about that find waits (a control object, whose
   *  answer is known, must look exactly like any other, and nobody is led) */
  blind: boolean;
}

/** The Task panel and what goes with it (an empty view when the lab has no task). */
export function useLabTask(a: TaskArgs): LabTaskView {
  const task = taskOfParams(a.params);
  const key = task ? `${task.kind}|${task.id}|${task.cell_id}|${task.finding_id}` : '';
  const [id, setId] = useState<string | null>(task?.id ?? null);
  const [body, setBody] = useState<NextTask | null>(null);
  const [busy, setBusy] = useState(false);
  const si = useSignIn();
  const [line, setLine] = useState('');
  // a question series: the answers given so far, and the pictures tapped as "missing"
  const [path, setPath] = useState<string[]>([]);
  const [missing, setMissing] = useState<number[]>([]);
  const [taps, setTaps] = useState<Tap[]>([]);
  // a closed task: what was answered and what it led to, until the person opens the next one (never a silent jump)
  const [done, setDone] = useState<{
    said: [string, string][];
    got: string[];
    points: number | null;
    pending: boolean;
    next: Record<string, string> | null;
    none: string;
  } | null>(null);
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
  // the task's body: the lab takes the task itself when opened with the kind only; a task opened by its id is found in my tasks
  useEffect(() => {
    setId(task?.id ?? null);
    setBody(null);
    setLine('');
    setPath([]);
    setMissing([]);
    setTaps([]);
    setDone(null);
    if (!task) return;
    let stop = false;
    void taskBody(task, a.params['exposure'] ?? null).then((r) => {
      if (stop || !r) return;
      if ('line' in r) setLine(r.line);
      else {
        setId(r.body.id);
        setBody(r.body);
      }
    });
    return () => {
      stop = true;
    };
  }, [key]);
  // after a sign-in link: the answer the person clicked as a guest is given (a square's once its look counts)
  const want = pendingDo(a.params);
  useEffect(() => {
    if (!task || !si.signedIn || !id || !/^(answer|path):/.test(want)) return;
    if (task.kind === 'cell' && !a.square?.canCount) return;
    clearDo();
    if (want.startsWith('path:')) void close('cant_tell', want.slice(5).split('.'));
    else void close(want.slice(7));
  }, [id, si.signedIn, a.square?.canCount, want]);

  // the series of the task's find: the service's own, else the lab's copy for the kind of the find
  const seriesId = body?.series?.id ?? (task?.finding_id ? (SERIES_OF_KIND[task.finding_id.split(':')[0] ?? ''] ?? null) : null);
  const steps: SeriesStep[] | null =
    task && ['finding', 'moves', 'red', 'anomaly'].includes(task.kind) && seriesId ? (body?.series?.steps ?? SERIES[seriesId] ?? null) : null;
  const step = steps ? stepAt(steps, path) : null;
  const tapping = !!task && (task.kind === 'find_again' || task.kind === 'recentre');
  const view: LabTaskView = {
    panel: null,
    kind: task?.kind ?? null,
    show: step?.show ?? null,
    series: steps ? seriesId : null,
    body,
    tapping,
    taps,
    blind: !!task && focusKind(task.kind) && !done,
    addTap: (t) => setTaps((x) => [...x.filter((q) => (t.pass ?? t.frame) == null || (q.pass ?? q.frame) !== (t.pass ?? t.frame)), t]),
  };
  if (!task) {
    // no task: no path of an earlier one (the square's own panel, which has no lead, leaves the task's alone)
    if (!a.embedded) taskTrack.pts = [];
    return view;
  }

  async function close(result: string, answers?: string[]) {
    if (!task || busy) return;
    // a guest: the popup; the sign-in link comes back to this task with the answer, and it is given then
    if (!si.signedIn) return si.ask(task.kind === 'cell' ? 'look' : 'answer', answers ? `path:${answers.join('.')}` : `answer:${result}`);
    setBusy(true);
    setLine('saving…');
    const markIds = a.marksHere.map((m) => m.id);
    // a square task's answer counts the look only for a signed-in person after 60 s and every pass (lab/square.tsx); a guest's
    // answer counts nothing, and the next task opens all the same
    if (task.kind === 'cell' && a.square?.canCount) await a.square.confirm(result === 'marks' ? 'marks' : 'nothing');
    let gotWords: string[] = [];
    let points: number | null = null;
    let pending = false;
    if (id) {
      const r = await post(`/api/me/tasks/${encodeURIComponent(id)}/done`, {
        // a series: the path of answers and the pictures tapped as missing; the service says what it leads to
        ...(answers ? { path: answers, pictures: missing } : { result }),
        ...(tapping && taps.length ? { taps } : {}),
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
        result?: { id?: string; label?: string };
        class?: { label?: string };
        agreement?: { answers?: number; needed?: number; settled?: boolean; points_when_agreed?: number };
      };
      const items = [
        ...((got.result?.label ?? got.class?.label) ? [`Your answers say: ${got.result?.label ?? got.class?.label}`] : []),
        ...(got.achievements ?? []).map((x) => `Achievement: ${x.name ?? ''}`),
        ...(typeof got.points === 'number' && got.points > 0 ? [`+${got.points} points`] : []),
      ];
      if (items.length) award = { text: items.join(' · '), until: Date.now() + 12000 };
      // a check is scored when enough people agree (service/routes_me.py agreement_of): say where it stands, not a bare 0 points
      const ag = got.agreement;
      if (!r.ok && r.status !== 401) {
        setBusy(false);
        // the service's own words when it answered with a refusal; "did not answer" only when it did not
        const why = typeof (r.json as { detail?: unknown } | null)?.detail === 'string' ? String((r.json as { detail: string }).detail) : '';
        return setLine(r.status >= 400 && r.status < 500 ? `Not taken: ${why || `the service said ${r.status}`}` : 'The service did not answer');
      }
      gotWords = [
        ...((got.result?.label ?? got.class?.label) ? [`Your answers say: ${got.result?.label ?? got.class?.label}`] : []),
        ...(got.achievements ?? []).map((x) => `Achievement: ${x.name ?? ''}`),
        // what the answers lead to once people agree (service/accounts/people.py ACTIONS), in the series' own terms
        ...(got.result?.id && seriesId ? [nextWord(seriesId, got.result.id)] : []),
        ...(ag && !ag.settled && !(got.points ?? 0)
          ? [`${ag.answers ?? 1} of ${ag.needed ?? 3} people answered · +${ag.points_when_agreed ?? 10} points if yours matches theirs`]
          : []),
      ];
      points = typeof got.points === 'number' ? got.points : null;
      pending = !!ag && !ag.settled && !points;
    }
    // what was said, in the words of the answers
    const said: [string, string][] =
      answers && steps
        ? answers.map((x, k) => {
            const st = stepAt(steps, answers.slice(0, k));
            return [st?.question ?? '', st?.answers.find((q) => q.id === x)?.label ?? x];
          })
        : [[body?.question ?? T.ask, body?.answers?.find((q) => q.id === result)?.label ?? T.answers.find(([q]) => q === result)?.[1] ?? result]];
    const next = await takeTask(task.kind as Takeable);
    setBusy(false);
    setLine('');
    const to = next && next !== 'failed' && next.id !== id ? taskParams(next) : null;
    setDone({
      said,
      got: gotWords,
      points,
      pending,
      next: to,
      none: next === 'failed' ? 'Tasks did not answer' : 'No more tasks of this kind',
    });
  }
  const n = a.marksHere.length;
  const T = TASKS[task.kind];
  const answers: [string, string][] = body?.answers?.length ? body.answers.map((x) => [x.id, x.label]) : T.answers;
  const answer = (x: SeriesStep['answers'][number]) => {
    if (x.next) setPath([...path, x.id]);
    else void close(x.result ?? 'cant_tell', [...path, x.id]);
  };
  const pictures = body?.pictures ?? [];
  taskTrack.pts = pictures.filter((q): q is { i: number; mjd: number; ra: number; dec: number } => q.mjd != null && q.ra != null && q.dec != null);
  taskTrack.missing = missing;
  view.panel = (
    <div class={`taskpanel k-${task.kind}${a.embedded ? ' lead' : ''}`} data-testid="task-panel">
      {si.popup}
      <div class="tp-head">
        <b>{a.embedded ? 'Lead' : 'Task'}</b>
        <span>{a.embedded ? (a.leadText ?? 'first, then the quarter') : T.title}</span>
        {focusKind(task.kind) && (
          <label class="tp-others" title="Show the other rings and names of the field as usual (in a task only the object in question is bright)">
            <input type="checkbox" checked={a.others} onChange={(e) => a.setOthers((e.target as HTMLInputElement).checked)} data-testid="task-others" />
            <span>Other objects</span>
          </label>
        )}
      </div>
      {done && (
        <div class="tp-done" data-testid="task-done">
          <div class="tp-ask">Recorded</div>
          <ol class="tp-said">
            {done.said.map(([q, x], k) => (
              <li key={k}>
                <span title={q}>{q}</span>
                <b>{x}</b>
              </li>
            ))}
          </ol>
          {done.got.map((g) => (
            <div key={g} class="tp-got">
              {g}
            </div>
          ))}
          <div class="mono-s tp-points">
            {done.pending ? 'points when people agree' : done.points == null ? 'not counted' : done.points > 0 ? `+${done.points} points` : '0 points'}{' '}
            ·{' '}
            <a class="cb-link" href="#/me">
              Cabinet
            </a>
          </div>
          {a.embedded ? (
            <div class="mono-s">then the quarter below</div>
          ) : done.next ? (
            <button class="btn sm primary tp-next" onClick={() => a.open(done.next!)} data-testid="task-next">
              Next: {T.title.toLowerCase()}
            </button>
          ) : (
            <>
              <div class="mono-s">{done.none}</div>
              {/* nothing of this kind is left: a square always is */}
              {task.kind !== 'cell' && <GiveSquareButton />}
            </>
          )}
          {/* the way to report what the machine missed stays after the answers too */}
          <button class="mini tp-missed" style={a.embedded ? { display: 'none' } : undefined} onClick={a.markMissed} title="Put a mark on something that moves or looks odd and has no ring · mark it, say what it is, the checks run" data-testid="task-missed">
            Mark something the machine missed
          </button>
        </div>
      )}
      {!done && (
        <div class="tp-ask" data-testid="task-ask">
          {step ? step.question : (body?.question ?? T.ask)}
        </div>
      )}
      {!done && step?.id === 'steps' && seriesId === 'track' && (
        <small class="tp-hint" data-testid="task-hint">
          The dot is faint: “No” is a fine answer
        </small>
      )}
      {!done && task.kind === 'cell' && (
        // what the rings of the picture are, at a glance: known (a catalogue has it), no catalogue match, a mark of mine
        <div class="tp-key mono-s" data-testid="task-key">
          <span title="A catalogue (Gaia, SIMBAD, JPL) has an object here">
            <i class="k-known" /> known
          </span>
          <span title="A source in this picture that no catalogue has at this place">
            <i class="k-lone" /> no catalogue match
          </span>
          <span title="Your mark: Mark something the machine missed, then click the picture">
            <i class="k-mark" /> your mark
          </span>
        </div>
      )}
      {!done && a.view?.(step ? step.show : null, body)}
      {!done && step?.tap && (
        <div class="tp-pics" data-testid="task-pictures">
          {pictures.map((q) => (
            <button
              key={q.i}
              class={`mini tp-pic${missing.includes(q.i) ? ' on' : ''}`}
              aria-pressed={missing.includes(q.i)}
              title={q.mjd != null ? `Missing in visit ${a.visitOf?.(q.mjd) ?? q.i + 1} · ${new Date((q.mjd - 40587) * 86400000).toISOString().slice(0, 16).replace('T', ' ')} UTC` : 'Missing in this picture'}
              onClick={() => {
                if (q.mjd != null) a.showPicture(q.mjd);
                setMissing((m) => (m.includes(q.i) ? m.filter((x) => x !== q.i) : [...m, q.i]));
              }}
            >
              {(q.mjd != null ? a.visitOf?.(q.mjd) : null) ?? q.i + 1}
            </button>
          ))}
          {!pictures.length && <span class="tp-none">The pictures of this track are not given</span>}
        </div>
      )}
      {!done && step?.tap && missing.length === 0 && pictures.length > 0 && (
        // why "Done" is off, in sight on every screen (it was only a tooltip, which a phone never shows: test of 20 Nov, S4)
        <div class="tp-need mono-s" data-testid="task-need">
          Tap at least one picture to finish
        </div>
      )}
      {!done && task.kind === 'cell' && a.square && !a.square.canCount && a.square.why !== 'Sign in to count your look' && (
        <i class="lookbar" aria-hidden="true" title="Your look: a minute with every pass shown">
          <b style={{ width: `${Math.round((a.square.frac ?? 0) * 100)}%` }} />
        </i>
      )}
      <div class="tp-line" data-testid="task-line" style={done ? { display: 'none' } : undefined}>
        {step && (
          <button
            class="tp-back"
            style={{ visibility: path.length > 0 ? 'visible' : 'hidden' }}
            disabled={busy}
            onClick={() => setPath(path.slice(0, -1))}
            data-testid="task-back"
            title="Back to the previous question"
          >
            ← Back
          </button>
        )}
        {line ||
          (task.kind === 'cell'
            ? [`${n} mark${n === 1 ? '' : 's'}`, a.square?.why ?? ''].filter(Boolean).join(' · ')
            : step && steps
              ? [
                  `Question ${path.length + 1} · up to ${steps.length}`,
                  // short enough for the one line of the panel (it never wraps, so nothing below it jumps)
                  a.ready === false && step.show !== 'sums'
                    ? step.show === 'blink' || step.show === 'pictures'
                      ? 'loading the visits…'
                      : 'loading the pictures…'
                    : step.tap
                      ? `${missing.length} missing`
                      : '',
                ]
                  .filter(Boolean)
                  .join(' · ')
              : tapping
                ? `${taps.length} tapped`
                : '')}
        {/* a guest learns before answering that an answer needs a name (the answer clicked is still given after the sign-in) */}
        {!line && task.kind !== 'cell' && step && !si.signedIn && (
          <button class="lnk tp-guest" onClick={() => si.ask('answer', '')} data-testid="task-guest">
            sign in to answer
          </button>
        )}
      </div>
      {done ? null : step ? (
        <div
          class="tp-tree"
          data-testid="task-tree"
          // as tall as the longest step of this series (its answers and their example pictures), so no step jumps and none leaves a gap
          // the card hugs its content: a step with fewer answers is shorter (the tab's list under it moves, the picture never does)
        >
          {step.answers.map((x, k) => (
            <button
              key={x.id}
              class={`btn sm tp-ans${k === 0 ? ' primary' : ''}${x.id === 'not_sure' ? ' quiet' : ''}`}
              // "missing in some" needs at least one picture tapped (the service refuses an empty list)
              disabled={busy || (a.ready === false && step.show !== 'sums') || (!!step.tap && x.id === 'done' && missing.length === 0)}
              title={step.tap && x.id === 'done' && missing.length === 0 ? 'Tap at least one picture where it is missing' : undefined}
              onClick={() => answer(x)}
              data-testid={`task-answer-${x.id}`}
            >
              <span>{x.label}</span>
              {(EXAMPLES[`${seriesId}.${step.id}.${x.id}`] ?? []).map((name) => (
                <img
                  key={name}
                  class="tp-ex"
                  src={`/examples/${name}.webp`}
                  alt=""
                  title={EXAMPLE_WORD[name] ?? name}
                  onError={(e) => ((e.target as HTMLImageElement).style.display = 'none')}
                />
              ))}
            </button>
          ))}
          <button class="mini tp-missed" style={a.embedded ? { display: 'none' } : undefined} onClick={a.markMissed} title="Put a mark on something that moves or looks odd and has no ring · mark it, say what it is, the checks run" data-testid="task-missed">
            Mark something the machine missed
          </button>
        </div>
      ) : (
        <div class={`tp-btns n${answers.length}`}>
          {answers.map(([result, text], k) => (
            <button
              key={result}
              class={`btn sm${(task.kind === 'cell' ? result === 'marks' : k === 0) ? ' primary' : ''}${result === 'cant_tell' ? ' quiet' : ''}`}
              disabled={
                busy ||
                (task.kind === 'cell' && result === 'marks' && n === 0) ||
                // signed in: the look must be long enough to count; a guest may go on (nothing is counted)
                (task.kind === 'cell' && !!a.square && !a.square.canCount && a.square.why !== 'Sign in to count your look') ||
                // a tap task's "Done" needs a tap
                (tapping && (result === 'seen' || result === 'done') && !taps.length)
              }
              title={
                task.kind === 'cell' && result === 'marks' && n === 0
                  ? 'No marks yet: Mark something the machine missed, then click the picture'
                  : task.kind === 'cell' && a.square && !a.square.canCount
                    ? a.square.why
                    : undefined
              }
              onClick={() => void close(result)}
              data-testid={`task-${result}`}
            >
              {text}
            </button>
          ))}
        </div>
      )}
      {!step && !done && (
        <button
          class="mini tp-missed"
          onClick={a.markMissed}
          title="Put a mark on something that moves or looks odd and has no ring · mark it, say what it is, the checks run"
          data-testid="task-missed"
          style={a.embedded ? { display: 'none' } : undefined}
        >
          Mark something the machine missed
        </button>
      )}
    </div>
  );
  return view;
}

/** What an example picture shows (its tooltip): each is a fixed picture made once from a real object of our own data. */
export const EXAMPLE_WORD: Record<string, string> = {
  star_point: 'A star: a sharp point',
  known_galaxy: 'A known galaxy: fuzzy, stretched',
  cosmic_ray: 'A cosmic ray: in one exposure only',
  satellite_streak: 'A satellite streak',
  picture_edge: 'The edge of the picture',
  bright_star_ghost: 'A ring or spike of a bright star',
  eps_indi_b: 'ε Indi B: deep dips',
  wise_0855: 'WISE 0855−0714: deep dips',
  dusty_star: 'A dusty star: a smooth rise to the red',
};

/** The button alone: the next quarter of a square to look through, taken when clicked. */
export function GiveSquareButton() {
  const [line, setLine] = useState('');
  const go = async () => {
    setLine('asking…');
    const t = await takeTask('cell');
    const to = t && t !== 'failed' ? taskParams(t) : null;
    if (to) location.hash = `#/lab?${new URLSearchParams(to).toString()}`;
    else setLine(t === 'failed' ? 'Tasks did not answer' : 'No square is waiting for a look');
  };
  return (
    <>
      <button class="btn primary" onClick={() => void go()} data-testid="give-square-button">
        Give me a square
      </button>
      {line && <span class="cb-status">{line}</span>}
    </>
  );
}

/** No task open: the one way to start one from the lab ("Give me a square": the next quarter of a square to look through). */
export function GiveSquare() {
  return (
    <section class="psec give-square" data-testid="give-square">
      <GiveSquareButton />
    </section>
  );
}
