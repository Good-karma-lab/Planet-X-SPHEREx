import { useEffect, useRef, useState } from 'preact/hooks';
import { formatHash } from '../../lib/route';
import { cleanName } from './finds';

/** A find as a card needs it (`GET /api/findings?lite=1`). */
export interface CardFind {
  id: string;
  kind: string;
  place: string;
  fov: number;
  ra: number;
  dec: number;
  rate: number | null;
  snr: number | null;
  score: number | null;
  status: string;
  verdict: string;
  stage: 'new' | 'in_review' | 'submitted' | 'known' | 'artefact' | 'candidate' | 'confirmed';
  promise: number;
  viewers: number;
  credit: { found_by: string; machine: boolean; first_marked_by: string | null; checked_by: string[]; looks: Record<string, number> };
  clip: { url: string; state: 'ready' | 'waiting' | 'building' | 'none'; reason?: string } | null;
  name: string | null;
  field: { ra: number; dec: number; fov: number } | null;
  group: string | null;
  passes: number | null;
}

export const STAGE_LABEL: Record<string, string> = {
  new: 'New',
  in_review: 'In review',
  submitted: 'Submitted',
  known: 'Known',
  artefact: 'Artefact',
  candidate: 'Candidate',
  confirmed: 'Confirmed',
};
const STAGE_BADGE: Record<string, string> = { new: 'orange', in_review: 'blue', submitted: 'purple', known: 'green', artefact: '', candidate: 'yellow', confirmed: 'green' };
export const KIND_ONE: Record<string, string> = {
  mover: 'Mover',
  browndwarf: 'Brown dwarf',
  red: 'Red source',
  anomaly: 'Spectral anomaly',
  pair: 'Pair changing',
  film: 'Time-lapse track',
  peak: 'Planet X peak',
};

const clean = (n: string) => cleanName(n);

export function cardTitle(f: CardFind): string {
  const nm = f.name ? clean(f.name) : '';
  switch (f.kind) {
    case 'mover':
      return `${nm || 'Mover'} · ${(f.rate ?? 0).toFixed(2)}″/yr`;
    case 'browndwarf':
      return `${nm || (f.group === 'known' ? 'Known brown dwarf' : f.group === 'slow' ? 'Slow red mover' : 'Brown dwarf candidate')} · ${(f.rate ?? 0).toFixed(1)}″/yr`;
    case 'film':
      return `${nm || 'Moves within one pass'} · ${(f.rate ?? 0).toFixed(1)}″/h`;
    case 'peak':
      return `Planet X peak · S/N ${(f.snr ?? f.score ?? 0).toFixed(1)}`;
    case 'red':
      return `${nm || 'Red source'} · colour ${(f.score ?? 0).toFixed(1)}`;
    case 'anomaly':
      return `${nm || 'Odd spectrum'} · score ${(f.score ?? 0).toFixed(1)}`;
    default:
      return nm || (KIND_ONE[f.kind] ?? f.kind);
  }
}

/** The lab on the field the search ran on, with the task "Check a machine finding" for this find (`task=finding`, `finding=<id>`). */
export function checkHref(f: CardFind): string {
  const fp = f.field;
  const base = fp
    ? { ra: fp.ra.toFixed(5), dec: fp.dec.toFixed(5), fov: String(fp.fov), find: f.id, find_ra: f.ra.toFixed(5), find_dec: f.dec.toFixed(5) }
    : { ra: f.ra.toFixed(5), dec: f.dec.toFixed(5), fov: f.kind === 'film' ? '60' : '20', find: f.id, find_ra: f.ra.toFixed(5), find_dec: f.dec.toFixed(5) };
  return formatHash({ screen: 'lab', params: { ...base, task: 'finding', finding: f.id } });
}

// ---- clips: only what is on screen is loaded and plays; a few are asked for at a time (a clip not made yet is rendered by the request)

const MAX_ASKING = 4;
let asking = 0;
const queue: (() => void)[] = [];
function slot(): Promise<void> {
  return new Promise((res) => {
    const go = () => {
      asking++;
      res();
    };
    if (asking < MAX_ASKING) go();
    else queue.push(go);
  });
}
function release(): void {
  asking--;
  queue.shift()?.();
}
const known = new Map<string, 'ready' | 'none'>(); // clips this page has already seen ready (or impossible), by address
const reasons = new Map<string, string>();

type ClipState = 'idle' | 'asking' | 'building' | 'ready' | 'none';

