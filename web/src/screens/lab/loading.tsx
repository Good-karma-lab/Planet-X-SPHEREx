import { useEffect, useRef, useState } from 'preact/hooks';

// While a field is being built or loaded, the picture never plays half-built stacks: it holds one finished pass (dimmed) or stays
// dark, and a progress block in the middle of the picture says what the service is doing, with a bar when it says how far.

export interface LoadStep {
  /** what is being done, short */
  title: string;
  /** how far, in numbers ('' when the service does not say) */
  detail: string;
  /** 0..1, or null when there is no measure */
  frac: number | null;
  failed?: boolean;
}

/** The service's phase line (lib/plain.ts wording) as a progress step. `pictures` / `passes`: finished pass pictures in hand. */
export function loadStep(phase: string, pictures: number, passes: number): LoadStep {
  const s = phase.trim();
  let m = s.match(/^reading the exposures: (\d+) of (\d+), (\d+) MB downloaded/);
  // the megabytes only once there are some (a cached exposure costs none: "0 MB" read as stuck)
  if (m) return { title: 'Loading the exposures', detail: `${m[1]} of ${m[2]} exposures${Number(m[3]) > 0 ? ` · our server has read ${m[3]} MB of NASA’s archive` : ''}`, frac: Number(m[1]) / Math.max(1, Number(m[2])) };
  m = s.match(/^pass (\d+) of (\d+): D\d stacked \((\d+) of (\d+) detector stacks\)/);
  if (m) return { title: `Stacking pass ${m[1]} of ${m[2]}`, detail: `${m[3]} of ${m[4]} detector stacks`, frac: Number(m[3]) / Math.max(1, Number(m[4])) };
  m = s.match(/^quick picture of pass (\d+) of (\d+)/);
  if (m) return { title: `Stacking pass ${m[1]} of ${m[2]}`, detail: 'first exposures', frac: null };
  m = s.match(/^pass (\d+): published/);
  if (m) return { title: `Pass ${m[1]} stacked`, detail: passes ? `${pictures} of ${passes} passes ready` : '', frac: passes ? pictures / passes : null };
  m = s.match(/^(\d+) exposures found in (\d+) passes/);
  if (m) return { title: 'Finding the exposures', detail: `${m[1]} exposures in ${m[2]} passes`, frac: null };
  if (/^comparing passes/.test(s)) return { title: 'Comparing the passes', detail: '', frac: null };
  // the service is done; the first pass is being put on the canvas
  if (/^done\b/.test(s)) return { title: 'Drawing the first pass', detail: '', frac: null };
  m = s.match(/^Request queued(?: · (\d+) ahead)?/);
  if (m) return { title: 'Request queued', detail: m[1] ? `${m[1]} ahead` : '', frac: null };
  if (/^loading the seasons/.test(s)) return { title: 'Loading the pictures', detail: passes ? `${pictures} of ${passes} passes` : '', frac: passes ? pictures / passes : null };
  if (/^failed: /.test(s)) return { title: 'The field could not be made', detail: s.replace(/^failed: /, ''), frac: null, failed: true };
  if (/restarting/.test(s)) return { title: 'The sky service is restarting', detail: 'trying again', frac: null };
  return { title: s.replace(/…$/, '') || 'Asking the sky service', detail: '', frac: null };
}

/** The wait of the Days scale as a step: the visits of the pass are being read (`note`: the service's line, '' before it says). */
export function daysStep(note: string, failed: boolean): LoadStep {
  const s = note.trim();
  return { title: failed ? 'No visits to play' : 'Loading the visits', detail: s || 'asking for the visits of the pass…', frac: null, failed };
}

/** The steps of making a field, as the overlay lists them; a field the service already has only loads its pictures. */
const STAGES = ['Finding the exposures', 'Reading the exposures', 'Stacking the passes', 'Comparing the passes', 'Drawing the first pass'];
const LOAD_STAGES = ['Loading the pictures', 'Drawing the first pass'];

