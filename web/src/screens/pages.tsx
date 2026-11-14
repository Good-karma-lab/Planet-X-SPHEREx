import { useEffect, useRef, useState } from 'preact/hooks';
import type { Health } from '../lib/api';
import { outline } from '../lib/healpix';
import { formatHash, type Screen } from '../lib/route';
import { D2R } from '../lib/sky';

const fmtN = (n: number | undefined | null) => (n ?? 0).toLocaleString('en-US');
const kv = (k: string, v: string) => (
  <div class="kvr">
    <dt>{k}</dt>
    <dd>{v}</dd>
  </div>
);
const lab = (ra: number, dec: number, fov: number) => formatHash({ screen: 'lab', params: { ra: ra.toFixed(5), dec: dec.toFixed(5), fov: String(fov) } });

interface IndexInfo {
  files?: number;
  [k: string]: unknown;
}
type FullHealth = Health & { index?: IndexInfo | null; s3?: { bytes?: number; requests?: number } | null };

/** Mollweide map of the exposure index: one HEALPix cell (order 3) per patch, brighter = more exposures. */
function CoverageMap() {
  const ref = useRef<HTMLCanvasElement>(null);
  const [note, setNote] = useState('');
  useEffect(() => {
    void fetch('/api/v1/coverage/map')
      .then((r) => (r.ok ? r.json() : null))
      .then((cov: { order: number; releases: string[]; cells: Record<string, number[]> } | null) => {
        const cv = ref.current;
        if (!cv || !cov) {
          setNote('The coverage map is not ready yet.');
          return;
        }
        const dpr = Math.min(2, devicePixelRatio || 1);
        const W = Math.max(300, cv.parentElement!.clientWidth);
        const H = W / 2;
        cv.width = W * dpr;
        cv.height = H * dpr;
        cv.style.height = `${H}px`;
        const c = cv.getContext('2d')!;
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        const proj = (ra: number, dec: number): [number, number] => {
          const lam = (((ra - 180 + 540) % 360) - 180) * D2R;
          const phi = dec * D2R;
          let th = phi;
          for (let i = 0; i < 12; i++) {
            const d = 2 + 2 * Math.cos(2 * th);
            if (Math.abs(d) < 1e-9) break;
            th -= (2 * th + Math.sin(2 * th) - Math.PI * Math.sin(phi)) / d;
          }
          return [W / 2 - ((lam * Math.cos(th)) / Math.PI) * (W / 2), H / 2 - Math.sin(th) * (H / 2)];
        };
        c.strokeStyle = 'rgba(140,225,240,.25)';
        c.beginPath();
        c.ellipse(W / 2, H / 2, W / 2, H / 2, 0, 0, 7);
        c.stroke();
        let max = 1;
        let total = 0;
        for (const v of Object.values(cov.cells)) {
          const n = v.reduce((s, x) => s + x, 0);
          max = Math.max(max, n);
          total += n;
        }
        for (const [k, v] of Object.entries(cov.cells)) {
          const n = v.reduce((s, x) => s + x, 0);
          const pts = outline(cov.order, Number(k), 4).map(([ra, de]) => proj(ra, de));
          const xs = pts.map((p) => p[0]);
          if (Math.max(...xs) - Math.min(...xs) > W / 3) continue;
          c.fillStyle = `hsla(175,80%,60%,${0.1 + (0.75 * Math.log(1 + n)) / Math.log(1 + max)})`;
          c.beginPath();
          pts.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
          c.closePath();
          c.fill();
        }
        setNote(`${fmtN(Object.keys(cov.cells).length)} of ${fmtN(12 * 4 ** cov.order)} cells hold exposure centres · ${fmtN(total)} exposures · brighter = more exposures (log scale)`);
      })
      .catch(() => setNote('The coverage map is not ready yet.'));
  }, []);
  return (
    <section class="card wide glass">
      <h3>Sky coverage by SPHEREx exposures</h3>
      <div class="covwrap">
        <canvas ref={ref} />
      </div>
      <p class="hint">{note}</p>
    </section>
  );
}

