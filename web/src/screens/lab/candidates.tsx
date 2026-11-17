// The Candidates tab of the lab (product spec "5-lab"): the finds of the field as one list of rows, unexplained first, each with a
// small still, its name or place, one line of what it is and one status badge. Known ones follow, dimmer, and never count.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';

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
  /** selects it on the picture and opens its card */
  pick: () => void;
}

/** What the tab's counter says: the unexplained finds of the field; known ones, and ones still being checked, never count. */
export function unexplainedCount(rows: FindRow[]): number {
  return rows.filter((r) => r.status === 'unexplained').length;
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

/** A small still of a find: the pixels of the lab's picture around its place, taken when the row appears and every few seconds. */
function Still({ uv, picture }: { uv: [number, number] | null; picture: () => HTMLCanvasElement | null }) {
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
      } catch {
        /* a picture not yet drawn: the still stays empty */
      }
    };
    draw();
    const t = setInterval(draw, 4000);
    return () => clearInterval(t);
  }, [key]);
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

export function CandList({
  rows,
  also,
  selected,
  picture,
  details,
}: {
  rows: FindRow[];
  also: string;
  /** the key of the row whose find is selected on the picture */
  selected: string | null;
  picture: () => HTMLCanvasElement | null;
  /** per-search statistics, exports and switches: folded away under "Details" */
  details: ComponentChildren;
}) {
  const list = orderRows(rows);
  return (
    <div class="candlist" data-testid="cand-list">
      {list.map((r) => (
        <button key={r.key} class={`crow s-${r.status}${selected === r.key ? ' on' : ''}`} onClick={r.pick} data-testid="cand-row">
          <Still uv={r.uv} picture={picture} />
          <span class="cr-text">
            <b title={r.name}>{r.name}</b>
            <span title={r.line}>{r.line}</span>
          </span>
          <em class={`cr-badge s-${r.status}`}>{STATUS_WORD[r.status]}</em>
        </button>
      ))}
      {!list.length && <div class="cr-none">Nothing found in this field yet</div>}
      <div class="cr-also" data-testid="cand-also">
        {also}
      </div>
      <details class="cr-details">
        <summary>Details</summary>
        {details}
      </details>
    </div>
  );
}