/** Which stage of the list a step is ('' stages: queued or asking, before the first). */
export function stageOf(step: LoadStep): { list: string[]; at: number } {
  const t = step.title;
  if (/^Loading the pictures/.test(t)) return { list: LOAD_STAGES, at: 0 };
  if (/^(Loading the visits|No visits)/.test(t)) return { list: [], at: -1 };
  if (/^Finding the exposures/.test(t)) return { list: STAGES, at: 0 };
  if (/^Loading the exposures/.test(t)) return { list: STAGES, at: 1 };
  if (/^(Stacking pass|Pass \d+ stacked)/.test(t)) return { list: STAGES, at: 2 };
  if (/^Comparing the passes/.test(t)) return { list: STAGES, at: 3 };
  // the last step of either list: the pictures are here, the first pass is being put on the canvas
  if (/^Drawing the first pass/.test(t)) return { list: LOAD_STAGES, at: 1 };
  return { list: STAGES, at: -1 };
}

/** seconds without any change of the step before the overlay says the archive is slow */
export const STALL_S = 20;

const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

/** The overlay over the picture while a field is made or loaded: a looping video of a telescope building the sky (generated with
 *  Gemini, marked so) whole and at full brightness at the top of the card, under it the step, a real progress bar with its numbers, the list of steps, the time since the start.
 *  It fades out when the picture is ready. `step` null: nothing is loading. */
