/** The line for a far-search peak with a bright 2MASS star 25–60″ away (service/findings.py WING_*): its glow outside the search's mask
 * most likely makes the peak. Empty without one. */
export const wingLine = (w: { sep?: number | null } | null | undefined): string =>
  w
    ? `A bright star ${w.sep != null ? `${Math.round(w.sep)}″` : 'nearby'} away: its glow outside the search's mask most likely makes this peak`
    : '';
