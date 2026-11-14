import { useEffect, useState } from 'preact/hooks';

interface Card {
  mission: string;
  date_obs: string;
  exposure_s: number;
  wcs_offset_arcsec: number;
  gaia_matches: number;
  found: boolean;
  offset_from_prediction_arcsec: number;
  g_mag: number | null;
  snr: number | null;
}

/** 3I/ATLAS seen by a second space agency on the day SPHEREx saw it: CSA's NEOSSat frame, measured by tools/neossat_card.py. */
export function NeossatCard() {
  const [c, setC] = useState<Card | null>(null);
  useEffect(() => {
    void fetch('/neossat/3i-2025-12-14.json')
      .then((r) => (r.ok ? r.json() : null))
      .then(setC)
      .catch(() => {});
  }, []);
  if (!c) return null;
  const t = c.date_obs.replace('T', ' ').slice(0, 16);
  return (
    <div class="xcard">
      <img src="/neossat/3i-2025-12-14.png" alt="3I/ATLAS in a NEOSSat frame, ringed at the place JPL predicts" />
      <div>
        <b>Also seen by NEOSSat (CSA)</b>
        <p>
          {t} UT, {c.exposure_s.toFixed(0)} s: the comet is found {c.offset_from_prediction_arcsec.toFixed(0)}″ from JPL's predicted place,
          at G ≈ {c.g_mag?.toFixed(1)} with S/N {c.snr?.toFixed(0)}.
        </p>
        <p class="faint">
          Frame from the Canadian Astronomy Data Centre; its pointing refitted on {c.gaia_matches} Gaia DR3 stars (the header was{' '}
          {c.wcs_offset_arcsec.toFixed(0)}″ off). North up, east left.
        </p>
      </div>
    </div>
  );
}