export function LoadOverlay({ step, field, firstLook = false }: { step: LoadStep | null; field: string; firstLook?: boolean }) {
  const [last, setLast] = useState<LoadStep | null>(step);
  const [fading, setFading] = useState(false);
  const started = useRef(Date.now());
  const changed = useRef({ at: Date.now(), key: '' });
  const [, tick] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  // the furthest step reached: the service reads the next pass's exposures while it stacks the last one, so the ticks never go back
  const reached = useRef(-1);
  // the whole wait's share done so far: it only grows; and how much of the exposures are read
  const overall = useRef(0);
  const readFrac = useRef(0);
  const listRef = useRef<string[] | null>(null);
  // a new field starts the clock and the steps again
  useEffect(() => {
    started.current = Date.now();
    reached.current = -1;
    overall.current = 0;
    readFrac.current = 0;
    listRef.current = null;
  }, [field]);
  useEffect(() => {
    if (step) {
      setLast(step);
      setFading(false);
      const key = `${step.title}|${step.detail}`;
      if (key !== changed.current.key) changed.current = { at: Date.now(), key };
      return;
    }
    if (!last) return;
    setFading(true);
    const t = setTimeout(() => {
      setLast(null);
      setFading(false);
    }, 450);
    return () => clearTimeout(t);
  }, [step?.title, step?.detail, step?.frac, step === null]);
  useEffect(() => {
    if (!last) return;
    const t = setInterval(() => tick((x) => x + 1), 1000);
    // the video plays only while the page is seen
    const vis = () => {
      const v = video.current;
      if (!v) return;
      if (document.visibilityState === 'visible') void v.play().catch(() => {});
      else v.pause();
    };
    document.addEventListener('visibilitychange', vis);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', vis);
    };
  }, [!!last]);
  if (!last) return null;
  const shown = step ?? { ...last, title: 'Ready', detail: '', frac: 1 };
  const st0 = stageOf(shown.title === 'Ready' ? { ...last, title: last.title } : shown);
  // "Drawing the first pass" is the last step of whichever list was on show (a made field's five, a kept field's two)
  const st = /^Drawing the first pass/.test(shown.title) && listRef.current ? { list: listRef.current, at: listRef.current.length - 1 } : st0;
  if (!/^Drawing the first pass/.test(shown.title)) listRef.current = st0.list;
  const list = st.list;
  reached.current = Math.max(reached.current, st.at);
  // reading is the step in hand until every exposure is read (the stacking lines come in between)
  const at = list === STAGES && readFrac.current > 0 && readFrac.current < 0.99 ? Math.min(reached.current, 1) : reached.current;
  const stalled = !!step && !step.failed && (Date.now() - changed.current.at) / 1000 >= STALL_S;
  const elapsed = (Date.now() - started.current) / 1000;
  // one bar for the whole wait, that only goes forward, each step weighted by the time it takes: reading the exposures is most of a
  // new field (the service stacks a pass while it reads the next: the step in hand, not the furthest, says how far)
  const f = shown.frac === null ? 0 : Math.max(0, Math.min(1, shown.frac));
  const t = shown.title;
  if (/^Loading the exposures/.test(t)) readFrac.current = Math.max(readFrac.current, f);
  const overallNow =
    t === 'Ready'
      ? 1
      : /^Finding the exposures/.test(t)
        ? 0.03
        : /^Loading the exposures/.test(t)
          ? 0.05 + 0.7 * f
          : /^(Stacking pass|Pass \d+ stacked)/.test(t)
            ? // stacking comes in between the reading (a pass is stacked while the next is read): it counts once all are read
              0.05 + 0.7 * readFrac.current + (readFrac.current >= 0.99 ? 0.2 * f : 0)
            : /^Comparing the passes/.test(t)
              ? 0.95
              : /^Loading the pictures/.test(t)
                ? 0.9 * f
                : /^Drawing the first pass/.test(t)
                  ? 0.97
                  : 0;
  overall.current = Math.max(overall.current, overallNow);
  const done = overall.current;
  // a rough time left once there is a rate to go by (10 s in, a sixth of the way): said in half minutes, never before
  const left = elapsed > 10 && done > 0.16 && done < 0.98 ? (elapsed * (1 - done)) / done : null;
  const leftWord = left == null ? '' : left < 45 ? 'under a minute left' : `about ${Math.max(1, Math.round(left / 30) / 2)} min left`;
  return (
    <div class={`loadov${fading ? ' fading' : ''}${shown.failed ? ' failed' : ''}${firstLook ? ' first' : ''}`} role="status" aria-live="polite" data-testid="load-block">
      {/* the real sky is on the picture already (the all-sky map's first look): the card steps aside, the video goes on in it, small */}
      {firstLook && <span class="lo-first">First look · SPHEREx all-sky map{shown.failed ? '' : ' · this field is being built'}</span>}
      <div class="lo-media">
        {reduced ? (
          <img class="lo-video" src="/loading/telescope_poster.jpg" alt="" />
        ) : (
          <video ref={video} class="lo-video" autoplay muted loop playsInline poster="/loading/telescope_poster.jpg" aria-hidden="true">
            <source src="/loading/telescope_loop.webm" type="video/webm" />
            <source src="/loading/telescope_loop.mp4" type="video/mp4" />
          </video>
        )}
        <span class="lo-ai" title="The video was generated with Google Gemini">AI-generated</span>
      </div>
      <div class="lo-body">
        <b class="lo-step">{shown.title}</b>
        <span class="lo-bar" aria-hidden="true">
          <i style={{ width: `${Math.max(2, Math.min(100, done * 100))}%` }} />
        </span>
        <span class="lo-detail">{shown.detail || '\u00a0'}</span>
        <ul class="lo-stages">
          {list.slice(0, -1).map((name, k) => (
            <li key={name} class={k < at || shown.title === 'Ready' ? 'done' : k === at ? 'now' : ''}>
              <i aria-hidden="true">{k < at || shown.title === 'Ready' ? '✓' : k === at ? '•' : ''}</i>
              {name}
            </li>
          ))}
        </ul>
        <span class="lo-time">
          {fmtTime(elapsed)}
          {leftWord && !stalled ? ` · ${leftWord}` : ''}
          {stalled ? ' · Still working: NASA’s archive is slow right now' : ''}
        </span>
      </div>
    </div>
  );
}

/** The block in the middle of the picture. Fixed size: its text changes, the block does not move or grow. */
export function LoadBlock({ step }: { step: LoadStep }) {
  return (
    <div class={`float loadblock${step.failed ? ' failed' : ''}`} role="status" aria-live="polite" data-testid="load-block">
      <b>{step.title}</b>
      <span class="lb-bar" aria-hidden="true">
        <i class={step.frac === null ? 'busy' : ''} style={{ width: `${step.frac === null ? 30 : Math.max(2, Math.min(100, step.frac * 100))}%` }} />
      </span>
      <span class="lb-detail">{step.detail}</span>
    </div>
  );
}
