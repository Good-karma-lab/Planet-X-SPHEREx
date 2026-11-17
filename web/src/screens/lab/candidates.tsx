// The Candidates tab of the lab (product spec "5-lab"): the finds of the field as one list of rows, unexplained first, each with a
// small still, its name or place, one line of what it is and one status badge. Known ones follow, dimmer, and never count.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { visibleInterval } from '../../lib/idle';

export type FindStatus = 'unexplained' | 'checking' | 'jpl' | 'known';
export const STATUS_WORD: Record<FindStatus, string> = {
  unexplained: 'unexplained',
  checking: 'checking',
  jpl: 'JPL did not answer',
  known: 'known',
};

export interface FindRow {
  key: string;
  /** its full name, or its place when it has none */
  name: string;
  /** one line of what it is */
  line: string;
  status: FindStatus;
  /** where it is on the picture now, as fractions of it (u right, v down); null when it is not on show */
  uv: [number, number] | null;
  /** a track: its visits' places on the picture (fractions), drawn over the still so a fast mover's row shows its path */
  path?: [number, number][];
  /** selects it on the picture and opens its card */
  pick: () => void;
  /** a red source or a spectral anomaly: as in Findings these are kept apart, in one folded group, and never counted */
  group?: 'red' | 'anomaly';
  /** its place (degrees), for finding the service's own find of it */
  ra?: number;
  dec?: number;
}

/** What the tab's counter says: the unexplained movers, tracks, brown dwarfs, pairs and Planet X peaks of the field. Known ones,
 *  ones still being checked, and every red source and spectral anomaly (the folded group) never count. */
export function unexplainedCount(rows: FindRow[]): number {
  return rows.filter((r) => r.status === 'unexplained' && !r.group).length;
}

/** The order of the list: unexplained first, then those being checked or without JPL's answer, the known ones last. */
export function orderRows(rows: FindRow[]): FindRow[] {
  const rank: Record<FindStatus, number> = { unexplained: 0, checking: 1, jpl: 1, known: 2 };
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => rank[a.r.status] - rank[b.r.status] || a.i - b.i)
    .map((x) => x.r);
}

/** The quiet line at the bottom: the searches that found nothing, and those not finished. */
export function alsoLine(searches: { name: string; state: 'nothing' | 'running' | 'found' | 'failed' }[]): string {
  const of = (st: string) => searches.filter((x) => x.state === st).map((x) => x.name);
  const parts: string[] = [];
  if (of('nothing').length) parts.push(`Also searched: ${of('nothing').join(', ')}: nothing`);
  if (of('running').length) parts.push(`Still searching: ${of('running').join(', ')}`);
  if (of('failed').length) parts.push(`Did not finish: ${of('failed').join(', ')}`);
  return parts.join(' · ');
}

const STILL = 44;
/** rows of the list on show before "All N" */
export const TOP_ROWS = 4;

/** A small still of a find: the pixels of the lab's picture around its place, taken when the row appears and every few seconds. */
function Still({ uv, picture, path }: { uv: [number, number] | null; picture: () => HTMLCanvasElement | null; path?: [number, number][] }) {
  const cv = useRef<HTMLCanvasElement>(null);
  const key = uv ? `${uv[0].toFixed(4)},${uv[1].toFixed(4)}` : '';
  useEffect(() => {
    const draw = () => {
      const c = cv.current;
      const src = picture();
      if (!c) return;
      const g = c.getContext('2d')!;
      g.clearRect(0, 0, c.width, c.height);
      if (!src || !src.width || !uv) return;
      // 1/9 of the picture's side around the place, never beyond its edge
      const s = src.width / 9;
      const x = Math.max(0, Math.min(src.width - s, uv[0] * src.width - s / 2));
      const y = Math.max(0, Math.min(src.height - s, uv[1] * src.height - s / 2));
      try {
        g.imageSmoothingEnabled = true;
        g.drawImage(src, x, y, s, s, 0, 0, c.width, c.height);
        // a track: its visits as dots on a thin line, in the still's own scale (it may run out of the still: then it is clipped)
        if (path && path.length > 1) {
          const k = c.width / s;
          const pts = path.map(([u, v]) => [(u * src.width - x) * k, (v * src.height - y) * k] as const);
          g.beginPath();
          pts.forEach(([px, py], i) => (i ? g.lineTo(px, py) : g.moveTo(px, py)));
          g.strokeStyle = 'rgba(255,200,110,0.85)';
          g.lineWidth = 1.5;
          g.stroke();
          for (const [px, py] of pts) {
            g.beginPath();
            g.arc(px, py, 2.6, 0, 7);
            g.fillStyle = '#ffd08a';
            g.fill();
          }
        }
      } catch {
        /* a picture not yet drawn: the still stays empty */
      }
    };
    draw();
    // the picture changes with the pass: the still follows it every 4 s, never in a hidden tab
    return visibleInterval(draw, 4000);
  }, [key, path?.length]);
  // a find that is not on the picture on show (a track inside one pass) keeps the place of its still, empty
  return <canvas ref={cv} class="cr-still" style={{ visibility: uv ? 'visible' : 'hidden' }} width={STILL * 2} height={STILL * 2} aria-hidden="true" />;
}

