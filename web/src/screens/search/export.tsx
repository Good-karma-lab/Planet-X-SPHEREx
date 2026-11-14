import { useEffect, useState } from 'preact/hooks';

interface Track {
  speed: number;
  pa: number;
  passes: number[];
  mjd?: number[];
  ra_2025_9?: number;
  dec_2025_9?: number;
  snr?: (number | null)[];
  check: string;
  gaia?: { source_id: string; pm: number; g?: number | null } | null;
}
interface Anom {
  n: number;
  ra: number;
  dec: number;
  score: number;
  snr?: number | null;
  moves?: boolean;
  identified?: { simbad?: { name?: string }; gaia?: { source_id?: string } };
}

interface Found {
  tracks: Track[];
  anomalies: Anom[];
  chance: { mean: number; trials: number } | null;
}

/** What the searches found in a field, as the service has it now (movers and anomalies); null while neither has an answer. */
export function useFound(ra: number, dec: number, fov: number): Found | null {
  const [f, setF] = useState<Found | null>(null);
  useEffect(() => {
    setF(null);
    const ctl = new AbortController();
    const q = `ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&fov=${fov}`;
    Promise.all([
      fetch(`/api/v1/movers?${q}`, { signal: ctl.signal }).then((r) => (r.ok ? r.json() : null)),
      fetch(`/api/v1/anomalies?${q}`, { signal: ctl.signal }).then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([m, a]) => {
        const mr = m?.state === 'done' ? m.result : null;
        const ar = a?.state === 'done' ? a.result : null;
        if (mr || ar) setF({ tracks: mr?.tracks ?? [], anomalies: ar?.anomalies ?? [], chance: mr?.chance ?? null });
      })
      .catch(() => {});
    return () => ctl.abort();
  }, [ra, dec, fov]);
  return f;
}

const csvCell = (v: unknown) => {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function foundCsv(ra: number, dec: number, fov: number, f: Found): string {
  const head = `# SPHEREx Sky: leads in the ${fov}' field at RA ${ra.toFixed(5)} Dec ${dec.toFixed(5)}; a lead is not a discovery\n# kind,id,ra_deg,dec_deg,rate_arcsec_per_yr,pa_deg,passes,gaia_check,gaia_source,score,name\n`;
  const rows = [
    ...f.tracks.map((t, i) => ['track', i + 1, t.ra_2025_9, t.dec_2025_9, t.speed, t.pa, t.passes.length, t.check, t.gaia?.source_id, '', '']),
    ...f.anomalies.map((a) => ['anomaly', a.n, a.ra, a.dec, '', '', '', '', a.identified?.gaia?.source_id, a.score, a.identified?.simbad?.name]),
  ];
  return head + rows.map((r) => r.map(csvCell).join(',')).join('\n') + '\n';
}

/**
 * Export of what the searches found in the field on show: CSV and JSON, built in the page from the service's movers and anomalies answers (the same numbers as the cards).
 * Put beside "Copy link"; renders nothing while the field has no finished search.
 */
export function ExportFound({ ra, dec, fov }: { ra: number; dec: number; fov: number }) {
  const f = useFound(ra, dec, fov);
  if (!f || (!f.tracks.length && !f.anomalies.length)) return null;
  const name = `spherex-${ra.toFixed(3)}_${dec.toFixed(3)}_${fov}arcmin`;
  const json = JSON.stringify({ field: { ra, dec, fov_arcmin: fov }, note: 'leads, not discoveries', chance: f.chance, tracks: f.tracks, anomalies: f.anomalies }, null, 2);
  return (
    <span class="export-found">
      <a class="lnk" download={`${name}.csv`} href={`data:text/csv;charset=utf-8,${encodeURIComponent(foundCsv(ra, dec, fov, f))}`}>
        Export CSV
      </a>
      <a class="lnk" download={`${name}.json`} href={`data:application/json;charset=utf-8,${encodeURIComponent(json)}`}>
        JSON
      </a>
    </span>
  );
}
