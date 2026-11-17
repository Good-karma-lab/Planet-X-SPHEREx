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
  if (m) return { title: 'Loading the exposures', detail: `${m[1]} of ${m[2]} exposures · ${m[3]} MB`, frac: Number(m[1]) / Math.max(1, Number(m[2])) };
  m = s.match(/^pass (\d+) of (\d+): D\d stacked \((\d+) of (\d+) detector stacks\)/);
  if (m) return { title: `Stacking pass ${m[1]} of ${m[2]}`, detail: `${m[3]} of ${m[4]} detector stacks`, frac: Number(m[3]) / Math.max(1, Number(m[4])) };
  m = s.match(/^quick picture of pass (\d+) of (\d+)/);
  if (m) return { title: `Stacking pass ${m[1]} of ${m[2]}`, detail: 'first exposures', frac: null };
  m = s.match(/^pass (\d+): published/);
  if (m) return { title: `Pass ${m[1]} stacked`, detail: passes ? `${pictures} of ${passes} passes ready` : '', frac: passes ? pictures / passes : null };
  m = s.match(/^(\d+) exposures found in (\d+) passes/);
  if (m) return { title: 'Finding the exposures', detail: `${m[1]} exposures in ${m[2]} passes`, frac: null };
  if (/^comparing passes/.test(s)) return { title: 'Comparing the passes', detail: '', frac: null };
  m = s.match(/^Request queued(?: · (\d+) ahead)?/);
  if (m) return { title: 'Request queued', detail: m[1] ? `${m[1]} ahead` : '', frac: null };
  if (/^loading the seasons/.test(s)) return { title: 'Loading the pictures', detail: passes ? `${pictures} of ${passes} passes` : '', frac: passes ? pictures / passes : null };
  if (/^failed: /.test(s)) return { title: 'The field could not be made', detail: s.replace(/^failed: /, ''), frac: null, failed: true };
  if (/restarting/.test(s)) return { title: 'The sky service is restarting', detail: 'trying again', frac: null };
  return { title: s.replace(/…$/, '') || 'Asking the sky service', detail: '', frac: null };
}

/** The steps of making a field, as the overlay lists them; a field the service already has only loads its pictures. */
const STAGES = ['Finding the exposures', 'Reading the exposures', 'Stacking the passes', 'Comparing the passes', 'Ready'];
const LOAD_STAGES = ['Loading the pictures', 'Ready'];

/** Which stage of the list a step is ('' stages: queued or asking, before the first). */
export function stageOf(step: LoadStep): { list: string[]; at: number } {
  const t = step.title;
  if (/^Loading the pictures/.test(t)) return { list: LOAD_STAGES, at: 0 };
  if (/^Finding the exposures/.test(t)) return { list: STAGES, at: 0 };
  if (/^Loading the exposures/.test(t)) return { list: STAGES, at: 1 };
  if (/^(Stacking pass|Pass \d+ stacked)/.test(t)) return { list: STAGES, at: 2 };
  if (/^Comparing the passes/.test(t)) return { list: STAGES, at: 3 };
  return { list: STAGES, at: -1 };
}

/** seconds without any change of the step before the overlay says the archive is slow */
export const STALL_S = 20;

const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

/** The overlay over the picture while a field is made or loaded: a looping video of a telescope building the sky (generated with
 *  Gemini, marked so) whole and at full brightness at the top of the card, under it the step, a real progress bar with its numbers, the list of steps, the time since the start.
 *  It fades out when the picture is ready. `step` null: nothing is loading. */
export function LoadOverlay({ step, field }: { step: LoadStep | null; field: string }) {
  const [last, setLast] = useState<LoadStep | null>(step);
  const [fading, setFading] = useState(false);
  const started = useRef(Date.now());
  const changed = useRef({ at: Date.now(), key: '' });
  const [, tick] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  // the furthest step reached: the service reads the next pass's exposures while it stacks the last one, so the ticks never go back
  const reached = useRef(-1);
  // a new field starts the clock and the steps again
  useEffect(() => {
    started.current = Date.now();
    reached.current = -1;
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
  const st = stageOf(shown.title === 'Ready' ? { ...last, title: last.title } : shown);
  const list = st.list;
  reached.current = Math.max(reached.current, st.at);
  const at = reached.current;
  const stalled = !!step && !step.failed && (Date.now() - changed.current.at) / 1000 >= STALL_S;
  const elapsed = (Date.now() - started.current) / 1000;
  return (
    <div class={`loadov${fading ? ' fading' : ''}${shown.failed ? ' failed' : ''}`} role="status" aria-live="polite" data-testid="load-block">
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
          <i class={shown.frac === null ? 'busy' : ''} style={{ width: `${shown.frac === null ? 30 : Math.max(2, Math.min(100, shown.frac * 100))}%` }} />
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
