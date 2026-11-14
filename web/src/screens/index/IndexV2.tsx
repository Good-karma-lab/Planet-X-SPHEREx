import { useEffect, useRef, useState } from 'preact/hooks';
import { ang2pix } from '../../lib/healpix';
import { formatHash } from '../../lib/route';
import { D2R } from '../../lib/sky';
import './index.css';

/** What the main instance reports about a run that reads new files in (all optional: while no run is active the service omits them). */
interface Ingest {
  state?: 'idle' | 'running';
  done?: number;
  total?: number;
  rate_per_min?: number | null;
  eta_s?: number | null;
  retries?: number;
  errors?: number;
  release?: string | null;
  new_files?: number;
}
interface IxHealth {
  role?: string;
  version?: string;
  started_at?: string;
  product_versions?: Record<string, string>;
  index?: { files?: number; bootstrap_at?: string; reconciled_at?: string | null; ingest?: Ingest | null } | null;
  warm?: { done: number; total: number; searches: { done: number; total: number } } | null;
  s3?: { requests?: number; bytes?: number; retries?: number; failures?: number; breaker?: { state?: string } } | null;
  tiles?: { cached?: number } | null;
}
interface Cov {
  order: number;
  releases: string[];
  cells: Record<string, number[]>;
  /** files added by the current run, per cell (only while one is running) */
  cells_new?: Record<string, number[]>;
}

const fmt = (n: number) => n.toLocaleString('en-US');
const dt = (s?: string | null) => (s ? `${s.replace('T', ' ').slice(0, 16)} UTC` : '—');
const REL = [
  { key: 'all', label: 'All', color: '#e6f0f3' },
  { key: '0', label: 'DR1', color: '#5fe3d6' },
  { key: '1', label: 'QR3', color: '#ffb454' },
];
const RAMPS = {
  teal: [[7, 26, 34], [14, 74, 82], [31, 143, 138], [95, 227, 214], [232, 255, 251]],
  amber: [[30, 18, 6], [90, 54, 16], [170, 104, 36], [255, 180, 84], [255, 236, 200]],
};
const eta = (s?: number | null) => (s == null ? '—' : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : `${Math.max(1, Math.round(s / 60))} min`);

