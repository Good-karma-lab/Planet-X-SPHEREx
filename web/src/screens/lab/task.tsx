// The Task panel of the lab (product spec §5, §5b). The panel is shown only when the lab was opened with a task:
// `task=<task id>&kind=cell|finding|moves|red|anomaly` (+ `cell=` / `finding=`), or `task=<kind>` with the same ids, in which case
// the lab takes the task itself. Without a task the lab is as it always was. A task closes with its answer only: notes, the checks
// and the submit are in the Investigation page of the Cabinet, never in the lab.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { Mark } from '../../lib/marks';

export type TaskKind = 'cell' | 'finding' | 'moves' | 'red' | 'anomaly';
const KINDS: TaskKind[] = ['cell', 'finding', 'moves', 'red', 'anomaly'];

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
};

/** One step of a question tree (plain questions, not classes: spec §5b): what the lab shows with it, and the answers; an answer
 *  leads to the next step or ends the path with a class. */
export interface TreeStep {
  id: string;
  question: string;
  /** what the lab shows with the question: picture | passes | defects | spectrum | exposures | detector */
  show: string;
  answers: { id: string; label: string; next?: string; result?: string; examples?: string[] }[];
}

const NOT_SURE = { id: 'not_sure', label: 'Not sure', result: 'cant_tell' };
/** The trees as the service sends them in the task (service/accounts/agreement.py TREES): kept here too for a task opened by its
 *  id alone, whose body the lab does not have. The service decides the class from the path; the lab only walks the steps. */
export const TREES: Partial<Record<TaskKind, TreeStep[]>> = {
  red: [
    {
      id: 'shape',
      question: 'Is it a sharp point like the stars around it?',
      show: 'picture',
      answers: [
        { id: 'sharp', label: 'Sharp point', next: 'every_pass', examples: ['star_point'] },
        { id: 'fuzzy', label: 'Fuzzy or stretched', result: 'galaxy', examples: ['known_galaxy'] },
        NOT_SURE,
      ],
    },
    {
      id: 'every_pass',
      question: 'Is it there in every pass?',
      show: 'passes',
      answers: [{ id: 'yes', label: 'Yes', next: 'defect' }, { id: 'some', label: 'Only in some', result: 'artefact', examples: ['cosmic_ray'] }, NOT_SURE],
    },
    {
      id: 'defect',
      question: 'Does it look like one of these defects?',
      show: 'defects',
      answers: [
        { id: 'yes', label: 'Yes', result: 'artefact', examples: ['satellite_streak', 'picture_edge', 'bright_star_ghost'] },
        { id: 'no', label: 'No', next: 'spectrum' },
        NOT_SURE,
      ],
    },
    {
      id: 'spectrum',
      question: 'Its spectrum next to two examples: which shape is closer?',
      show: 'spectrum',
      answers: [
        { id: 'dips', label: 'A: deep dips, like ε Indi B', result: 'brown_dwarf', examples: ['eps_indi_b', 'wise_0855'] },
        { id: 'smooth', label: 'B: a smooth rise to the red', result: 'dusty_star', examples: ['dusty_star'] },
        NOT_SURE,
      ],
    },
  ],
  anomaly: [
    {
      id: 'exposures',
      question: 'The odd point of the spectrum: is it in more than one exposure?',
      show: 'exposures',
      answers: [{ id: 'yes', label: 'Yes', next: 'detector' }, { id: 'one', label: 'Only one', result: 'cosmic_ray', examples: ['cosmic_ray'] }, NOT_SURE],
    },
    {
      id: 'detector',
      question: 'Where did those exposures fall on the detector?',
      show: 'detector',
      answers: [
        { id: 'edge', label: 'Near an edge or a bright star', result: 'detector', examples: ['picture_edge', 'bright_star_ghost'] },
        { id: 'open', label: 'In the open', result: 'real' },
        NOT_SURE,
      ],
    },
  ],
};

/** The step a path of answers has reached: the first step for an empty path; null when the path has ended or is not of the tree. */
export function stepAt(tree: TreeStep[], path: string[]): TreeStep | null {
  let step: TreeStep | null = tree[0] ?? null;
  for (const answer of path) {
    const a = step?.answers.find((x) => x.id === answer);
    if (!a || !a.next) return null;
    step = tree.find((x) => x.id === a.next) ?? null;
  }
  return step;
}