/** Whether JPL answered for the asteroid tracks of a pass: 'done', 'unavailable' (it did not), or 'asking'. */
export function useJplState(filmKey: string | undefined): 'done' | 'unavailable' | 'asking' {
  const [st, setSt] = useState<'done' | 'unavailable' | 'asking'>('asking');
  useEffect(() => {
    setSt('asking');
    if (!filmKey) return;
    let stop = false;
    void (async () => {
      for (let i = 0; i < 40 && !stop; i++) {
        try {
          const r = await fetch(`/api/v1/film/${filmKey}/bodies`, { cache: 'no-store' });
          if (r.ok) {
            const s = ((await r.json()) as { state?: string }).state;
            if (stop) return;
            if (s === 'done' || s === 'unavailable') return setSt(s);
          }
        } catch {
          /* asked again */
        }
        await new Promise((res) => setTimeout(res, 3000));
      }
      if (!stop) setSt('unavailable');
    })();
    return () => {
      stop = true;
    };
  }, [filmKey]);
  return st;
}

/** The service's own finds of the two folded kinds near the field (asked once, when the group is opened): a row whose place has
 *  one can start its question task. */
export function useFieldFinds(open: boolean, ra: number, dec: number, fov: number): { id: string; kind: string; ra: number; dec: number }[] {
  const [finds, setFinds] = useState<{ id: string; kind: string; ra: number; dec: number }[]>([]);
  useEffect(() => {
    setFinds([]);
    if (!open) return;
    let stop = false;
    // the two kinds, each asked for whole (the list gives at most 2000 a time)
    void Promise.all(
      (['red', 'anomaly'] as const).map((kind) =>
        fetch(`/api/findings?limit=2000&kind=${kind}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((d: { finds?: { id: string; kind: string; ra: number; dec: number }[] } | null) => d?.finds ?? [])
          .catch(() => []),
      ),
    ).then((lists) => {
      if (stop) return;
      const half = fov / 120 + 0.01;
      const cosd = Math.cos((dec * Math.PI) / 180);
      setFinds(lists.flat().filter((f) => Math.abs(f.dec - dec) < half && Math.abs(((f.ra - ra + 540) % 360) - 180) * cosd < half));
    });
    return () => {
      stop = true;
    };
  }, [open, ra, dec, fov]);
  return finds;
}

/** The find of the service at a row's place (within 6″, of the row's kind), or null. */
export function findOfRow(row: FindRow, finds: { id: string; kind: string; ra: number; dec: number }[]): string | null {
  if (!row.group || row.ra == null || row.dec == null) return null;
  const cosd = Math.cos((row.dec * Math.PI) / 180);
  let best: string | null = null;
  let bd = 6;
  for (const f of finds) {
    if (f.kind !== row.group) continue;
    const d = Math.hypot((((f.ra - row.ra + 540) % 360) - 180) * cosd, f.dec - row.dec) * 3600;
    if (d < bd) {
      bd = d;
      best = f.id;
    }
  }
  return best;
}

export function CandList({
  rows,
  busy,
  also,
  selected,
  picture,
  details,
  field,
  onTask,
}: {
  rows: FindRow[];
  also: string;
  /** the key of the row whose find is selected on the picture */
  selected: string | null;
  picture: () => HTMLCanvasElement | null;
  /** per-search statistics, exports and switches: folded away under "Details" */
  details: ComponentChildren;
  /** a search of the field is still running: an empty list says so, not that nothing is there */
  busy?: boolean;
  /** the field on show: its centre and size */
  field: { ra: number; dec: number; fov: number };
  /** starts the question task of a red source or an anomaly (the service's find id) */
  onTask: (kind: 'red' | 'anomaly', findId: string, row: FindRow) => void;
}) {
  const list = orderRows(rows.filter((r) => !r.group));
  const folded = orderRows(rows.filter((r) => r.group));
  const [openGroup, setOpenGroup] = useState(false);
  // the top rows are on show; "All N" opens the rest (the column below keeps its place)
  const [all, setAll] = useState(false);
  const shown = all ? list : list.slice(0, TOP_ROWS);
  const finds = useFieldFinds(openGroup && folded.length > 0, field.ra, field.dec, field.fov);
  const row = (r: FindRow) => {
    const fid = r.group ? findOfRow(r, finds) : null;
    return (
      <div key={r.key} class="crow-wrap">
        <button class={`crow s-${r.status}${selected === r.key ? ' on' : ''}`} onClick={r.pick} data-testid="cand-row">
          <Still uv={r.uv} picture={picture} path={r.path} />
          <span class="cr-text">
            <b title={r.name}>{r.name}</b>
            <span title={r.line}>{r.line}</span>
          </span>
          <em class={`cr-badge s-${r.status}`}>{STATUS_WORD[r.status]}</em>
        </button>
        {r.group && fid && (
          <button
            class="mini cr-ask"
            title={r.group === 'red' ? 'Start the task: why is it red?' : 'Start the task: is this feature real?'}
            onClick={() => onTask(r.group!, fid, r)}
            data-testid="cand-ask"
          >
            Answer the questions
          </button>
        )}
      </div>
    );
  };
  return (
    <div class="candlist" data-testid="cand-list">
      {shown.map(row)}
      {list.length > TOP_ROWS && (
        <button class="mini cr-all" onClick={() => setAll((v) => !v)} data-testid="cand-all">
          {all ? 'Fewer' : `All ${list.length}`}
        </button>
      )}
      {!list.length && <div class="cr-none">{busy ? 'Searching…' : 'Nothing unexplained here'}</div>}
      {folded.length > 0 && (
        <details class="cr-group" open={openGroup} onToggle={(e) => setOpenGroup((e.target as HTMLDetailsElement).open)} data-testid="cand-group">
          <summary>
            Red sources and anomalies · {folded.length}
          </summary>
          {folded.map(row)}
        </details>
      )}
      {also && (
        <div class="cr-also" data-testid="cand-also">
          {also}
        </div>
      )}
      <details class="cr-details">
        <summary>Details</summary>
        {details}
      </details>
    </div>
  );
}
