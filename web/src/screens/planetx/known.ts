// The known bodies Find Planet X meets on its own fields, and the rule that tells a known body's echo from a lead.

/** Where the live service's Eris, Makemake and Sedna detections stand in each pass: a Find Planet X peak this close is that body. */
export const KNOWN_AT: [number, number, string][] = [
  [27.3724, -0.114, 'Eris'],
  [26.3287, -0.4364, 'Eris'],
  [27.5024, 0.1488, 'Eris'],
  [199.9413, 21.4873, 'Makemake'],
  [202.3119, 20.1508, 'Makemake'],
  [61.86, 8.84, 'Sedna'],
];
export const KNOWN_R = 0.05; // degrees

/** The angle between two places on the sky, degrees. */
export const sep = (a: [number, number], b: [number, number]): number => {
  const r = Math.PI / 180;
  return (
    Math.acos(
      Math.min(1, Math.sin(a[1] * r) * Math.sin(b[1] * r) + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.cos((a[0] - b[0]) * r)),
    ) / r
  );
};

export interface PassPlace {
  ra?: number;
  dec?: number;
  sn?: number;
}

/** A known body's echo: in its strongest pass the peak sits on a known body's place (the body gives the signal; the other passes add
 *  noise along an orbit through it). The service checks the known bodies at the peak's reference date only, so it misses these.
 *  The body's name, or null. */
export function echoOf(f: { data: { peak?: { by_pass?: PassPlace[] | null } } }): string | null {
  const bp = (f.data.peak?.by_pass ?? []).filter((p) => p.ra != null && p.dec != null);
  if (!bp.length) return null;
  const top = Math.max(...bp.map((p) => p.sn ?? -Infinity));
  for (const p of bp) {
    if ((p.sn ?? -Infinity) < top) continue;
    const k = KNOWN_AT.find((k) => sep([k[0], k[1]], [p.ra!, p.dec!]) < KNOWN_R);
    if (k) return k[2];
  }
  return null;
}

/** Is a peak at a known body's place now (any of its passes' places)? */
export const atKnown = (ra: number, dec: number): boolean => KNOWN_AT.some((k) => sep([k[0], k[1]], [ra, dec]) < KNOWN_R);
