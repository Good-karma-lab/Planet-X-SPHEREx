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
const lab = (ra: number, dec: number, fov: number) =>
  formatHash({ screen: 'lab', params: { ra: ra.toFixed(5), dec: dec.toFixed(5), fov: String(fov) } });

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
        setNote(
          `${fmtN(Object.keys(cov.cells).length)} of ${fmtN(12 * 4 ** cov.order)} cells hold exposure centres · ${fmtN(total)} exposures · brighter = more exposures (log scale)`,
        );
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
    const load = () =>
      void fetch('/api/v1/health', { cache: 'no-store' })
        .then((r) => r.json())
        .then(setH)
        .catch(() => {});
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
            {kv(
              'Product versions',
              Object.entries(h?.product_versions ?? {})
                .map(([k, v]) => `${k} ${v}`)
                .join(' · ') || '—',
            )}
            {kv('Data read from S3 since start', h?.s3?.bytes != null ? `${(h.s3.bytes / 1e6).toFixed(0)} MB` : '—')}
          </dl>
        </section>
        <CoverageMap />
      </div>
    </>
  );
}

interface CatObj {
  n: string;
  ra: number;
  dec: number;
  pmra: number | null;
  pmdec: number | null;
  b?: number;
}
interface TrackLite {
  speed: number;
  pa: number;
  passes: number[];
  check: string;
  gaia: { source_id: string; pm: number; g?: number | null } | null;
}
interface AnomalyLite {
  n: number;
  score: number;
  drivers: { lo: number; hi: number; z: number; share: number }[];
  identified?: { simbad?: { state: string; name?: string; type?: string }; gaia?: { state: string; source_id?: string } };
}
interface Live {
  name: string;
  ra: number;
  dec: number;
  movers: { state: string; result?: { passes: number; sources_per_pass: number[]; tracks: TrackLite[] } } | null;
  anomalies: {
    state: string;
    result?: { with_spectrum: number; threshold: number | null; anomalies: AnomalyLite[]; reason: string } | null;
  } | null;
}

const FIELD = 20;
const CHECK: Record<string, string> = { same: 'Gaia: same motion', none: 'no Gaia motion', pending: 'Gaia not checked yet' };

/** What the searches found in the 20′ fields of the five test targets, read live from the service (nothing is started here). */
function useLive(): Live[] {
  const [live, setLive] = useState<Live[]>([]);
  useEffect(() => {
    let stop = false;
    void (async () => {
      const cat = (await fetch('/catalog.json').then((r) => r.json())) as { catalog: CatObj[] };
      const targets = cat.catalog.filter((o) => o.b);
      const out: Live[] = [];
      for (const o of targets) {
        const dt = 2025.9 - 2000;
        const dec = o.dec + ((o.pmdec ?? 0) * dt) / 3.6e6;
        const ra = (o.ra + ((o.pmra ?? 0) * dt) / 3.6e6 / Math.cos((((dec + o.dec) / 2) * Math.PI) / 180) + 360) % 360;
        const q = `ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&fov=${FIELD}`;
        const [mv, an] = await Promise.all([
          fetch(`/api/v1/movers?${q}`)
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null),
          fetch(`/api/v1/anomalies?${q}`)
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null),
        ]);
        out.push({ name: o.n, ra, dec, movers: mv, anomalies: an });
        if (stop) return;
        setLive([...out]);
      }
    })();
    return () => {
      stop = true;
    };
  }, []);
  return live;
}

function FindingsPage() {
  const live = useLive();
  return (
    <>
      <div class="pg-h">
        <h1>Findings</h1>
        <p class="hint">
          What the searches find in real SPHEREx exposures. The cards below are read live from the service; every number is measured by this
          system on SPHEREx Level-2 files.
        </p>
      </div>
      <div class="pg-grid">
        {live.map((l) => {
          const mv = l.movers?.result;
          const an = l.anomalies?.result;
          return (
            <section class="card glass" key={l.name}>
              <h3>
                {l.name} · {FIELD}′ field
              </h3>
              <dl class="kv">
                {kv(
                  'Movers',
                  !l.movers || l.movers.state === 'waiting'
                    ? 'field not built yet'
                    : !mv
                      ? l.movers.state
                      : mv.passes < 3
                        ? `${mv.passes} passes: a track needs 3`
                        : mv.tracks.length
                          ? mv.tracks
                              .map(
                                (t) =>
                                  `${t.speed.toFixed(2)}″/yr toward ${t.pa.toFixed(0)}° in ${t.passes.length} passes (${CHECK[t.check] ?? t.check})`,
                              )
                              .join('; ')
                          : `none among ${mv.sources_per_pass.join(' / ')} sources per pass`,
                )}
                {kv(
                  'Anomalies',
                  !l.anomalies || l.anomalies.state === 'waiting' || l.anomalies.state === 'idle'
                    ? 'not searched yet'
                    : !an
                      ? l.anomalies.state
                      : an.reason ||
                        (an.anomalies.length
                          ? an.anomalies
                              .slice(0, 3)
                              .map(
                                (a) =>
                                  `${a.n}. ${a.identified?.simbad?.name ?? (a.identified?.gaia?.state === 'ok' ? `Gaia DR3 ${a.identified.gaia.source_id}` : 'not in SIMBAD or Gaia')} (score ${a.score.toFixed(1)})`,
                              )
                              .join('; ')
                          : `none of ${an.with_spectrum} spectra above ${an.threshold?.toFixed(2)}`),
                )}
              </dl>
              <p>
                <a href={lab(l.ra, l.dec, FIELD)}>Open in the lab</a>
              </p>
            </section>
          );
        })}
        <section class="card glass">
          <h3>Motion measured on the stacks (validation)</h3>
          <dl class="kv">
            {kv("Barnard's Star", '10.08 ″/yr toward 355° (SIMBAD 10.39 ″/yr, 356°); 3 passes, 386 exposures')}
            {kv('ε Indi B', '4.58 ″/yr toward 122° (SIMBAD 4.68 ″/yr, 122°); 4 passes, 494 exposures')}
            {kv('Star width (FWHM)', '8.8″, isolated unsaturated stars')}
            {kv('Pass-to-pass registration', '0.14–0.26″ scatter')}
          </dl>
        </section>
        <section class="card glass">
          <h3>Spectra (validation)</h3>
          <dl class="kv">
            {kv('2MASS J17572542+0446559', '357 of 385 exposures used; 42 mJy in D2 against J 11.53 (39 mJy)')}
            {kv('A click in an open field', 'first point in about 2 s, all points in about 30 s')}
          </dl>
        </section>
      </div>
    </>
  );
}