/** The looping time-lapse clip of a find. Off screen it holds no picture at all, so a thousand cards cost nothing. */
export function Clip({ f }: { f: CardFind }) {
  const box = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  const url = f.clip?.url ?? null;
  const [state, setState] = useState<ClipState>(() => (url && known.get(url) === 'ready' ? 'ready' : !url || known.get(url) === 'none' ? 'none' : 'idle'));
  useEffect(() => {
    const el = box.current;
    if (!el || typeof IntersectionObserver === 'undefined') return setNear(true);
    const io = new IntersectionObserver((es) => setNear(es.some((e) => e.isIntersecting)), { rootMargin: '150px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (!near || !url || state === 'ready' || state === 'none') return;
    if (f.clip?.state === 'ready') {
      known.set(url, 'ready');
      return setState('ready');
    }
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = async () => {
      await slot();
      let code = 0;
      let reason = '';
      try {
        if (stop) return;
        setState((s) => (s === 'building' ? s : 'asking'));
        const r = await fetch(url);
        code = r.status;
        if (code === 404) {
          const b = (await r.json().catch(() => ({}))) as { state?: string; reason?: string };
          if (b.state === 'none') reason = b.reason ?? '';
          else code = 0; // not the clip's own answer (the service is restarting): asked again
        }
        else await r.arrayBuffer(); // the clip is now in the browser's cache: the picture below reads it from there
      } catch {
        code = 0;
      } finally {
        release();
      }
      if (stop) return;
      if (code === 200) {
        known.set(url, 'ready');
        setState('ready');
      } else if (code === 404) {
        known.set(url, 'none');
        reasons.set(url, reason);
        setState('none');
      } else {
        setState('building');
        timer = setTimeout(ask, code === 202 ? 6000 : 15000);
      }
    };
    void ask();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [near, url, f.clip?.state]);
  const why = (url && reasons.get(url)) || f.clip?.reason || 'no SPHEREx field covers this place yet';
  return (
    <div class="fclip" ref={box}>
      {state === 'ready' && near && url && <img src={url} alt={`Time-lapse of ${cardTitle(f)}: one frame per SPHEREx pass, the object ringed`} width={320} height={320} decoding="async" />}
      {state === 'asking' && <span class="mono-s">rendering the time-lapse…</span>}
      {state === 'building' && <span class="mono-s">building the field of this time-lapse…</span>}
      {state === 'none' && <span class="mono-s">no time-lapse: {why}</span>}
    </div>
  );
}

export function FindCard({ f, onOpen, current }: { f: CardFind; onOpen: (id: string) => void; current: boolean }) {
  const looks = f.viewers;
  const by = f.credit.first_marked_by ? `Marked first by ${f.credit.first_marked_by}` : `Found by ${f.credit.found_by.replace(/^SPHEREx Space, /, '')}`;
  const checked = f.credit.checked_by.length ? ` · checked by ${f.credit.checked_by.slice(0, 2).join(', ')}${f.credit.checked_by.length > 2 ? ` +${f.credit.checked_by.length - 2}` : ''}` : '';
  return (
    <li class={`fcard glass${current ? ' on' : ''}`}>
      <button class="fcard-pic" onClick={() => onOpen(f.id)} title="Open the report of this finding" aria-label={`Open the report: ${cardTitle(f)}`}>
        <Clip f={f} />
        <span class={`badge ${STAGE_BADGE[f.stage] ?? ''} fcard-stage`}>{STAGE_LABEL[f.stage] ?? f.stage}</span>
        <span class="fcard-promise" title="Promise, 0 to 100: what to look at first (Method, findings)">
          {f.promise}
        </span>
      </button>
      <div class="fcard-body">
        <b class="fcard-title" title={cardTitle(f)}>
          {cardTitle(f)}
        </b>
        <span class="mono-s fcard-sub">
          {KIND_ONE[f.kind] ?? f.kind} · {f.place}
        </span>
        <span class="fcard-meta">
          <span title="How many people have looked at this finding in the lab" class={!looks && f.stage === 'new' ? 'wait' : ''}>
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
              <path d="M1 8s2.6-4.5 7-4.5S15 8 15 8s-2.6 4.5-7 4.5S1 8 1 8z" fill="none" stroke="currentColor" stroke-width="1.3" />
              <circle cx="8" cy="8" r="2" fill="currentColor" />
            </svg>
            {looks ? `${looks} ${looks === 1 ? 'look' : 'looks'}` : f.stage === 'new' ? 'waiting for a look' : 'no looks yet'}
          </span>
        </span>
        <span class="mono-s fcard-credit" title={`${f.credit.found_by}${checked}`}>
          {by}
          {checked}
        </span>
        <a class="btn sm primary fcard-go" href={checkHref(f)}>
          Check this finding
        </a>
      </div>
    </li>
  );
}
