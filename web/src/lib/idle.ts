// A drawing loop that goes quiet when nothing moves. While the screen is busy (an animation runs, playback plays, the person
// touched the page in the last moments) it draws on every animation frame; at rest it looks a few times a second (a tile or a part
// that arrives is still drawn within a quarter of a second); in a hidden tab it does nothing. Any input wakes it at once.

/** at rest: how often the loop looks (ms) */
export const REST_MS = 250;
/** after the last input or busy frame, how long the loop keeps every frame (ms) */
export const AWAKE_MS = 2000;

const INPUT = ['pointerdown', 'pointermove', 'wheel', 'keydown', 'touchstart'] as const;

export const pageVisible = (): boolean => typeof document === 'undefined' || document.visibilityState !== 'hidden';

/**
 * Run `frame(now)` on every animation frame while `busy()` (or within AWAKE_MS of input or of a busy frame), else every REST_MS.
 * Returns stop() and wake() (call wake when something changed that the loop must draw now, e.g. a state change from a click).
 */
export function idleLoop(frame: (now: number) => void, busy: () => boolean): { stop: () => void; wake: () => void } {
  let raf = 0;
  let timer: ReturnType<typeof setTimeout> | 0 = 0;
  let stopped = false;
  let awakeUntil = performance.now() + AWAKE_MS;
  const schedule = () => {
    if (stopped || raf || timer) return;
    if (performance.now() < awakeUntil) raf = requestAnimationFrame(run);
    else timer = setTimeout(run, REST_MS);
  };
  const run = (t?: number) => {
    raf = 0;
    timer = 0;
    if (stopped) return;
    const now = typeof t === 'number' ? t : performance.now();
    if (pageVisible()) {
      frame(now);
      if (busy()) awakeUntil = performance.now() + AWAKE_MS;
    }
    schedule();
  };
  const wake = () => {
    awakeUntil = performance.now() + AWAKE_MS;
    if (timer) {
      clearTimeout(timer);
      timer = 0;
    }
    schedule();
  };
  const onVis = () => pageVisible() && wake();
  for (const e of INPUT) window.addEventListener(e, wake, { passive: true });
  document.addEventListener('visibilitychange', onVis);
  schedule();
  return {
    wake,
    stop: () => {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      if (timer) clearTimeout(timer);
      for (const e of INPUT) window.removeEventListener(e, wake);
      document.removeEventListener('visibilitychange', onVis);
    },
  };
}

/** setInterval that skips while the tab is hidden, and runs once as soon as it is seen again (polls of the service). */
export function visibleInterval(fn: () => void, ms: number): () => void {
  const t = setInterval(() => pageVisible() && fn(), ms);
  let missed = false;
  const onVis = () => {
    if (!pageVisible()) {
      missed = true;
      return;
    }
    if (missed) {
      missed = false;
      fn();
    }
  };
  document.addEventListener('visibilitychange', onVis);
  return () => {
    clearInterval(t);
    document.removeEventListener('visibilitychange', onVis);
  };
}