/** A task about one object: that object alone gets the bright ring and its label, the rest is dimmed (spec §5b). */
export const focusKind = (k: TaskKind): boolean => k !== 'cell';

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
interface NextTask {
  id: string;
  kind: Takeable;
  cell_id: string | null;
  finding_id: string | null;
  ra: number | null;
  dec: number | null;
  /** the field size the find was made in, arcminutes (when the service says it) */
  fov?: number | null;
  /** red sources and anomalies: the tree of plain questions the lab asks */
  tree?: TreeStep[];
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
  /** what the lab shows for the task's question (the mover's blink and ruler), or for one step of a question tree (`show` of the step) */
  view?: (show: string | null) => ComponentChildren;
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
  // a tree of plain questions (red source, anomaly): the service's own when the task brought it, and the answers given so far
  const [tree, setTree] = useState<TreeStep[] | null>(task ? (TREES[task.kind] ?? null) : null);
  const [path, setPath] = useState<string[]>([]);
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
  // a lab opened with the kind only (from Findings, from the globe): the lab takes the task itself
  useEffect(() => {
    setId(task?.id ?? null);
    setRefs({ cell: task?.cell_id ?? null, finding: task?.finding_id ?? null });
    setLine('');
    setTree(task ? (TREES[task.kind] ?? null) : null);
    setPath([]);
    if (!task) return;
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
        if (Array.isArray(t.tree) && t.tree.length) setTree(t.tree);
      }
    });
    return () => {
      stop = true;
    };
  }, [key]);
  if (!task) return null;

  const close = async (result: string, answers?: string[]) => {
    if (busy) return;
    setBusy(true);
    setLine('saving…');
    const markIds = a.marksHere.map((m) => m.id);
    if (task.kind === 'cell' && refs.cell) await post(`/api/grid/cell/${encodeURIComponent(refs.cell)}/viewed`, { result });
    // closing needs an account; a guest's answer still counts the square as viewed, and the next task opens
    if (id) {
      const r = await post(`/api/me/tasks/${encodeURIComponent(id)}/done`, {
        // a tree of questions: the path of answers goes, and the service says which class it leads to
        ...(answers ? { path: answers } : { result }),
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
        class?: { label?: string };
      };
      const items = [
        ...(got.class?.label ? [`Your answers say: ${got.class.label}`] : []),
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
  const n = a.marksHere.length;
  const T = TASKS[task.kind];
  // a tree of plain questions: one step at a time; an answer goes on to the next step or ends the task with the path
  const step = tree ? stepAt(tree, path) : null;
  const answer = (x: TreeStep['answers'][number]) => {
    if (x.next) setPath([...path, x.id]);
    else void close(x.result ?? 'cant_tell', [...path, x.id]);
  };
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
      <div class="tp-ask" data-testid="task-ask">
        {step ? step.question : T.ask}
      </div>
      {a.view?.(step ? step.show : null)}
      <div class="tp-line" data-testid="task-line">
        {line ||
          (task.kind === 'cell'
            ? `${n} mark${n === 1 ? '' : 's'} in this square`
            : step && tree
                ? `Question ${path.length + 1} of at most ${tree.length}`
                : '')}
      </div>
      {step ? (
        <div class="tp-tree" data-testid="task-tree">
          {step.answers.map((x, k) => (
            <button
              key={x.id}
              class={`btn sm tp-ans${k === 0 ? ' primary' : ''}${x.id === 'not_sure' ? ' quiet' : ''}`}
              disabled={busy}
              onClick={() => answer(x)}
              data-testid={`task-answer-${x.id}`}
            >
              <span>{x.label}</span>
              {(x.examples ?? []).map((name) => (
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
          {/* always there, so the panel keeps its height from the first question on */}
          <button
            class="mini tp-back"
            style={{ visibility: path.length > 0 ? 'visible' : 'hidden' }}
            disabled={busy}
            onClick={() => setPath(path.slice(0, -1))}
            data-testid="task-back"
          >
            Back
          </button>
        </div>
      ) : (
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
        </div>
      )}
    </div>
  );
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
