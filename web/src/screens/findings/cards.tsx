import { useEffect, useRef, useState } from 'preact/hooks';
import { nextTask, taskHash } from '../../lib/account';
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
  clip: {
    url: string;
    state: 'ready' | 'waiting' | 'building' | 'none';
    reason?: string;
    still?: string;
    blink?: string[] | null;
    note?: string;
  } | null;
  name: string | null;
  /** is the object visible in its own picture (S/N in the ring of 3 or more)? null: the picture is not made yet */
  visible?: boolean | null;
  visible_sn?: number | null;
  /** what the review of the clips saw, when it looks like an artefact (a note, not a status) */
  note?: string | null;
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
const STAGE_BADGE: Record<string, string> = {
  new: 'orange',
  in_review: 'blue',
  submitted: 'purple',
  known: 'green',
  artefact: '',
  candidate: 'yellow',
  confirmed: 'green',
};
export const KIND_ONE: Record<string, string> = {
  mover: 'Mover',
  browndwarf: 'Brown dwarf',
  red: 'Red source',
  anomaly: 'Spectral anomaly',
  pair: 'Pair changing',
  film: 'Time-lapse track',
  peak: 'Planet X peak',
  submitted: 'Found by a person',
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
    ? {
        ra: fp.ra.toFixed(5),
        dec: fp.dec.toFixed(5),
        fov: String(fp.fov),
        find: f.id,
        find_ra: f.ra.toFixed(5),
        find_dec: f.dec.toFixed(5),
      }
    : {
        ra: f.ra.toFixed(5),
        dec: f.dec.toFixed(5),
        fov: f.kind === 'film' ? '60' : '20',
        find: f.id,
        find_ra: f.ra.toFixed(5),
        find_dec: f.dec.toFixed(5),
      };
  return formatHash({ screen: 'lab', params: { ...base, task: 'finding', finding: f.id } });
}

/** "Check this finding": the task is taken from the service (it is kept for a signed-in user), then the lab opens with it. Without the
 *  accounts of the service, or when it does not answer in two seconds, the lab opens with the find alone. */
export async function takeTask(f: CardFind): Promise<void> {
  let href = checkHref(f);
  try {
    const t = await Promise.race([nextTask('finding', f.id), new Promise<null>((res) => setTimeout(() => res(null), 2500))]);
    if (t) href = taskHash(t); // the lab's own address of the task (a guest gets one too; it is kept for a signed-in user)
  } catch {
    // no accounts here: the find alone
  }
  location.hash = href;
}

/** A finding a person submitted (`GET /api/findings/submitted`, the accounts of the service) as a card: its picture is the time-lapse of its place. */
export interface Submitted {
  finding_id: string;
  status?: string;
  title?: string;
  ra?: number;
  dec?: number;
  created?: string;
  credit?: { name: string; role: string }[];
}
export function submittedCard(s: Submitted): CardFind {
  const names = (s.credit ?? []).map((c) => c.name);
  const stage = (s.status && s.status in STAGE_LABEL ? s.status : 'submitted') as CardFind['stage'];
  const has = s.ra != null && s.dec != null;
  return {
    id: s.finding_id,
    kind: 'submitted',
    place: s.created ? s.created.slice(0, 10) : '',
    fov: 20,
    ra: s.ra ?? 0,
    dec: s.dec ?? 0,
    rate: null,
    snr: null,
    score: null,
    status: s.status ?? 'submitted',
    verdict: '',
    stage,
    promise: 0,
    viewers: (s.credit ?? []).filter((c) => c.role === 'checked').length,
    credit: {
      found_by: names[0] ?? 'a person',
      machine: false,
      first_marked_by: names[0] ?? null,
      checked_by: (s.credit ?? []).filter((c) => c.role === 'checked').map((c) => c.name),
      looks: {},
    },
    clip: has ? { url: `/api/clips/place.webp?ra=${s.ra!.toFixed(4)}&dec=${s.dec!.toFixed(4)}&size=320&ring=1`, state: 'waiting' } : null,
    name: s.title ?? 'Submitted finding',
    field: null,
    group: null,
    passes: null,
  };
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
  const [state, setState] = useState<ClipState>(() =>
    url && known.get(url) === 'ready' ? 'ready' : !url || known.get(url) === 'none' ? 'none' : 'idle',
  );
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
        } else await r.arrayBuffer(); // the clip is now in the browser's cache: the picture below reads it from there
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
      {state === 'ready' && near && url && (
        <img
          src={url}
          alt={`Time-lapse of ${cardTitle(f)}: one frame per SPHEREx pass, the object ringed`}
          width={320}
          height={320}
          decoding="async"
        />
      )}
      {state !== 'ready' && near && f.clip?.still && (
        <img
          class="fstill"
          src={f.clip.still}
          alt=""
          loading="lazy"
          onError={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')}
        />
      )}
      {state === 'asking' && <span class="mono-s">rendering the time-lapse…</span>}
      {state === 'building' && <span class="mono-s">building the field of this time-lapse…</span>}
      {state === 'none' && <span class="mono-s">no time-lapse: {why}</span>}
    </div>
  );
}

