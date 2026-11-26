/** The line for a far-search peak with a bright 2MASS star whose light outside the search's mask most likely makes the peak
 * (service/findings.py WING_BANDS: its glow within 25–92″ or, for the brightest, 6′; its ghost about 3′ away). Empty without one. */
export const wingLine = (w: { sep?: number | null; k?: number | null } | null | undefined): string => {
  if (!w) return '';
  const away = w.sep == null ? 'nearby' : w.sep >= 90 ? `${(w.sep / 60).toFixed(1)}′ away` : `${Math.round(w.sep)}″ away`;
  const ghost = w.sep != null && w.sep >= 165 && w.sep <= 215 && (w.k == null || w.k > 3);
  return ghost
    ? `A bright star ${away}: its ghost, which falls about 3′ from it, most likely makes this peak`
    : `A bright star ${away}: its glow outside the search's mask most likely makes this peak`;
};

/** service/clips.py SIGHT_Z: the dot of a peak shown to people stands this far above the noise of its sum along the orbit */
export const SIGHT_Z = 5.0;

/** Why a Planet X peak stands behind the faint filter (service/findings.py hidden_why), in the words of the chip's tooltip */
const WHY: Record<string, string> = {
  photometry: 'far peaks (beyond 200 au) on detectors 1–3 whose photometry is under 6σ',
  star: 'peaks a bright star’s light makes: its glow outside the search’s mask (2MASS K ≤ 10 at 25–60″, K ≤ 7 to 92″, K ≤ 3 to 6′) or its ghost about 3′ away (K ≤ 7.5)',
  no_sum: 'peaks whose sum along the orbit is not kept here, so there is nothing to see them in',
  unseen: `peaks whose dot does not stand out of the noise in the sum along the orbit (under ${SIGHT_Z.toFixed(1)}×)`,
};

/** The tooltip of the faint-peaks chip: why each hidden peak is hidden, with the counts the list gave (`faint_why`). */
export function faintTitle(why: Record<string, number> | null | undefined, tail: string): string {
  const parts = Object.keys(WHY)
    .filter((k) => (why?.[k] ?? 0) > 0)
    .map((k) => `${why![k]} ${WHY[k]}`);
  const list = parts.length ? parts : Object.values(WHY);
  return `Left out until you ask for them, as most are not bodies: ${list.join('; ')}${parts.length > 1 ? ' (a peak may be counted twice)' : ''}. ${tail}`;
}

/** The line on a hidden peak's card (when the faint peaks are shown): why it is hidden. Empty when it is not. */
export function hiddenLine(why: string[] | null | undefined, sight?: { z?: number | null } | null): string {
  const out: string[] = [];
  for (const w of why ?? []) {
    if (w === 'photometry') out.push('its photometry is under 6σ');
    else if (w === 'no_sum') out.push('its sum along the orbit is not kept here, so nobody can see it');
    else if (w === 'unseen')
      out.push(
        `its dot stands ${sight?.z != null ? `${sight.z.toFixed(1)}×` : 'too little'} above the noise of the sum along the orbit (${SIGHT_Z.toFixed(1)}× needed)`,
      );
    else if (w === 'star') out.push('a bright star’s glow most likely makes it');
  }
  return out.length ? `Hidden with the faint peaks: ${out.join('; ')}` : '';
}