/** Main-instance health, polled every 15 s. The standby reports `warm: null`, so the last real value is kept. */
function useHealth(first: IxHealth | null) {
  const [h, setH] = useState<IxHealth | null>(first);
  useEffect(() => {
    let stop = false;
    const load = () =>
      void fetch('/api/v1/health', { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : null))
        .then((n: IxHealth | null) => {
          if (stop || !n) return;
          setH((p) => ({ ...n, warm: n.warm ?? p?.warm ?? null }));
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);
  return h;
}

function useCoverage(running: boolean) {
  const [c, setC] = useState<Cov | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () =>
      void fetch('/api/v1/coverage/map', { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: Cov | null) => !stop && d && setC(d))
        .catch(() => {});
    load();
    const t = running ? setInterval(load, 30000) : undefined;
    return () => {
      stop = true;
      if (t) clearInterval(t);
    };
  }, [running]);
  return c;
}

type Geo = { cx: number; cy: number; rx: number; ry: number };
const invMoll = (g: Geo, px: number, py: number): [number, number] | null => {
  const X = (px - g.cx) / g.rx;
  const Y = (g.cy - py) / g.ry;
  if (X * X + Y * Y > 1) return null;
  const th = Math.asin(Y);
  const dec = Math.asin((2 * th + Math.sin(2 * th)) / Math.PI);
  const lon = (-Math.PI * X) / Math.cos(th);
  return [(((lon / D2R) % 360) + 360) % 360, dec / D2R];
};
const fwdMoll = (g: Geo, ra: number, dec: number): [number, number] => {
  const lon = (-((((ra + 180) % 360) + 360) % 360 - 180) * Math.PI) / 180;
  const phi = dec * D2R;
  let th = phi;
  for (let k = 0; k < 12; k++) th -= (2 * th + Math.sin(2 * th) - Math.PI * Math.sin(phi)) / (2 + 2 * Math.cos(2 * th) || 1e-9);
  return [g.cx + g.rx * (lon / Math.PI) * Math.cos(th), g.cy - g.ry * Math.sin(th)];
};

function SkyMap({ cov, rel, glow, onHover, onPick }: { cov: Cov; rel: string; glow: boolean; onHover: (h: Hov | null) => void; onPick: (ra: number, dec: number) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const geo = useRef<Geo | null>(null);
  const [tick, setTick] = useState(0);
  const cells = glow && cov.cells_new ? cov.cells_new : cov.cells;
  const val = (k: string) => {
    const v = cells[k];
    if (!v) return 0;
    return rel === 'all' || glow ? v.reduce((a, b) => a + b, 0) : (v[+rel] ?? 0);
  };
  useEffect(() => {
    const f = () => setTick((x) => x + 1);
    window.addEventListener('resize', f);
    return () => window.removeEventListener('resize', f);
  }, []);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext('2d')!;
    const dpr = Math.min(2, devicePixelRatio || 1);
    const w = cv.clientWidth;
    const h = cv.clientHeight;
    if (!w || !h) return;
    cv.width = w * dpr;
    cv.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const small = w < 600;
    const pad = small ? 10 : 28;
    const rx = Math.min((w - 2 * pad) / 2, h - 2 * pad - (small ? 30 : 100));
    const g: Geo = { cx: w / 2, cy: h / 2 + (small ? 10 : -34), rx, ry: rx / 2 };
    geo.current = g;
    const ramp = glow ? RAMPS.amber : RAMPS.teal;
    const col = (f: number) => {
      const s = Math.max(0, Math.min(1, f)) * 4;
      const i = Math.min(3, Math.floor(s));
      const t = s - i;
      return ramp[i]!.map((c, k) => Math.round(c + (ramp[i + 1]![k]! - c) * t));
    };
    const vals = Object.keys(cells).map(val).filter((v) => v > 0).sort((a, b) => a - b);
    const lo = Math.log(vals[0] ?? 1);
    const hi = Math.log(vals[vals.length - 1] ?? 2);
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);
    const step = 2;
    const img = ctx.createImageData(Math.ceil(w / step), Math.ceil(h / step));
    for (let j = 0; j < img.height; j++)
      for (let i = 0; i < img.width; i++) {
        const a = invMoll(g, i * step + 1, j * step + 1);
        if (!a) continue;
        const v = val(String(ang2pix(cov.order, a[0], a[1])));
        const c = v > 0 ? col((Math.log(v) - lo) / (hi - lo || 1)) : [20, 24, 30];
        const o = (j * img.width + i) * 4;
        img.data[o] = c[0]!;
        img.data[o + 1] = c[1]!;
        img.data[o + 2] = c[2]!;
        img.data[o + 3] = 255;
      }
    const off = document.createElement('canvas');
    off.width = img.width;
    off.height = img.height;
    off.getContext('2d')!.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, 0, 0, img.width * step, img.height * step);
    ctx.strokeStyle = 'rgba(140,225,240,.35)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.ellipse(g.cx, g.cy, g.rx, g.ry, 0, 0, 2 * Math.PI);
    ctx.stroke();
    const line = (fn: (t: number) => [number, number], c: string) => {
      ctx.strokeStyle = c;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      let prev: [number, number] | null = null;
      for (let t = 0; t <= 360; t++) {
        const [ra, dec] = fn(t);
        const p = fwdMoll(g, ra, dec);
        if (prev && Math.abs(p[0] - prev[0]) > g.rx) ctx.moveTo(p[0], p[1]);
        else if (prev) ctx.lineTo(p[0], p[1]);
        else ctx.moveTo(p[0], p[1]);
        prev = p;
      }
      ctx.stroke();
      ctx.setLineDash([]);
    };
    const E = 23.4393 * D2R;
    line((l) => {
      const L = l * D2R;
      return [Math.atan2(Math.sin(L) * Math.cos(E), Math.cos(L)) / D2R, Math.asin(Math.sin(L) * Math.sin(E)) / D2R];
    }, 'rgba(255,180,84,.85)');
    const M = [[-0.0548755604, 0.4941094279, -0.867666149], [-0.8734370902, -0.44482963, -0.1980763734], [-0.4838350155, 0.7469822445, 0.4559837762]] as const;
    line((l) => {
      const L = l * D2R;
      const gx = Math.cos(L);
      const gy = Math.sin(L);
      return [Math.atan2(M[1][0] * gx + M[1][1] * gy, M[0][0] * gx + M[0][1] * gy) / D2R, Math.asin(M[2][0] * gx + M[2][1] * gy) / D2R];
    }, 'rgba(165,140,240,.85)');
    ctx.font = '500 11px ui-monospace, Menlo, monospace';
    ctx.fillStyle = 'rgba(230,240,243,.6)';
    [0, 6, 18, ...(small ? [] : [12])].forEach((hh) => {
      const p = fwdMoll(g, hh * 15, 0);
      ctx.fillText(`${hh}h`, p[0] + 3, p[1] - 4);
    });
  }, [cov, rel, glow, tick]);
  const at = (e: MouseEvent) => {
    const cv = ref.current;
    if (!cv || !geo.current) return null;
    const r = cv.getBoundingClientRect();
    return invMoll(geo.current, e.clientX - r.left, e.clientY - r.top);
  };
  return (
    <canvas
      ref={ref}
      class="ix-map"
      aria-label="Exposures on the sky; click a place to open it in the Lab"
      onMouseMove={(e) => {
        const a = at(e);
        if (!a) return onHover(null);
        const k = ang2pix(cov.order, a[0], a[1]);
        onHover({ cell: k, ra: a[0], dec: a[1], counts: cov.cells[String(k)] ?? [0, 0] });
      }}
      onMouseLeave={() => onHover(null)}
      onClick={(e) => {
        const a = at(e);
        if (a) onPick(a[0], a[1]);
      }}
    />
  );
}

