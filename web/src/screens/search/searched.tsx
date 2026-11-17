import { visibleInterval } from '../../lib/idle';
import { useEffect, useState } from 'preact/hooks';
import { useProduct } from './jobs';
import './search.css';

/** One searched field, as GET /api/v1/searched lists it. */
export interface SearchedField {
  key: string;
  ra: number;
  dec: number;
  /** arcminutes */
  fov: number;
  state: 'done' | 'running';
  kinds: string[];
  /** leads: tracks, pairs and peaks above the bar */
  found: number;
  /** searched and nothing above the bar */
  nothing?: boolean;
  best?: { kind: string; label: string; sn?: number } | null;
  searched_at?: string;
  /** exposures the searches of this field read */
  exposures?: number;
  /** what the lab address needs to show the result */
  open: { ra: number; dec: number; fov: number; pass?: number | null; film?: number | null };
}

/** One colour per result kind: something found (amber), nothing found (teal), Find Planet X depth reached (violet), still running (grey). */
export const SEARCHED_COLOR = { found: '#ffb454', nothing: '#4fe3d6', depth: '#c58bff', running: '#9fb4bd' } as const;
export type SearchedKind = keyof typeof SEARCHED_COLOR;
export const kindOf = (f: SearchedField): SearchedKind =>
  f.state === 'running' ? 'running' : f.kinds.includes('distant') || f.kinds.includes('deep') ? 'depth' : f.found > 0 ? 'found' : 'nothing';
const colorOf = (f: SearchedField) => SEARCHED_COLOR[kindOf(f)];

/** The searched fields. `null` while unknown or when the service does not make the list (then nothing is drawn). Refreshed once a minute while `live`. */
export function useSearched(live = true): SearchedField[] | null {
  const has = useProduct('searched');
  const [list, setList] = useState<SearchedField[] | null>(null);
  useEffect(() => {
    if (has !== true || !live) return;
    let stop = false;
    const load = () =>
      fetch('/api/v1/searched')
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { fields?: SearchedField[] } | null) => {
          if (!stop && d?.fields) setList(d.fields);
        })
        .catch(() => {});
    void load();
    const off = visibleInterval(load, 60000); // a hidden tab asks nothing; seen again, it asks at once
    return () => {
      stop = true;
      off();
    };
  }, [has, live]);
  return list;
}

type Project = (ra: number, dec: number) => [number, number, boolean];

/** The footprint of a field in canvas pixels (a square on the sky), or a dot of at least `minR` pixels when it is smaller than that. */
function shape(
  f: SearchedField,
  project: Project,
  minR: number,
): { x: number; y: number; r: number; poly: [number, number][] | null } | null {
  const c = project(f.ra, f.dec);
  if (!c[2]) return null;
  const h = f.fov / 120;
  const cd = Math.max(0.05, Math.cos((f.dec * Math.PI) / 180));
  const corners = [
    [f.ra - h / cd, f.dec - h],
    [f.ra + h / cd, f.dec - h],
    [f.ra + h / cd, f.dec + h],
    [f.ra - h / cd, f.dec + h],
  ].map(([a, d]) => project(a!, d!));
  const poly = corners.every((p) => p[2]) ? corners.map((p) => [p[0], p[1]] as [number, number]) : null;
  const size = poly ? Math.max(...poly.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1]))) : 0;
  return { x: c[0], y: c[1], r: Math.max(minR, size), poly: size >= minR ? poly : null };
}

/** The smallest a zone is drawn: a field of 5′ is a few pixels on a whole-sky view, so it is a ring of at least this radius. */
const MIN_R = 7;

/**
 * «Where we've searched» on the globe or map: every searched field as a filled translucent footprint with a bright outline (a ring of at least MIN_R pixels when the
 * footprint is smaller), one colour per result kind (see SEARCHED_COLOR). The selected field is ringed in white. Small zones are drawn last so that they stay on top.
 */
export function drawSearched(c: CanvasRenderingContext2D, project: Project, fields: SearchedField[], selected?: string | null): void {
  c.save();
  const shapes = fields
    .map((f) => ({ f, s: shape(f, project, MIN_R) }))
    .filter((x): x is { f: SearchedField; s: NonNullable<ReturnType<typeof shape>> } => !!x.s);
  shapes.sort((p, q) => q.s.r - p.s.r);
  for (const { f, s } of shapes) {
    const col = colorOf(f);
    const sel = f.key === selected;
    c.beginPath();
    if (s.poly) {
      s.poly.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
      c.closePath();
    } else c.arc(s.x, s.y, MIN_R, 0, 7);
    c.fillStyle = `${col}${s.poly ? '40' : '66'}`;
    c.fill();
    // a dark under-stroke, then the bright outline with a soft glow
    c.lineJoin = 'round';
    c.strokeStyle = 'rgba(3,8,15,0.6)';
    c.lineWidth = sel ? 5 : 4;
    c.stroke();
    c.shadowColor = col;
    c.shadowBlur = 8;
    c.strokeStyle = col;
    c.lineWidth = sel ? 3 : 2;
    c.stroke();
    c.shadowBlur = 0;
    if (sel) {
      c.strokeStyle = '#fff';
      c.lineWidth = 1.5;
      c.beginPath();
      c.arc(s.x, s.y, Math.max(MIN_R + 4, s.r + 5), 0, 7);
      c.stroke();
    }
  }
  c.restore();
}

