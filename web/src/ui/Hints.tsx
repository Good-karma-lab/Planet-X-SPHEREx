// The newcomer's hints (replaces the guided tour): one short hint at a time, pointing at the real control it is about, on the screen
// the person has reached by themself — landing → globe → a square in the lab → a mark → Findings → Cabinet. Nothing navigates for
// them. Each hint is shown once; "Skip all" ends them; "Show the hints again" (About, Cabinet) or startTour() starts them over.
import { render } from 'preact';
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import './tour.css';

export interface Hint {
  id: string;
  /** the screen it belongs to (the first part of the address: landing, explore, lab, findings, me) */
  screen: string;
  /** the control it points at; the hint waits until it is on screen */
  at: string;
  title: string;
  /** one short line */
  text: string;
  /** only in a lab with a task of this kind */
  task?: string;
}

export const HINTS: Hint[] = [
  { id: 'start', screen: 'landing', at: '[data-testid=landing-start]', title: 'Real SPHEREx pictures', text: 'Start here. No account needed to look.' },
  { id: 'square', screen: 'explore', at: '[data-testid=give-square]', title: 'Give me a square', text: 'A piece of sky nobody has looked through yet.' },
  { id: 'play', screen: 'lab', at: '[data-testid=picture]', title: 'Watch it play', text: 'The passes take turns. Find a dot that steps while the stars stay.', task: 'cell' },
  { id: 'mark', screen: 'lab', at: '[data-testid=task-missed]', title: 'It moves and has no ring?', text: 'Mark it here, then click it on the picture.', task: 'cell' },
  { id: 'answer', screen: 'lab', at: '[data-testid=task-panel] .tp-btns', title: 'Then answer', text: 'Nothing here, or Done, with marks. After a minute and every pass.', task: 'cell' },
  { id: 'finds', screen: 'findings', at: '.fcard', title: 'What the machine found', text: 'Each is a lead, not a discovery. Check one: a few questions.' },
  { id: 'cabinet', screen: 'me', at: '.cb-work', title: 'Your work', text: 'Squares you looked through, your marks, findings and points.' },
];

const KEY = 'spherex-hints-seen';
const OLD = 'spherex-tour-seen';
const readSeen = (): Set<string> => {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY) ?? '[]') as string[]);
  } catch {
    return new Set();
  }
};
const writeSeen = (s: Set<string>) => {
  try {
    localStorage.setItem(KEY, JSON.stringify([...s]));
  } catch {
    // shown again next time: a convenience, nothing is lost
  }
};

/** Show every hint again, from the screen the person is on. */
export const startTour = () => window.dispatchEvent(new CustomEvent('spherex:tour'));

