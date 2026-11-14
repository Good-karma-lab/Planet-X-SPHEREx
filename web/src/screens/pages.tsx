import { useEffect, useRef, useState } from 'preact/hooks';
import type { Health } from '../lib/api';
import { outline } from '../lib/healpix';
import { formatHash, type Screen } from '../lib/route';
import { D2R } from '../lib/sky';
import { NeossatCard } from './neossat';
import { FindingPicture } from './findings/thumb';
import './findings/findings.css';
import { FIELD, useLive } from './findings/live';
import { AboutBody } from './about/About';
import { LeadReport, LeadsList, plainLine, rankLeads, useLeadParam } from './findings/leads';
import { mountTour } from '../ui/Tour';
import { SearchedCounter, useSearched } from './search/searched';


mountTour();

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
        <p class="hint">
          How much of the SPHEREx archive this site can search, and how current that is. An exposure is one detector's picture of one pointing; the index lists every one
          with where on the sky it looks and when, so a field opens in seconds instead of searching the archive each time. The map shows how often each part of the sky has been seen.
        </p>
      </div>
      <div class="pg-grid">
        <section class="card glass">
          <h3>What can be searched</h3>
          <p class="hint" style="margin-top:0">Every SPHEREx Level-2 exposure in the public archive, with its footprint, time and detector.</p>
          <dl class="kv">
            {kv('Exposures we can search', fmtN(ix['files'] as number))}
            {kv('Releases', String(ix['releases'] ?? 'DR1 (year 1), QR3 (after it)'))}
            {kv('Index updated', String(ix['built_at'] ?? ix['updated_at'] ?? ix['bootstrap_at'] ?? '—').replace('T', ' '))}
            {kv('Source', String(ix['source'] ?? 'IRSA TAP, checked against the archive bucket'))}
          </dl>
        </section>
        <section class="card glass">
          <h3>The service behind this site</h3>
          <p class="hint" style="margin-top:0">Opens fields, runs the searches and measures spectra.</p>
          <dl class="kv">
            {kv('Version', h?.version ?? '—')}
            {kv('Running since', (h?.started_at ?? '—').replace('T', ' '))}
            {kv(
              'Searches and products (version)',
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

const CHECK: Record<string, string> = { same: 'Gaia: same motion', none: 'no Gaia motion', pending: 'Gaia not checked yet' };

function FindingsPage() {
  const live = useLive();
  const searched = useSearched();
  const leads = rankLeads(live);
  const [leadId, setLeadId] = useLeadParam();
  const open = leads.find((l) => l.id === leadId);
  if (leadId && open)
    return (
      <>
        <div class="pg-h">
          <h1>Findings</h1>
        </div>
        <LeadReport lead={open} onBack={() => setLeadId(null)} />
      </>
    );
  return (
    <>
      <div class="pg-h">
        <h1>Findings</h1>
        <p class="hint">
          What the searches find in real SPHEREx exposures. The cards below are read live from the service; every number is measured by this
          system on SPHEREx Level-2 files.
        </p>
      </div>
      <div class="fsum">
        <div><b>{live.length}</b><span>fields searched</span></div>
        <div><b>{live.reduce((a, l) => a + (l.movers?.result?.tracks.length ?? 0), 0)}</b><span>tracks between passes</span></div>
        <div><b>{live.reduce((a, l) => a + (l.movers?.result?.tracks.filter((t) => t.check === 'same').length ?? 0), 0)}</b><span>of them a Gaia star with the same motion</span></div>
        <div><b>{live.reduce((a, l) => a + (l.anomalies?.result?.anomalies.length ?? 0), 0)}</b><span>spectral anomalies</span></div>
      </div>
      <SearchedCounter fields={searched} />
      <LeadsList leads={leads} onOpen={setLeadId} />
      <div class="pg-grid">
        {live.map((l) => {
          const mv = l.movers?.result;
          const an = l.anomalies?.result;
          const chips: [string, string][] = [];
          if (mv && mv.passes >= 3) {
            for (const t of mv.tracks) chips.push([t.check === 'same' ? 'good' : 'warn', `${t.speed.toFixed(2)}″/yr · ${CHECK[t.check] ?? t.check}`]);
            if (mv.pairs?.length) chips.push(['warn', `${mv.pairs.length} pair${mv.pairs.length === 1 ? '' : 's'} changing in place`]);
          }
          if (an?.anomalies?.length) chips.push(['warn', `${an.anomalies.length} spectral anomal${an.anomalies.length === 1 ? 'y' : 'ies'}`]);
          return (
            <section class="card glass fcard" key={l.name}>
              <FindingPicture name={l.name} href={lab(l.ra, l.dec, FIELD)} fieldKey={mv?.field ?? null} passes={mv?.passes ?? 0} />
              <div class="fbody">
                <h3>
                  {l.name} · {FIELD}′ field
                </h3>
                <p class="plain">{plainLine(l)}</p>
                <div class="fchips">
                  {chips.length ? chips.map(([k, t], i) => <span key={i} class={`badge ${k === 'good' ? 'green' : 'orange'}`}>{t}</span>) : <span class="mono-s">nothing marked: nothing above the bar</span>}
                </div>
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
              </div>
            </section>
          );
        })}
        <section class="card glass">
          <h3>3I/ATLAS · interstellar comet · 14 Dec 2025</h3>
          <p>
            SPHEREx imaged the comet 55 times that day (IRSA's exposure table); the film of the pass rings it where JPL predicts it on every
            visit. The same day CSA's NEOSSat took it too:
          </p>
          <NeossatCard />
          <p>
            <a href={formatHash({ screen: 'lab', params: { ra: '166.82740', dec: '5.27870', fov: '60', film: '61023.6' } })}>
              Open the film in the lab
            </a>
          </p>
        </section>
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
            {kv('A click in an open field', 'all 377 exposures measured in about 3 s (16 worker processes)')}
          </dl>
        </section>
      </div>
    </>
  );
}

/** The full method is 80 kB of text: loaded when the Method page opens, not with the app. */
function LazyMethodFull() {
  const [C, setC] = useState<null | (() => preact.JSX.Element)>(null);
  useEffect(() => {
    void import('./method/MethodFull').then((m) => setC(() => m.MethodFull));
  }, []);
  return C ? <C /> : <div class="mono-s">Loading the full method…</div>;
}

function MethodPage() {
  return <LazyMethodFull />;
}

function AboutPage() {
  return <AboutBody />;
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