function IndexPage({ health }: { health: FullHealth | null }) {
  const [h, setH] = useState<FullHealth | null>(health);
  useEffect(() => {
    const load = () => void fetch('/api/v1/health', { cache: 'no-store' }).then((r) => r.json()).then(setH).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);
  const ix = (h?.index ?? {}) as Record<string, unknown>;
  return (
    <>
      <div class="pg-h">
        <h1>Exposure index</h1>
        <p class="hint">What the sky service can search: every SPHEREx Level-2 exposure with its footprint, time and detector.</p>
      </div>
      <div class="pg-grid">
        <section class="card glass">
          <h3>What can be searched</h3>
          <dl class="kv">
            {kv('Exposures indexed', fmtN(ix['files'] as number))}
            {kv('Releases', String(ix['releases'] ?? 'DR1 (year 1), QR3 (after it)'))}
            {kv('Built', String(ix['built_at'] ?? ix['updated_at'] ?? '—').replace('T', ' '))}
            {kv('Source', String(ix['source'] ?? 'IRSA TAP, checked against the archive bucket'))}
          </dl>
        </section>
        <section class="card glass">
          <h3>Service</h3>
          <dl class="kv">
            {kv('Version', h?.version ?? '—')}
            {kv('Started', (h?.started_at ?? '—').replace('T', ' '))}
            {kv('Product versions', Object.entries(h?.product_versions ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ') || '—')}
            {kv('Data read from S3 since start', h?.s3?.bytes != null ? `${(h.s3.bytes / 1e6).toFixed(0)} MB` : '—')}
          </dl>
        </section>
        <CoverageMap />
      </div>
    </>
  );
}

function FindingsPage() {
  return (
    <>
      <div class="pg-h">
        <h1>Findings</h1>
        <p class="hint">What the real exposures show. Every number is measured by this system on SPHEREx Level-2 files.</p>
      </div>
      <div class="pg-grid">
        <section class="card glass">
          <h3>Barnard's Star moves</h3>
          <dl class="kv">
            {kv('Measured motion', '10.08 ″/yr toward 355°')}
            {kv('Catalogue (SIMBAD)', '10.39 ″/yr toward 356°')}
            {kv('Passes', '3 (Aug 2025 – Sep 2026), 386 exposures')}
            {kv('Scatter of the places', '0.49″')}
          </dl>
          <p>
            <a href={lab(269.4479, 4.7687, 5)}>Open in the lab</a>
          </p>
        </section>
        <section class="card glass">
          <h3>ε Indi B moves</h3>
          <dl class="kv">
            {kv('Measured motion', '4.58 ″/yr toward 122°')}
            {kv('Catalogue (SIMBAD)', '4.68 ″/yr toward 122°')}
            {kv('Passes', '4 (Apr 2025 – Oct 2026), 494 exposures')}
            {kv('Scatter of the places', '0.05″')}
          </dl>
          <p>
            <a href={lab(331.077, -56.794, 5)}>Open in the lab</a>
          </p>
        </section>
        <section class="card glass">
          <h3>Image quality</h3>
          <dl class="kv">
            {kv('Star width (FWHM)', '8.8″, isolated unsaturated stars')}
            {kv('Pass-to-pass registration', '0.14–0.26″ scatter')}
            {kv('Field of 7′, cold', '30–49 s')}
          </dl>
        </section>
      </div>
    </>
  );
}

function MethodPage() {
  const steps: [string, string][] = [
    ['Exposures', 'SPHEREx Level-2 spectral images from the public archive bucket, read by HTTP range requests: only the rows a field needs.'],
    ['Index', 'Every exposure with its footprint (TAN-SIP sky polygon, 60″ margin), time, detector and release, in an R*-tree.'],
    ['Seasons', 'Exposures of a place group into passes, about six months apart; each pass becomes one frame of the video.'],
    ['Wavelength', 'Each pixel has its own wavelength (the per-pixel calibration of the mission); the named bands select pixels by it.'],
    ['Cleaning', 'Pipeline flags, a leave-one-out cosmic-ray test, a per-visit test, halo and saturation rules; saturated cores are drawn white.'],
    ['Stacking', 'Super-resolution splat of every exposure onto a common grid; registration of each pass to the first on field stars.'],
    ['Colour', 'Six detector stacks map to one colour picture by a fixed matrix with a white balance per pass; nothing is tinted by hand.'],
    ['Difference', 'Each pass against the first, as significance per pixel: |z| > 4 is shown (magenta brighter, cyan fainter).'],
    ['Film', 'The visits inside one pass, aligned; the static sky removed; what moves between visits is shown in amber.'],
  ];
  return (
    <>
      <div class="pg-h">
        <h1>Method</h1>
        <p class="hint">How every picture and number is computed, step by step.</p>
      </div>
      <section class="card glass">
        <table class="tbl">
          <tbody>
            {steps.map(([k, v]) => (
              <tr key={k}>
                <th>{k}</th>
                <td>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function AboutPage() {
  return (
    <>
      <div class="pg-h">
        <h1>About</h1>
        <p class="hint">SPHEREx Sky: real SPHEREx exposures of any patch of the sky, as a video of the seasons and the spectral bands inside them. Not affiliated with or endorsed by NASA.</p>
      </div>
      <div class="pg-grid">
        <section class="card glass">
          <h3>Data</h3>
          <ul>
            <li>SPHEREx Level-2 spectral images: NASA / JPL-Caltech, served by NASA/IPAC Infrared Science Archive (IRSA).</li>
            <li>Object catalogue: SIMBAD, CDS Strasbourg.</li>
            <li>Globe backgrounds: DSS2, 2MASS, AllWISE and IRIS all-sky surveys (HiPS), CDS and IRSA.</li>
          </ul>
        </section>
        <section class="card glass">
          <h3>Team</h3>
          <ul>
            <li>Good Karma Lab · NASA Space Apps Challenge 2026, Barcelona.</li>
            <li>Licence: Apache-2.0.</li>
            <li>Icons: Phosphor (MIT).</li>
          </ul>
        </section>
      </div>
    </>
  );
}

export function InfoPage({ screen, health }: { screen: Screen; health: Health | null }) {
  return (
    <div class="screen">
      <div class={`ipage ${screen === 'method' ? 'method' : ''}`}>
        {screen === 'index' ? <IndexPage health={health as FullHealth | null} /> : screen === 'findings' ? <FindingsPage /> : screen === 'method' ? <MethodPage /> : <AboutPage />}
      </div>
    </div>
  );
}
