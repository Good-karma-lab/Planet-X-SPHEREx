import { render } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import './tour.css';

interface Step {
  /** the hash the step shows */
  hash: string;
  title: string;
  text: string;
}

const LAB = '#/lab?ra=133.75999&dec=-7.24220&fov=5';

/** Five steps on a real field: WISE 0855−0714, the coldest known brown dwarf, at 5′. Each step opens its screen; nothing is simulated. */
export const STEPS: Step[] = [
  {
    hash: '#/explore',
    title: '1 · The sky',
    text: 'This is the globe: the whole sky with every SPHEREx exposure. Drag to rotate, scroll to zoom, click to aim, double-click to open the frame in the lab. Orange stars are the test targets.',
  },
  {
    hash: LAB,
    title: '2 · A field',
    text: 'This is WISE 0855−0714, the coldest known brown dwarf, as SPHEREx saw it. The picture loops through the seasons: look for the one source that is not where it was. Field sizes 2′ to 60′ are at the top right; the coloured buttons below choose wavelength bands.',
  },
  {
    hash: LAB,
    title: '3 · A spectrum',
    text: 'Click the source at the centre and open the Spectrum tab. Points appear exposure by exposure; below the chart a table compares them with the 2MASS and WISE catalogues. A ratio near 1 means the numbers are right.',
  },
  {
    hash: LAB,
    title: '4 · Months and days',
    text: 'In the timeline, Months shows the seasons, Days shows the visits inside one season, where asteroids hop from frame to frame. The Searches panel lists the movers found and how many chance alone would make.',
  },
  {
    hash: '#/findings',
    title: '5 · What was found',
    text: 'Findings lists every lead the searches made, ranked, each with its checks. A lead is a track or an odd spectrum no catalogue explains. It is not a discovery. Open one for its report.',
  },
];

const KEY = 'spherex-tour-seen';
const seen = () => {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
};
const mark = () => {
  try {
    localStorage.setItem(KEY, '1');
  } catch {
    // the tour is simply offered again next time
  }
};

/** Start the tour from anywhere (a button on the About page): `startTour()`. */
export const startTour = () => window.dispatchEvent(new CustomEvent('spherex:tour'));

/**
 * The guided tour: a card at the bottom left with Back / Next / Skip that opens the screen of each step. It starts by itself once for a first-time visitor on the globe
 * (`auto`), and whenever startTour() is called. Mount it once in the app shell.
 */
export function Tour({ auto = true }: { auto?: boolean }) {
  const [k, setK] = useState<number | null>(null);
  useEffect(() => {
    const on = () => setK(0);
    window.addEventListener('spherex:tour', on);
    // only on the bare globe: the front page (landing) and a link that names a place or an object (#/explore?ra=..) are never taken over by the tour
    const bare = () => /^#\/(globe|explore)$/.test(location.hash);
    if (auto && !seen() && bare()) {
      // it starts only for someone still on the bare globe: a click on the rail (or any other address) before it is due cancels it,
      // so the tour never takes a person back to the globe
      const t = setTimeout(() => {
        if (bare()) setK(0);
      }, 2500);
      const away = () => {
        if (!bare()) clearTimeout(t);
      };
      window.addEventListener('hashchange', away);
      return () => {
        clearTimeout(t);
        window.removeEventListener('hashchange', away);
        window.removeEventListener('spherex:tour', on);
      };
    }
    return () => window.removeEventListener('spherex:tour', on);
  }, [auto]);
  useEffect(() => {
    if (k == null) return;
    location.hash = STEPS[k]!.hash;
    // a visitor who leaves the step's screen on their own has left the tour: it ends (and counts as seen)
    const screen = (h: string) => h.replace(/\?.*/, '').replace('#/globe', '#/explore');
    const left = () => {
      if (screen(location.hash) !== screen(STEPS[k]!.hash)) {
        mark();
        setK(null);
      }
    };
    window.addEventListener('hashchange', left);
    return () => window.removeEventListener('hashchange', left);
  }, [k]);
  if (k == null) return null;
  const s = STEPS[k]!;
  const done = () => {
    mark();
    setK(null);
  };
  return (
    <div class="tour glass" role="dialog" aria-label="Guided tour">
      <div class="tour-h">
        <b>{s.title}</b>
        <button class="lnk" onClick={done} aria-label="Skip the tour">
          Skip
        </button>
      </div>
      <p>{s.text}</p>
      <div class="tour-f">
        <span class="mono-s">
          {k + 1} of {STEPS.length}
        </span>
        <span>
          {k > 0 && (
            <button class="btn sm" onClick={() => setK(k - 1)}>
              Back
            </button>
          )}{' '}
          {k < STEPS.length - 1 ? (
            <button class="btn sm primary" onClick={() => setK(k + 1)}>
              Next
            </button>
          ) : (
            <button class="btn sm primary" onClick={done}>
              Done
            </button>
          )}
        </span>
      </div>
    </div>
  );
}

let mounted = false;
/** Puts the tour in its own element of the page, once, so that it needs no place in the app shell and survives screen changes. */
export function mountTour(): void {
  if (mounted || typeof document === 'undefined') return;
  mounted = true;
  const el = document.createElement('div');
  document.body.append(el);
  render(<Tour />, el);
}