/** The searched field under a click (px, py in canvas pixels), the nearest if several; null if none within its footprint or 8 px. */
export function hitSearched(project: Project, fields: SearchedField[], px: number, py: number): SearchedField | null {
  let best: SearchedField | null = null;
  let bd = Infinity;
  for (const f of fields) {
    const s = shape(f, project, MIN_R);
    if (!s) continue;
    const d = Math.hypot(px - s.x, py - s.y);
    if (d <= Math.max(8, s.r) && d < bd) {
      best = f;
      bd = d;
    }
  }
  return best;
}

const KIND_TEXT: Record<SearchedKind, string> = {
  found: 'something found',
  nothing: 'nothing found',
  depth: 'Find Planet X depth reached',
  running: 'running',
};

/** The key and the counter of the zones, small, floating over the globe while the zones are shown: a colour per result kind with its count, and the fields and sky searched. */
export function SearchedLegend({ fields }: { fields: SearchedField[] }) {
  const n: Record<SearchedKind, number> = { found: 0, nothing: 0, depth: 0, running: 0 };
  for (const f of fields) n[kindOf(f)]++;
  const deg2 = fields.reduce((a, f) => a + (f.fov / 60) ** 2, 0);
  return (
    <div class="searched-key" data-testid="searched-key">
      {(Object.keys(KIND_TEXT) as SearchedKind[])
        .filter((k) => k !== 'running' || n.running > 0)
        .map((k) => (
          <span class="mono-s" key={k}>
            <span class="sw" style={{ borderColor: SEARCHED_COLOR[k], background: `${SEARCHED_COLOR[k]}55` }} />
            {KIND_TEXT[k]} · {n[k]}
          </span>
        ))}
      <span class="mono-s faint">
        {fields.length.toLocaleString('en-US')} fields · {deg2 < 10 ? deg2.toFixed(2) : deg2.toFixed(0)} deg² searched
      </span>
    </div>
  );
}

/** The card of a clicked field: what was searched, what was found, and the button that opens the result in the lab. */
export function SearchedCard({
  f,
  onOpen,
  onClose,
}: {
  f: SearchedField;
  onOpen: (o: SearchedField['open']) => void;
  onClose?: () => void;
}) {
  const kinds: Record<string, string> = {
    passes: 'between passes',
    film: 'inside a pass',
    distant: 'Find Planet X',
    deep: 'Find Planet X · deep sum',
  };
  return (
    <div class="searched-card">
      <div class="nm">
        <b>{f.fov}′ field</b>{' '}
        <span class="badge orange">
          {f.state === 'running' ? 'running' : f.found > 0 ? `${f.found} lead${f.found === 1 ? '' : 's'}` : 'nothing above the bar'}
        </span>
      </div>
      <div class="mono-s">{f.kinds.map((k) => kinds[k] ?? k).join(' · ')}</div>
      {f.best && (
        <div class="mono-s">
          best: {f.best.label}
          {f.best.sn != null ? ` · S/N ${f.best.sn}` : ''} · a lead, not a discovery
        </div>
      )}
      {f.searched_at && <div class="mono-s">searched {f.searched_at.slice(0, 10)}</div>}
      <div class="chips">
        <button class="btn sm primary" onClick={() => onOpen(f.open)}>
          Open the result
        </button>
        {onClose && (
          <button class="btn sm" onClick={onClose}>
            Close
          </button>
        )}
      </div>
    </div>
  );
}

/** The totals of the searched fields: how many, how much sky (the fields' areas added: overlaps count twice), how many exposures and how many leads. */
export function searchedTotals(fields: SearchedField[]): { fields: number; deg2: number; exposures: number; leads: number } {
  return {
    fields: fields.length,
    deg2: fields.reduce((a, f) => a + (f.fov / 60) ** 2, 0),
    exposures: fields.reduce((a, f) => a + (f.exposures ?? 0), 0),
    leads: fields.reduce((a, f) => a + f.found, 0),
  };
}

/** «Searched so far»: fields, square degrees, exposures and leads, from /api/v1/searched. Renders nothing while the service has no such list. */
export function SearchedCounter({ fields, leads = true }: { fields: SearchedField[] | null; leads?: boolean }) {
  if (!fields) return null;
  const t = searchedTotals(fields);
  const tile = (b: string, l: string, tip?: string) => (
    <div title={tip}>
      <b>{b}</b>
      <span>{l}</span>
    </div>
  );
  return (
    <div class="fsum searched-counter" aria-label="Searched so far">
      {tile(t.fields.toLocaleString('en-US'), 'fields searched so far')}
      {tile(
        t.deg2 < 10 ? t.deg2.toFixed(2) : t.deg2.toFixed(0),
        'square degrees',
        'the areas of the fields added; fields that overlap count twice',
      )}
      {t.exposures > 0 && tile(t.exposures.toLocaleString('en-US'), 'exposures read')}
      {leads && tile(t.leads.toLocaleString('en-US'), 'leads (not discoveries)')}
    </div>
  );
}