export function FindCard({
  f,
  onOpen,
  current,
  mine = false,
  people = '',
}: {
  f: CardFind;
  onOpen: (id: string) => void;
  current: boolean;
  mine?: boolean;
  /** what people said in the classification tasks, one line (empty: nobody classified it) */
  people?: string;
}) {
  const looks = f.viewers;
  const by = f.credit.first_marked_by
    ? `Marked first by ${f.credit.first_marked_by}`
    : `Found by ${f.credit.found_by.replace(/^SPHEREx Space, /, '')}`;
  const checked = f.credit.checked_by.length
    ? ` · checked by ${f.credit.checked_by.slice(0, 2).join(', ')}${f.credit.checked_by.length > 2 ? ` +${f.credit.checked_by.length - 2}` : ''}`
    : '';
  return (
    <li class={`fcard glass${current ? ' on' : ''}`}>
      <button
        class="fcard-pic"
        onClick={() => onOpen(f.id)}
        title="Open the report of this finding"
        aria-label={`Open the report: ${cardTitle(f)}`}
      >
        <Clip f={f} />
        <span class={`badge ${STAGE_BADGE[f.stage] ?? ''} fcard-stage`}>{STAGE_LABEL[f.stage] ?? f.stage}</span>
        {f.kind !== 'submitted' && (
          <span class="fcard-promise" title="Promise, 0 to 100: what to look at first (Method, findings)">
            {f.promise}
          </span>
        )}
      </button>
      <div class="fcard-body">
        <a
          class="fcard-title"
          href={f.kind === 'submitted' ? checkHref(f) : `#/findings/${encodeURIComponent(f.id)}`}
          title="Open the page of this finding"
        >
          {cardTitle(f)}
        </a>
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
          {mine && <span class="fcard-mine">viewed by me</span>}
          {f.visible_sn != null && (
            <span
              class={f.visible ? '' : 'wait'}
              title="How far the brightest point inside the ring stands above the noise of this picture"
            >
              S/N {f.visible_sn.toFixed(1)} in the ring
            </span>
          )}
        </span>
        {f.kind === 'peak' && f.clip?.state === 'ready' && !f.clip.blink && (
          <span class="mono-s fcard-sub">the sum along the orbit is not kept here: the passes alone are shown</span>
        )}
        {f.note && (
          <span class="fcard-note" title="A machine note from the review of the clips. It sets no status: judge it in the lab.">
            Machine note: {f.note}
          </span>
        )}
        {people && (
          <span class="mono-s fcard-people" title="What people answered about this find in the classification tasks">
            {people}
          </span>
        )}
        <span class="mono-s fcard-credit" title={`${f.credit.found_by}${checked}`}>
          {by}
          {checked}
        </span>
        <a
          class="btn sm primary fcard-go"
          href={checkHref(f)}
          onClick={(e) => {
            e.preventDefault();
            void takeTask(f);
          }}
        >
          Check this finding
        </a>
      </div>
    </li>
  );
}
