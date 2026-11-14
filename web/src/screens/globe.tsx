import { useEffect, useState } from 'preact/hooks';
import { formatHash } from '../lib/route';

interface Target {
  label: string;
  main_id: string;
  otype: string;
  ra_deg: number;
  dec_deg: number;
  epoch: number;
  pmra_mas_yr: number;
  pmdec_mas_yr: number;
}

// The place a test target is opened at: its catalogue place moved by proper motion to 2025.9, the middle of the SPHEREx passes
// (the same rule as the service's warm set, service/warm.py).
const EPOCH = 2025.9;

function atEpoch(t: Target): [number, number] {
  const dt = EPOCH - t.epoch;
  const dec = t.dec_deg + (t.pmdec_mas_yr * dt) / 3.6e6;
  const ra = t.ra_deg + (t.pmra_mas_yr * dt) / 3.6e6 / Math.cos(((dec + t.dec_deg) / 2) * (Math.PI / 180));
  return [((ra % 360) + 360) % 360, dec];
}

const WHAT: Record<string, string> = {
  "Barnard's Star": 'the fastest star on the sky: watch it swing',
  'ε Indi B': 'a cold brown dwarf: an orange-red dot among white stars',
  'WISE 0855−0714': 'the coldest brown dwarf known: seen only near 4–5 µm',
  'RW Aurigae': 'a young variable star in a star-forming region',
  'R Draconis': 'a pulsating red giant near the ecliptic pole',
};

const DEMO: { label: string; ra: number; dec: number; fov: number; what: string; extra: Record<string, string> }[] = [
  { label: 'Asteroids near the ecliptic', ra: 169.8917, dec: 4.3514, fov: 60, what: 'the film of one pass: asteroids hop from visit to visit', extra: { view: 'film', film: '61027' } },
  { label: 'Eris field', ra: 27.378, dec: -0.12, fov: 20, what: 'the dwarf planet beyond Neptune, 95 au away', extra: {} },
];

function open(ra: number, dec: number, fov: number, extra: Record<string, string> = {}) {
  location.hash = formatHash({ screen: 'lab', params: { ra: ra.toFixed(5), dec: dec.toFixed(5), fov: String(fov), ...extra } });
}

export function Globe() {
  const [targets, setTargets] = useState<Target[]>([]);
  const [coords, setCoords] = useState('');
  const [err, setErr] = useState('');
  useEffect(() => {
    void fetch('/targets.json')
      .then((r) => r.json())
      .then((d: { targets: Target[] }) => setTargets(d.targets));
  }, []);
  const go = () => {
    const m = coords.trim().split(/[\s,]+/).map(Number);
    if (m.length >= 2 && m.every(Number.isFinite) && m[0]! >= 0 && m[0]! < 360 && Math.abs(m[1]!) <= 90) {
      setErr('');
      open(m[0]!, m[1]!, m[2] && [2, 5, 10, 20, 60].includes(m[2]) ? m[2] : 5);
    } else setErr('RA and Dec in degrees, e.g. 269.4463 4.7679');
  };
  return (
    <div class="entry">
      <section>
        <h2>Test targets</h2>
        <div class="cards">
          {targets.map((t) => {
            const [ra, dec] = atEpoch(t);
            return (
              <button class="card" key={t.label} onClick={() => open(ra, dec, 5)} data-testid={`target-${t.label}`}>
                <b>{t.label}</b>
                <span>{WHAT[t.label] ?? t.otype}</span>
                <small>RA {ra.toFixed(4)} · Dec {dec.toFixed(4)} · 5′</small>
              </button>
            );
          })}
        </div>
      </section>
      <section>
        <h2>Fields</h2>
        <div class="cards">
          {DEMO.map((d) => (
            <button class="card" key={d.label} onClick={() => open(d.ra, d.dec, d.fov, d.extra)}>
              <b>{d.label}</b>
              <span>{d.what}</span>
              <small>RA {d.ra.toFixed(4)} · Dec {d.dec.toFixed(4)} · {d.fov}′</small>
            </button>
          ))}
        </div>
      </section>
      <section>
        <h2>Any place</h2>
        <div class="row">
          <input value={coords} onInput={(e) => setCoords((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === 'Enter' && go()} placeholder="RA Dec [field′]" aria-label="RA Dec in degrees" />
          <button class="chip on" onClick={go}>Open</button>
          <span class="dim">{err}</span>
        </div>
      </section>
    </div>
  );
}
