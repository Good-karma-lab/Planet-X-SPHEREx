// The pendulum of the passes (docs/plan/02-methods/2.3-views.md 2.3.4): 1→2→…→N→…→2, hold 430 ms, crossfade 100 ms with t²(3−2t).

export const HOLD_MS = 430;
export const FADE_MS = 100;

/** The order of the passes in one period of the pendulum: 0,1,…,N−1,…,1 (no return jump). */
export function order(n: number): number[] {
  if (n <= 1) return [0];
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(i);
  for (let i = n - 2; i >= 1; i--) out.push(i);
  return out;
}

export interface Frame {
  a: number; // pass shown
  b: number; // pass fading in
  t: number; // 0..1 eased mix of b over a
  step: number; // index in the order
}

export function frameAt(ms: number, n: number): Frame {
  const seq = order(n);
  const slot = HOLD_MS + FADE_MS;
  const k = Math.floor(ms / slot) % seq.length;
  const within = ms % slot;
  const a = seq[k]!;
  const b = seq[(k + 1) % seq.length]!;
  if (within < HOLD_MS || seq.length === 1) return { a, b: a, t: 0, step: k };
  const x = (within - HOLD_MS) / FADE_MS;
  return { a, b, t: x * x * (3 - 2 * x), step: k };
}