const screenOf = (hash: string) => (hash.replace(/^#\/?/, '').split(/[?/]/)[0] || 'landing').replace('globe', 'explore');
// a task's address names its kind (task=<id>&kind=cell), or the kind alone (task=cell)
const taskOf = (hash: string) => {
  const q = new URLSearchParams(hash.split('?')[1] ?? '');
  return q.get('kind') || q.get('task') || '';
};

/** where the card goes: under the control, else above it, kept inside the window (16 px gutter) */
function place(r: DOMRect, w: number, h: number): { left: number; top: number; up: boolean } {
  const W = window.innerWidth;
  const H = window.innerHeight;
  const left = Math.max(16, Math.min(W - w - 16, r.left + r.width / 2 - w / 2));
  // a big control (the picture): the card sits inside it, near its top, not off the screen
  if (r.height > H * 0.45) return { left, top: Math.max(16, r.top + 56), up: false };
  const below = r.bottom + 12;
  if (below + h <= H - 16) return { left, top: below, up: false };
  return { left, top: Math.max(16, r.top - h - 12), up: true };
}

export function Hints() {
  const [hash, setHash] = useState(typeof location === 'undefined' ? '' : location.hash);
  const [seen, setSeen] = useState<Set<string>>(() => {
    const s = readSeen();
    // someone who went through the old tour has seen enough of the globe's hint
    try {
      if (localStorage.getItem(OLD) === '1') s.add('square');
    } catch {
      // nothing to carry over
    }
    return s;
  });
  const [off, setOff] = useState(() => {
    try {
      return localStorage.getItem(`${KEY}-off`) === '1';
    } catch {
      return false;
    }
  });
  const [box, setBox] = useState<DOMRect | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean } | null>(null);
  useEffect(() => {
    const on = () => setHash(location.hash);
    const again = () => {
      writeSeen(new Set());
      setSeen(new Set());
      setOff(false);
      try {
        localStorage.removeItem(`${KEY}-off`);
      } catch {
        // fine
      }
    };
    window.addEventListener('hashchange', on);
    window.addEventListener('spherex:tour', again);
    return () => {
      window.removeEventListener('hashchange', on);
      window.removeEventListener('spherex:tour', again);
    };
  }, []);
  const screen = screenOf(hash);
  // this screen's hints not seen yet, in their order; the one shown is the first whose control is on screen now (a control hidden
  // in a phone's sheet at rest never holds up the next)
  const due = off ? [] : HINTS.filter((h) => h.screen === screen && !seen.has(h.id) && (!h.task || h.task === taskOf(hash)));
  const [curId, setCurId] = useState<string | null>(null);
  const hint = due.find((h) => h.id === curId);
  const dueKey = due.map((h) => h.id).join(',');
  // follow the control: it may appear late (the lab draws its picture after the field is read) and move with the layout
  useEffect(() => {
    setBox(null);
    setCurId(null);
    if (!due.length) return;
    let raf = 0;
    let last = '';
    const tick = () => {
      let pick: { h: Hint; r: DOMRect } | null = null;
      for (const h of due) {
        const r = document.querySelector<HTMLElement>(h.at)?.getBoundingClientRect();
        if (r && r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight) {
          pick = { h, r };
          break;
        }
      }
      const k = pick ? `${pick.h.id}:${Math.round(pick.r.left)},${Math.round(pick.r.top)},${Math.round(pick.r.width)},${Math.round(pick.r.height)}` : '';
      if (k !== last) {
        last = k;
        setCurId(pick?.h.id ?? null);
        setBox(pick?.r ?? null);
      }
      raf = requestAnimationFrame(tick);
    };
    // a moment after the screen opens, so the hint never covers the first look
    const t = setTimeout(() => (raf = requestAnimationFrame(tick)), 900);
    return () => {
      clearTimeout(t);
      cancelAnimationFrame(raf);
    };
  }, [dueKey, screen]);
  useLayoutEffect(() => {
    const c = card.current;
    if (!box || !c) return setPos(null);
    setPos(place(box, c.offsetWidth, c.offsetHeight));
  }, [box]);
  useEffect(() => {
    if (!hint || !box) return;
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') skipAll();
      else if (e.key === 'Enter' && document.activeElement === document.body) next();
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  });
  if (!hint || !box) return null;
  const left = HINTS.filter((h) => !seen.has(h.id) && h.id !== hint.id).length;
  const next = () => {
    const s = new Set(seen).add(hint.id);
    writeSeen(s);
    setSeen(s);
  };
  function skipAll() {
    const s = new Set(HINTS.map((h) => h.id));
    writeSeen(s);
    setSeen(s);
    setOff(true);
    try {
      localStorage.setItem(`${KEY}-off`, '1');
    } catch {
      // fine
    }
  }
  const n = HINTS.indexOf(hint) + 1;
  return (
    <>
      {/* the control the hint is about, outlined */}
      <div
        class="hint-ring"
        aria-hidden="true"
        style={{ left: `${box.left - 4}px`, top: `${box.top - 4}px`, width: `${box.width + 8}px`, height: `${box.height + 8}px` }}
      />
      <div
        ref={card}
        class={`hint-card glass${pos?.up ? ' up' : ''}`}
        role="dialog"
        aria-label={`Hint: ${hint.title}`}
        style={pos ? { left: `${pos.left}px`, top: `${pos.top}px` } : { visibility: 'hidden', left: '0px', top: '0px' }}
        data-testid="hint"
      >
        <b>{hint.title}</b>
        <p>{hint.text}</p>
        <div class="hint-f">
          <span class="mono-s">
            {n} of {HINTS.length}
          </span>
          <button class="lnk" onClick={skipAll} data-testid="hint-skip">
            Skip all
          </button>
          <button class="btn sm primary" onClick={next} data-testid="hint-next">
            {left ? 'Got it' : 'Done'}
          </button>
        </div>
      </div>
    </>
  );
}

let mounted = false;
/** Puts the hints in their own element of the page, once: they need no place in the app shell and survive screen changes. */
export function mountTour(): void {
  if (mounted || typeof document === 'undefined') return;
  mounted = true;
  const el = document.createElement('div');
  document.body.append(el);
  render(<Hints />, el);
}