function MethodPage() {
  const steps: [string, string][] = [
    [
      'Exposures',
      'SPHEREx Level-2 spectral images from the public archive bucket (DR1 for the first year, QR3 after it), read by HTTP range requests: only the rows a field needs, cached on disk.',
    ],
    [
      'Index',
      'Every exposure (1.63 million) with its footprint (TAN-SIP sky polygon with a 60″ margin), time, detector and release, in an R*-tree; reconciled against the bucket.',
    ],
    ['Seasons', 'The exposures of a place group into passes about six months apart; each pass becomes one frame of the video.'],
    [
      'Wavelength',
      "Each detector pixel has its own wavelength, from the mission's per-pixel calibration maps; the named bands select pixels by it.",
    ],
    [
      'Cleaning',
      'Pipeline flags, a leave-one-out cosmic-ray test, a per-visit test, halo and saturation rules; saturated cores are drawn white, never as holes.',
    ],
    [
      'Stacking',
      'Super-resolution splat of every exposure onto a common grid (Gaussian kernel 0.35 detector pixel with 16 or more exposures, 0.50 below); each pass registered to the first on field stars.',
    ],
    [
      'Colour',
      'Six detector stacks map to one picture by a fixed 3 × 6 matrix (every wavelength its rainbow colour); each pass is white-balanced on the peaks of its brightest unsaturated stars.',
    ],
    [
      'Display',
      "asinh law (linear knee 0.40, white point fixed or at the field's 99.9th / 99.5th percentile); Sharpen boosts faint point sources only, with one gain for every channel, never near saturated cores.",
    ],
    ['Difference', 'Each pass against the first as significance per pixel; |z| > 4 is shown (magenta brighter, cyan fainter).'],
    [
      'Spectrum',
      "A click measures the source in every exposure that covers it: PSF fit on a 7 × 7 stamp with the detector zone's PSF, weights from the variance plane, three rounds of 6σ rejection; flux from the local solid angle; a moving star is measured where it was on each date; points beyond 5σ of their 7 neighbours in wavelength are flagged.",
    ],
    [
      'Movers',
      'Sources per pass (peaks of the stack smoothed to 7″, 8 MAD over the local background; a saturated zone is one source); stationary within 2.5″; straight tracks of 3 or more passes at 3–120 ″/yr; then each track against Gaia DR3 at the pass dates: same motion, a centroid effect (rejected), or no Gaia motion.',
    ],
    [
      'Anomalies',
      "Stationary sources with S/N ≥ 12 and every track, measured in every exposure of the field; spectra in 24 bins of ln λ; the field's ordinary star from the median of the bright half and two principal components; score with a 5 % floor; anomalous above median + 6 robust σ; the passport names the driving bins and what SIMBAD and Gaia know.",
    ],
    ['Film', 'The visits inside one pass, aligned; the static sky removed; what moves between visits is shown in amber.'],
    [
      'Archive',
      "Cutouts of DSS2 red (1985–1999), 2MASS Ks (1997–2001) and AllWISE W1 (2010) from all-sky HiPS tiles, north up and east left like the SPHEREx picture; a test target is ringed where it was at each survey's epoch.",
    ],
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
        <p class="hint">
          SPHEREx Sky: real SPHEREx exposures of any patch of the sky, as a video of the seasons and the spectral bands inside them. Not
          affiliated with or endorsed by NASA.
        </p>
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
        {screen === 'index' ? (
          <IndexPage health={health as FullHealth | null} />
        ) : screen === 'findings' ? (
          <FindingsPage />
        ) : screen === 'method' ? (
          <MethodPage />
        ) : (
          <AboutPage />
        )}
      </div>
    </div>
  );
}