interface Hov {
  cell: number;
  ra: number;
  dec: number;
  counts: number[];
}

export function IndexV2({ health }: { health: unknown }) {
  const h = useHealth((health as IxHealth | null) ?? null);
  const ing = h?.index?.ingest;
  const running = ing?.state === 'running';
  const cov = useCoverage(running);
  const [rel, setRel] = useState('all');
  const [hov, setHov] = useState<Hov | null>(null);
  const tot = [0, 0];
  if (cov) for (const v of Object.values(cov.cells)) { tot[0]! += v[0] ?? 0; tot[1]! += v[1] ?? 0; }
  const cells = cov ? Object.keys(cov.cells).length : 0;
  const allCells = cov ? 12 * 4 ** cov.order : 768;
  const all = cov ? Object.values(cov.cells).map((v) => v.reduce((a, b) => a + b, 0)).sort((a, b) => a - b) : [];
  const glow = running && !!cov?.cells_new;
  const W = h?.warm;
  const S = h?.s3;
  const mb = (S?.bytes ?? 0) / 1048576;
  const pct = ing?.total ? Math.min(100, ((ing.done ?? 0) / ing.total) * 100) : 0;
  const open = (ra: number, dec: number) => (location.hash = formatHash({ screen: 'lab', params: { ra: ra.toFixed(5), dec: dec.toFixed(5), fov: '20' } }));
  return (
    <div class="ix">
      <header class="ix-top">
        <h1>Exposure index</h1>
        <div class="ix-kpi">
          <b>{h?.index?.files != null ? fmt(h.index.files) : '…'}</b>
          <span>SPHEREx exposures indexed</span>
        </div>
        <div class="ix-kpi" style="--c:#5fe3d6">
          <i />
          <b>{cov ? fmt(tot[0]!) : '…'}</b>
          <span>DR1 · first year</span>
        </div>
        <div class="ix-kpi" style="--c:#ffb454">
          <i />
          <b>{cov ? fmt(tot[1]!) : '…'}</b>
          <span>QR3 · after DR1</span>
        </div>
        <div class="ix-kpi">
          <b>{cov ? `${fmt(cells)} / ${fmt(allCells)}` : '…'}</b>
          <span>sky cells covered</span>
        </div>
        <div class="ix-fresh">{running ? 'ingesting new files from IRSA' : `index built ${dt(h?.index?.bootstrap_at)} from IRSA TAP`}</div>
      </header>
      <div class="ix-body">
        <section class="ix-mapbox" aria-label="Exposures on the sky">
          {cov && <SkyMap cov={cov} rel={rel} glow={glow} onHover={setHov} onPick={open} />}
          {!cov && <div class="ix-wait mono-s">reading the coverage map…</div>}
          <div class="ix-ov ix-seg glass">
            {REL.map((r) => (
              <button key={r.key} class={rel === r.key ? 'on' : ''} style={`--c:${r.color}`} onClick={() => setRel(r.key)}>
                <i />
                {r.label}
              </button>
            ))}
          </div>
          {hov && (
            <div class="ix-ov ix-hover glass">
              <div>
                cell {hov.cell} · RA {hov.ra.toFixed(1)}° Dec {hov.dec >= 0 ? '+' : ''}
                {hov.dec.toFixed(1)}°
              </div>
              <b>{fmt((hov.counts[0] ?? 0) + (hov.counts[1] ?? 0))}</b> exposures
              <div class="r">
                <span style="color:#5fe3d6">DR1 {fmt(hov.counts[0] ?? 0)}</span>
                <span style="color:#ffb454">QR3 {fmt(hov.counts[1] ?? 0)}</span>
              </div>
              <div class="go">Click to open this place in the Lab</div>
            </div>
          )}
          {cov && (
            <div class="ix-ov ix-legend glass">
              <span>{glow ? 'files added by this run per cell, log scale' : 'exposures per sky cell (≈ 54 deg²), log scale'}</span>
              <div class={`ramp${glow ? ' amber' : ''}`} />
              {!glow && all.length > 0 && (
                <div class="sc">
                  <span>{fmt(all[0]!)}</span>
                  <span>median {fmt(all[all.length >> 1]!)}</span>
                  <span>{fmt(all[all.length - 1]!)}</span>
                </div>
              )}
              <div class="ln">
                <span style="--c:#ffb454">ecliptic</span>
                <span style="--c:#a58cf0">Milky Way plane</span>
              </div>
            </div>
          )}
          <div class="ix-ov ix-note">equatorial · RA 0h at centre, east left · hover a cell, click to open it</div>
        </section>
        <aside class="ix-side">
          <div class={`ix-card${running ? ' busy' : ''}`}>
            <h2>
              Ingest <span>{running ? 'running' : 'idle'}</span>
            </h2>
            {running ? (
              <>
                <div class="ix-bh">
                  <b>New files at IRSA</b>
                  <small>{ing?.release ? `release ${ing.release}` : 'a new release or new files in a known one'}</small>
                </div>
                <div class="ix-pbar">
                  <i style={`width:${pct}%`} />
                </div>
                <div class="ix-kv3">
                  <span>indexed</span>
                  <b>{ing?.total ? `${fmt(ing.done ?? 0)} of ${fmt(ing.total)} files` : '—'}</b>
                  <span>rate</span>
                  <b>{ing?.rate_per_min != null ? `${fmt(Math.round(ing.rate_per_min))} files / min` : '—'}</b>
                  <span>time left</span>
                  <b>{eta(ing?.eta_s)}</b>
                  <span>retries · errors</span>
                  <b>
                    {ing?.retries ?? 0} · {ing?.errors ?? 0}
                  </b>
                </div>
                {glow && <p class="ix-cn">The cells receiving files glow on the map.</p>}
              </>
            ) : (
              <div class="ix-calm">
                <span class="dot" />
                <div>
                  <b>Up to date</b>
                  <small>
                    {h?.index?.reconciled_at ? `last check against IRSA ${dt(h.index.reconciled_at)}` : `index built ${dt(h?.index?.bootstrap_at)} from IRSA TAP`}
                  </small>
                </div>
              </div>
            )}
            {!running && <p class="ix-cn">The index is compared with IRSA's tables; when IRSA publishes new SPHEREx files they are read in here, and this card shows the progress.</p>}
          </div>
          <div class="ix-card">
            <h2>
              Reading from the archive <span>since the service started</span>
            </h2>
            <div class="ix-kv">
              <span>Reads from the archive</span>
              <b>{S?.requests != null ? fmt(S.requests) : '—'}</b>
              <span>Data read</span>
              <b>{S?.bytes != null ? (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`) : '—'}</b>
              <span>Retries · failures</span>
              <b>
                {S?.retries ?? 0} · {S?.failures ?? 0}
              </b>
              <span>Archive connection</span>
              <b>{S?.breaker?.state ? (S.breaker.state === 'closed' ? 'healthy' : 'paused') : '—'}</b>
            </div>
          </div>
          <div class="ix-card">
            <h2>Ready now</h2>
            <div class="ix-kv">
              {W ? (
                <>
                  <span>Fields computed ahead</span>
                  <b>
                    {W.done} / {W.total}
                  </b>
                  <div class="bar">
                    <i style={`width:${W.total ? (W.done / W.total) * 100 : 0}%`} />
                  </div>
                  <span>Searches on them</span>
                  <b>
                    {W.searches.done} / {W.searches.total}
                  </b>
                  <div class="bar">
                    <i style={`width:${W.searches.total ? (W.searches.done / W.searches.total) * 100 : 0}%`} />
                  </div>
                </>
              ) : null}
              <span>Survey tiles kept for the globe</span>
              <b>{h?.tiles?.cached != null ? fmt(h.tiles.cached) : '—'}</b>
            </div>
          </div>
        </aside>
      </div>
      <footer class="ix-foot">
        <span>
          service {h?.version ?? '—'} · up since {dt(h?.started_at)}
        </span>
        <span>
          products{' '}
          {Object.entries(h?.product_versions ?? {})
            .map(([k, v]) => `${k} ${v}`)
            .join(' · ')}
        </span>
      </footer>
    </div>
  );
}
