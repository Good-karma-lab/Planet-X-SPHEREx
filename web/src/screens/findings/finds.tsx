import { useEffect, useRef, useState } from 'preact/hooks';
import { formatHash } from '../../lib/route';
import { formatDec, formatRA } from '../../lib/sky';
import { parseSxp, halfToFloat } from '../../lib/sxp';
import { ATLAS_LEAD, LeadPanel } from './leads';
import { FindCard, STAGE_LABEL, submittedCard, type CardFind, type Submitted } from './cards';

export interface Ident {
  catalogue: string;
  label: string;
  state: 'ok' | 'none' | 'unavailable' | 'pending';
  name: string | null;
  sep: number | null;
  type: string | null;
  detail: Record<string, unknown>;
  raw?: unknown;
  note?: string;
}
export interface Find {
  id: string;
  kind: string;
  place: string;
  fov: number;
  ra: number;
  dec: number;
  rate: number | null;
  snr: number | null;
  score: number | null;
  chance: number | null;
  status: 'unexplained' | 'unchecked' | 'known' | 'residual';
  verdict: string;
  idents: Ident[];
  data: Record<string, any>;
  positions: { label: string; mjd: number | null; ra: number | null; dec: number | null; snr: number | null }[];
  spectrum: { lam: number | null; lam_hi: number | null; flux: number | null; err: number | null; model: number | null }[];
  lightcurve: { label: string; mjd: number | null; flux: number | null; err: number | null }[];
  versions: Record<string, string | null>;
  identity?: { name: string; catalogue: string; type: string | null; basis: string };
}
interface Reply {
  total: number;
  counts: Record<string, { all: number; unexplained: number; unchecked: number; known: number; leads?: number }>;
  labels: Record<string, string>;
  identification: { pending: number; unavailable: number; unavailable_decisive?: number; unavailable_extras?: number; catalogues: string[] };
  finds: Find[];
}

const KIND_SHORT: Record<string, string> = { mover: 'Movers', browndwarf: 'Brown dwarfs', red: 'Red sources', anomaly: 'Spectral anomalies', pair: 'Pairs changing', film: 'Time-lapse tracks', peak: 'Planet X peaks' };
const um = (v: number) => v.toFixed(2).replace(/0$/, '');
const mjdDate = (m: number | null) => (m == null ? '' : new Date((m - 40587) * 864e5).toISOString().slice(0, 10));

export function useFinds(): Reply | null {
  const [r, setR] = useState<Reply | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () =>
      void fetch('/api/v1/findings?status=all&limit=2000')
        .then((x) => (x.ok ? x.json() : null))
        .then((d: Reply | null) => {
          if (stop || !d) return;
          // a Planet X peak the service calls a residual (not a lead: ok false) reads as a residual, not as 'not yet checked'
          for (const f of d.finds) if (f.kind === 'peak' && (f.data as { peak?: { ok?: boolean } })?.peak?.ok === false && f.status !== 'known') f.status = 'residual';
          setR(d);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);
  return r;
}

const GREEK: Record<string, string> = { alf: 'α', bet: 'β', gam: 'γ', del: 'δ', eps: 'ε', zet: 'ζ', eta: 'η', tet: 'θ', iot: 'ι', kap: 'κ', lam: 'λ', mu: 'μ', nu: 'ν', ksi: 'ξ', pi: 'π', rho: 'ρ', sig: 'σ', tau: 'τ', phi: 'φ', chi: 'χ', psi: 'ψ', ome: 'ω' };
const CONST: Record<string, string> = { Ind: 'Indi', Dra: 'Draconis', Aur: 'Aurigae', Cen: 'Centauri', Eri: 'Eridani', Oph: 'Ophiuchi' };
/** A SIMBAD name read by people: no leading mark, Greek letters as letters, the constellation in full where it is common. */
export const cleanName = (n: string) =>
  n
    .replace(/^(NAME|\*+)\s+/, '')
    .replace(/^([a-z]{2,3})(?=\s)/, (m) => GREEK[m] ?? m)
    .replace(/\s([A-Z][a-z]{1,2})(?=\s|$)/, (m, c) => (CONST[c] ? ` ${CONST[c]}` : m))
    .replace(/\s+/g, ' ')
    .trim();
/** The name a catalogue gave the find, when one did (SIMBAD first). */
export const knownName = (f: Find) => {
  const sb = f.idents.find((i) => i.catalogue === 'simbad' && i.state === 'ok' && i.name);
  return sb ? cleanName(sb.name!) : '';
};

export function titleOf(f: Find): string {
  const d = f.data;
  switch (f.kind) {
    case 'mover': {
      const nm = knownName(f);
      return nm ? `${nm} · ${(f.rate ?? 0).toFixed(2)}″/yr` : `${(f.rate ?? 0).toFixed(2)}″/yr mover${f.status === 'known' ? ' (a known star)' : ''}`;
    }
    case 'browndwarf':
      return `${d.group === 'known' ? 'Known brown dwarf' : d.group === 'slow' ? 'Slow red mover' : 'Brown dwarf candidate'} · ${(f.rate ?? 0).toFixed(1)}″/yr`;
    case 'red':
      return `Red source · colour ${(f.score ?? 0).toFixed(1)}`;
    case 'anomaly': {
      const top = [...(d.anomaly?.drivers ?? [])].sort((a: any, b: any) => Math.abs(b.z) - Math.abs(a.z))[0];
      return top ? `${top.z > 0 ? 'Excess' : 'Deficit'} at ${um(top.lo)}–${um(top.hi)} µm` : 'Odd spectrum';
    }
    case 'pair':
      return `Pair ${(d.pair?.separation_arcsec ?? 0).toFixed(1)}″ apart · flux change ${(f.score ?? 0).toFixed(1)}σ`;
    case 'film':
      return `${(f.rate ?? 0).toFixed(1)}″/h object moving in a pass${d.less_certain ? ' (less certain)' : ''}`;
    default: {
      const jpl = f.idents.find((i) => i.catalogue === 'sbident' && i.state === 'ok' && i.name);
      return jpl ? `${String(jpl.name).replace(/^\d+ /, '').replace(/ \(.*\)$/, '')} · ${(d.peak?.d ?? 0).toFixed(1)} au (a known body)` : `Planet X peak · ${(d.peak?.d ?? 0).toFixed(0)} au`;
    }
  }
}

// ---- the 2′ picture as a blink of the passes ----
const frames = new Map<string, Promise<{ n: number; g: Uint8ClampedArray } | null>>();
function loadFrame(key: string, pass: number) {
  const id = `${key}:${pass}`;
  let p = frames.get(id);
  if (!p) {
    p = fetch(`/api/v1/fields/${key}/parts/quick_p${pass}`)
      .then(async (r) => {
        if (!r.ok) return null;
        const a = parseSxp(await r.arrayBuffer()).arrays['white'];
        if (!a) return null;
        const n = a.shape[0]!;
        const raw = a.data as Uint16Array;
        const v = new Float32Array(n * n);
        const s: number[] = [];
        for (let i = 0; i < n * n; i++) {
          v[i] = halfToFloat(raw[i]!);
          if (Number.isFinite(v[i]!) && i % 7 === 0) s.push(v[i]!);
        }
        s.sort((x, y) => x - y);
        const lo = s[Math.floor(s.length * 0.01)] ?? 0;
        const hi = s[Math.floor(s.length * 0.998)] ?? 1;
        const k = Math.asin(10) || 1;
        const g = new Uint8ClampedArray(n * n);
        const ash = Math.log(10 + Math.sqrt(101));
        for (let i = 0; i < n * n; i++) {
          const x = Number.isFinite(v[i]!) ? Math.max(0, Math.min(1, (v[i]! - lo) / (hi - lo || 1))) : 0;
          g[i] = Math.round((255 * Math.asinh(10 * x)) / ash);
        }
        void k;
        return { n, g };
      })
      .catch(() => null);
    frames.set(id, p);
  }
  return p;
}

function Blink({ f }: { f: Find }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [st, setSt] = useState<{ ok: boolean; labels: string[]; msg: string }>({ ok: false, labels: [], msg: 'loading…' });
  const [i, setI] = useState(0);
  const crops = useRef<ImageData[]>([]);
  useEffect(() => {
    let stop = false;
    crops.current = [];
    setSt({ ok: false, labels: [], msg: 'loading…' });
    const fp = f.data.field_params as { ra: number; dec: number; fov: number } | undefined;
    if (!fp) {
      setSt({ ok: false, labels: [], msg: 'no picture for this kind of find' });
      return;
    }
    void (async () => {
      const m = await fetch(`/api/v1/fields?ra=${fp.ra}&dec=${fp.dec}&fov=${fp.fov}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      const g = m?.meta?.grid;
      const passes: { index: number; mjd_mid: number }[] = m?.meta?.passes ?? [];
      if (!g || !passes.length || stop) return setSt({ ok: false, labels: [], msg: 'picture not available' });
      const u = (f.ra - g.ra0) * Math.cos((g.dec0 * Math.PI) / 180) * 3600;
      const x0 = g.n / 2 - u / g.step_arcsec;
      const y0 = g.n / 2 - ((f.dec - g.dec0) * 3600) / g.step_arcsec;
      const half = Math.round(60 / g.step_arcsec);
      const out: ImageData[] = [];
      const labels: string[] = [];
      for (const p of passes) {
        const fr = await loadFrame(m.key, p.index);
        if (!fr || stop) continue;
        const w = 2 * half;
        const img = new ImageData(w, w);
        for (let yy = 0; yy < w; yy++)
          for (let xx = 0; xx < w; xx++) {
            const sx = Math.round(x0 - half + xx);
            const sy = Math.round(y0 - half + yy);
            const v = sx >= 0 && sy >= 0 && sx < fr.n && sy < fr.n ? fr.g[sy * fr.n + sx]! : 0;
            const o = (yy * w + xx) * 4;
            img.data[o] = img.data[o + 1] = img.data[o + 2] = v;
            img.data[o + 3] = 255;
          }
        if (!img.data.some((v, k) => k % 4 === 0 && v > 0)) continue; // a pass that did not cover this place is left out
        const vals = Array.from({ length: w * w }, (_, k) => img.data[k * 4]!).sort((a, b) => a - b);
        const lo = vals[Math.floor(vals.length * 0.02)]!;
        const hi = Math.max(lo + 8, vals[Math.floor(vals.length * 0.995)]!);
        for (let k = 0; k < w * w; k++) {
          const t = Math.max(0, Math.min(255, ((img.data[k * 4]! - lo) / (hi - lo)) * 255));
          img.data[k * 4] = img.data[k * 4 + 1] = img.data[k * 4 + 2] = t;
        }
        out.push(img);
        labels.push(mjdDate(p.mjd_mid));
      }
      if (stop) return;
      crops.current = out;
      setSt(out.length ? { ok: true, labels, msg: '' } : { ok: false, labels: [], msg: 'picture not available' });
    })();
    return () => {
      stop = true;
    };
  }, [f.id]);
  useEffect(() => {
    if (!st.ok) return;
    const t = setInterval(() => setI((x) => (x + 1) % crops.current.length), 900);
    return () => clearInterval(t);
  }, [st.ok]);
  useEffect(() => {
    const cv = ref.current;
    const im = crops.current[i % Math.max(1, crops.current.length)];
    if (!cv || !im) return;
    cv.width = im.width;
    cv.height = im.height;
    cv.getContext('2d')!.putImageData(im, 0, 0);
  }, [i, st.ok]);
  return (
    <div class="fblink">
      <div class="fthumb" style="width:240px;height:240px;position:relative">
        <canvas ref={ref} style={{ display: st.ok ? 'block' : 'none', imageRendering: 'auto' }} aria-label="The 2′ around the find in each pass" />
        {!st.ok && <span class="mono-s">{st.msg}</span>}
        {st.ok && <span class="fring" aria-hidden="true" />}
      </div>
      <div class="mono-s">{st.ok ? `2′ · pass ${(i % st.labels.length) + 1} of ${st.labels.length} · ${st.labels[i % st.labels.length]}` : ''}</div>
    </div>
  );
}

// ---- small charts from the stored rows ----
function Chart({ pts, title, xlab }: { pts: { x: number; y: number; e?: number | null; m?: number | null }[]; title: string; xlab: string }) {
  if (!pts.length) return null;
  const W = 420;
  const H = 150;
  const px = 38;
  const py = 14;
  const xs = pts.map((p) => p.x);
  const ys = pts.flatMap((p) => [p.y - (p.e ?? 0), p.y + (p.e ?? 0), ...(p.m != null ? [p.m] : [])]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs) || x0 + 1;
  const y0 = Math.min(0, ...ys);
  const y1 = Math.max(...ys) * 1.08 || 1;
  const X = (v: number) => px + ((v - x0) / (x1 - x0 || 1)) * (W - px - 8);
  const Y = (v: number) => H - py - ((v - y0) / (y1 - y0 || 1)) * (H - 2 * py);
  const mod = pts.filter((p) => p.m != null);
  return (
    <figure class="fchart">
      <figcaption class="mono-s">{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        <line x1={px} y1={Y(y0)} x2={W - 8} y2={Y(y0)} stroke="var(--edge)" />
        <text x={px} y={H - 1} class="ft">{x0.toFixed(2)}</text>
        <text x={W - 8} y={H - 1} class="ft" text-anchor="end">{x1.toFixed(2)} {xlab}</text>
        <text x={px - 4} y={py + 4} class="ft" text-anchor="end">{y1.toPrecision(2)}</text>
        {mod.length > 1 && <polyline fill="none" stroke="var(--amber)" stroke-width="1.4" points={mod.map((p) => `${X(p.x)},${Y(p.m!)}`).join(' ')} />}
        {pts.map((p, i) => (
          <g key={i}>
            {p.e ? <line x1={X(p.x)} x2={X(p.x)} y1={Y(p.y - p.e)} y2={Y(p.y + p.e)} stroke="var(--accent)" opacity="0.6" /> : null}
            <circle cx={X(p.x)} cy={Y(p.y)} r="2.6" fill="var(--accent)" />
          </g>
        ))}
      </svg>
    </figure>
  );
}

function Detail({ f, onClose, inline }: { f: Find; onClose: () => void; inline: boolean }) {
  useEffect(() => {
    if (inline) return;
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [inline, onClose]);
  // the Lab opens the FIELD the searches ran on (its own centre and size: the warm one), with the find's id and place to highlight
  const fp = f.data.field_params as { ra: number; dec: number; fov: number } | undefined;
  const lab = formatHash({
    screen: 'lab',
    params: fp
      ? { ra: fp.ra.toFixed(5), dec: fp.dec.toFixed(5), fov: String(fp.fov), find: f.id, find_ra: f.ra.toFixed(5), find_dec: f.dec.toFixed(5) }
      : { ra: f.ra.toFixed(5), dec: f.dec.toFixed(5), fov: f.kind === 'film' ? '60' : '20', find: f.id },
  });
  const json = () => encodeURIComponent(JSON.stringify({ ...f, note: 'a lead, not a discovery', exported: new Date().toISOString() }, null, 2));
  const spec = f.spectrum.filter((s) => s.lam != null && s.flux != null).map((s) => ({ x: ((s.lam as number) + (s.lam_hi ?? (s.lam as number))) / 2, y: s.flux as number, e: s.err, m: s.model }));
  const curves = [...new Set(f.lightcurve.map((p) => p.label))];
  const nums: [string, string][] = [
    ...(f.rate != null ? ([['Rate', `${f.rate.toFixed(2)}${f.data.rate_err ? ` ± ${Number(f.data.rate_err).toFixed(2)}` : ''} ${f.kind === 'film' ? '″/h' : '″/yr'}${f.data.best_detection ? ` · best of ${f.data.detections.length}` : ''}`]] as [string, string][]) : []),
    ...(f.snr != null ? ([['S/N', f.snr.toFixed(1)]] as [string, string][]) : []),
    ...(f.score != null ? ([['Score', f.score.toFixed(2)]] as [string, string][]) : []),
    ...(f.chance != null ? ([['Chance', f.kind === 'film' ? `controls: ${f.chance.toFixed(2)} per search at ${f.data.track?.n} visits` : `${f.chance.toFixed(2)} per field`]] as [string, string][]) : []),
    [f.kind === 'peak' ? "Place at the search's reference date" : 'Place', `${formatRA(f.ra)} ${formatDec(f.dec)}`],
    ['Field', `${f.place} · ${f.fov}′`],
    ...(((f.data.detections?.length ?? 0) > 1) ? ([['Detections', f.data.detections.map((d: any) => `${d.fov}′${d.rate != null ? ` ${Number(d.rate).toFixed(2)}${d.rate_err ? ` ± ${Number(d.rate_err).toFixed(2)}` : ''}` : ''}${d.sigma != null ? ` (${Number(d.sigma).toFixed(1)}σ)` : ''}`).join(' · ')]] as [string, string][]) : []),
  ];
  return (
    <aside class={`leadpanel glass fdetail${inline ? ' inline' : ''}`} role={inline ? 'region' : 'dialog'} aria-label="Find report">
      {!inline && (
        <button class="leadpanel-x" aria-label="Close" onClick={onClose}>
          ×
        </button>
      )}
      <h3 class="lp-title">{titleOf(f)}</h3>
      <div class="mono-s">
        {f.place} · {(f.kind && KIND_SHORT[f.kind]) ?? f.kind} · <b class={f.status === 'known' ? 'st-known' : f.status === 'unchecked' || f.status === 'residual' ? 'st-wait' : 'st-un'}>{f.status === 'unchecked' ? 'not yet checked' : f.status}</b>
      </div>
      <p class="fverdict">{f.verdict}</p>
      <div class="fmedia">
        <Blink f={f} />
        <div class="fnums">
          {nums.map(([k, v]) => (
            <div key={k}>
              <span>{k}</span>
              <b title={v}>{v}</b>
            </div>
          ))}
        </div>
      </div>
      {spec.length > 0 && <Chart pts={spec} title="Spectrum (binned flux, model in amber)" xlab="µm" />}
      {curves.map((c) => (
        <Chart key={c} pts={f.lightcurve.filter((p) => p.label === c && p.flux != null).map((p, i) => ({ x: p.mjd ?? i, y: p.flux as number, e: p.err }))} title={`Light curve · ${c}`} xlab={f.lightcurve.some((p) => p.mjd != null) ? 'MJD' : 'pass'} />
      ))}
      {f.positions.length > 0 && (
        <table class="ftab">
          <caption class="mono-s">Positions per pass</caption>
          <tbody>
            {f.positions.map((p, i) => (
              <tr key={i}>
                <td>{p.label}</td>
                <td>{mjdDate(p.mjd)}</td>
                <td>{p.ra != null && p.dec != null ? `${p.ra.toFixed(5)} ${p.dec.toFixed(5)}` : ''}</td>
                <td>{p.snr != null ? `S/N ${p.snr.toFixed(0)}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <table class="ftab cat">
        <caption class="mono-s">Catalogues</caption>
        <tbody>
          {f.idents.map((i) => (
            <tr key={i.catalogue} class={`c-${i.state}`}>
              <td>{i.label}</td>
              <td title={i.name ?? ''}>
                {i.state === 'ok' ? `${i.name ?? '—'}${i.type ? ` · ${i.type}` : ''}${i.catalogue === 'sbident' && i.detail.visits_matched ? ` · at ${i.detail.visits_matched} of ${i.detail.visits_checked} visits` : ''}${i.note ? ` · ${i.note}` : ''}` : i.state === 'none' ? (i.catalogue === 'halo' ? 'no bright star within 60″' : 'nothing within 6″') : i.state === 'unavailable' ? 'did not answer' : 'not asked yet'}
              </td>
              <td>{i.state === 'ok' && i.sep != null ? `${i.sep.toFixed(1)}″` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div class="chips">
        <a class="btn sm primary" href={lab}>
          Open in the lab
        </a>
        <a class="btn sm" download={`find-${f.id.replace(/[^a-z0-9]+/gi, '_')}.json`} href={`data:application/json;charset=utf-8,${json()}`}>
          Download JSON
        </a>
      </div>
      <div class="mono-s fver">{Object.entries(f.versions).map(([k, v]) => `${k} ${v}`).join(' · ')}</div>
    </aside>
  );
}

interface CardReply {
  count: number;
  total: number;
  counts: Record<string, { all: number; leads?: number }>;
  stages: Record<string, number>;
  waiting: number;
  waiting_promising: number;
  promising: number;
  promising_kinds: string[];
  identification: { pending: number; unavailable_decisive?: number };
  finds: CardFind[];
}
type KindSel = 'submitted' | 'promising' | 'mover' | 'browndwarf' | 'peak' | 'film' | 'pair' | 'red' | 'anomaly';
type StageSel = 'any' | 'waiting' | 'new' | 'in_review' | 'submitted' | 'candidate' | 'confirmed' | 'known';
const PAGE = 48;
const PROMISING = ['mover', 'browndwarf', 'peak', 'film', 'pair'];

/** The cards of one filter, a page at a time: the next page is asked for when the end of the list comes on screen. */
function useCards(kind: KindSel, stage: StageSel) {
  const [state, setState] = useState<{ key: string; head: CardReply | null; finds: CardFind[]; done: boolean; loading: boolean; failed: boolean }>({ key: '', head: null, finds: [], done: false, loading: false, failed: false });
  const key = `${kind}|${stage}`;
  const busy = useRef(false);
  const load = (reset: boolean) => {
    if (busy.current && !reset) return;
    busy.current = true;
    if (kind === 'submitted') {
      // what people submitted (the accounts of the service): a short list, all at once
      setState({ key, head: state.head, finds: [], done: false, loading: true, failed: false });
      fetch('/api/findings/submitted')
        .then((r) => (r.ok ? (r.json() as Promise<{ findings: Submitted[] }>) : Promise.reject(new Error(String(r.status)))))
        .then((d) => setState((s) => (s.key !== key ? s : { ...s, finds: d.findings.map(submittedCard), done: true, loading: false })))
        .catch(() => setState((s) => (s.key === key ? { ...s, loading: false, failed: true, done: true } : s)))
        .finally(() => {
          busy.current = false;
        });
      return;
    }
    const offset = reset ? 0 : state.finds.length;
    const q = new URLSearchParams({ lite: '1', sort: 'promise', status: 'all', limit: String(PAGE), offset: String(offset) });
    if (kind === 'promising') q.set('kinds', PROMISING.join(','));
    else q.set('kind', kind);
    if (stage === 'waiting') q.set('waiting', '1');
    else if (stage !== 'any') q.set('stage', stage);
    setState((s) => (reset ? { key, head: s.head, finds: [], done: false, loading: true, failed: false } : { ...s, loading: true }));
    fetch(`/api/v1/findings?${q}`)
      .then((r) => (r.ok ? (r.json() as Promise<CardReply>) : Promise.reject(new Error(String(r.status)))))
      .then((d) => {
        setState((s) => {
          if (s.key !== key) return s; // the filter changed while this page was on its way
          const finds = reset ? d.finds : [...s.finds, ...d.finds.filter((f) => !s.finds.some((x) => x.id === f.id))];
          return { key, head: d, finds, done: finds.length >= d.count || d.finds.length === 0, loading: false, failed: false };
        });
      })
      .catch(() => setState((s) => (s.key === key ? { ...s, loading: false, failed: true, done: true } : s)))
      .finally(() => {
        busy.current = false;
      });
  };
  useEffect(() => {
    busy.current = false;
    load(true);
  }, [key]);
  return { ...state, more: () => !state.done && !state.loading && state.key === key && load(false) };
}

/** The full report of one find (catalogue answers, spectrum, positions): asked for when its card is opened. */
function useFind(id: string | null): Find | null {
  const [f, setF] = useState<Find | null>(null);
  useEffect(() => {
    setF(null);
    if (!id || id === ATLAS_LEAD.id) return;
    let stop = false;
    void fetch(`/api/v1/findings/${encodeURIComponent(id)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Find | null) => {
        if (stop || !d) return;
        if (d.kind === 'peak' && (d.data as { peak?: { ok?: boolean } })?.peak?.ok === false && d.status !== 'known') d.status = 'residual';
        setF(d);
      })
      .catch(() => {});
    return () => {
      stop = true;
    };
  }, [id]);
  return f;
}

export function FindingsView({ leadId, setLeadId }: { wide: boolean; leadId: string | null; setLeadId: (id: string | null) => void }) {
  const [kind, setKind] = useState<KindSel>('promising');
  const [stage, setStage] = useState<StageSel>('any');
  const cards = useCards(kind, stage);
  const sel = useFind(leadId);
  // findings submitted by people: the button is there only when the service has accounts and somebody has submitted one
  const [people, setPeople] = useState(0);
  useEffect(() => {
    void fetch('/api/findings/submitted')
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { findings?: unknown[] } | null) => setPeople(d?.findings?.length ?? 0))
      .catch(() => {});
  }, []);
  const end = useRef<HTMLDivElement>(null);
  const more = useRef(cards.more);
  more.current = cards.more;
  useEffect(() => {
    const el = end.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && more.current(), { rootMargin: '300px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [cards.finds.length, cards.done]);
  const h = cards.head;
  const atlas = leadId === ATLAS_LEAD.id;
  const cnt = (k: KindSel) => (!h || k === 'submitted' ? null : k === 'promising' ? h.promising : (h.counts[k]?.all ?? 0));
  const kinds: [KindSel, string][] = [
    ['promising', 'Promising'],
    ['mover', KIND_SHORT.mover!],
    ['browndwarf', KIND_SHORT.browndwarf!],
    ['peak', KIND_SHORT.peak!],
    ['film', KIND_SHORT.film!],
    ['pair', KIND_SHORT.pair!],
  ];
  const more2: [KindSel, string][] = [
    ['red', KIND_SHORT.red!],
    ['anomaly', KIND_SHORT.anomaly!],
  ];
  const stages: StageSel[] = ['any', 'waiting', 'new', 'in_review', 'submitted', 'candidate', 'confirmed', 'known'];
  // the numbers on the status buttons are of the whole list, so they are shown only where they are what the button gives
  const stageTotal = (s: StageSel) => (!h || s === 'any' ? 0 : s === 'waiting' ? h.waiting : (h.stages[s] ?? 0));
  const stageCount = (s: StageSel) => (h && s === 'waiting' && kind === 'promising' ? h.waiting_promising : null);
  const chip = (k: KindSel, label: string) =>
    (cnt(k) ?? 1) > 0 || k === 'promising' || k === kind ? (
      <button key={k} class={kind === k ? 'on' : ''} aria-pressed={kind === k} onClick={() => setKind(k)}>
        {label} <span>{cnt(k) ?? ''}</span>
      </button>
    ) : null;
  return (
    <>
      <section class="card glass fbar">
        <div class="fkinds" role="group" aria-label="Kind">
          {people > 0 && (
            <button class={kind === 'submitted' ? 'on' : ''} aria-pressed={kind === 'submitted'} onClick={() => setKind('submitted')}>
              Found by people <span>{people}</span>
            </button>
          )}
          {kinds.map(([k, l]) => chip(k, l))}
          <span class="fsep" aria-hidden="true" />
          {more2.map(([k, l]) => chip(k, l))}
        </div>
        <div class="fkinds fstat" role="group" aria-label="Status">
          {stages
            .filter((s) => s === 'any' || s === 'waiting' || s === 'new' || s === 'known' || s === stage || stageTotal(s) > 0)
            .map((s) => (
              <button key={s} class={stage === s ? 'on' : ''} aria-pressed={stage === s} onClick={() => setStage(s)}>
                {s === 'any' ? 'Any status' : s === 'waiting' ? 'Waiting for a look' : STAGE_LABEL[s]} {stageCount(s) != null && <span>{stageCount(s)}</span>}
              </button>
            ))}
        </div>
        <p class="hint leadcount">
          {kind === 'submitted' ? `${cards.finds.length} findings submitted by people` : h ? `${cards.finds.length} of ${h.count} shown · ${h.total} finds in the searched fields · a find is a lead, not a discovery` : 'reading the findings…'}
          {h && h.identification.pending > 0 && ` · identifying: ${h.identification.pending} catalogue answers to come`}
        </p>
      </section>
      <div class="fgrid-wrap">
        <ol class="fgrid" aria-busy={cards.loading}>
          {cards.finds.map((f) => (
            <FindCard key={f.id} f={f} current={leadId === f.id} onOpen={setLeadId} />
          ))}
          {kind === 'promising' && (stage === 'any' || stage === 'known') && cards.done && (
            <li class={`fcard glass${atlas ? ' on' : ''}`}>
              <button class="fcard-pic" onClick={() => setLeadId(ATLAS_LEAD.id)} aria-label={`Open the report: ${ATLAS_LEAD.title}`}>
                <div class="fclip">
                  <img src="/neossat/3i-2025-12-14.png" alt="Comet 3I/ATLAS in a NEOSSat frame, ringed at the place JPL predicts" loading="lazy" />
                </div>
                <span class="badge green fcard-stage">Known</span>
              </button>
              <div class="fcard-body">
                <b class="fcard-title">{ATLAS_LEAD.title}</b>
                <span class="mono-s fcard-sub">Comet · {ATLAS_LEAD.sub}</span>
                <button class="btn sm fcard-go" onClick={() => setLeadId(ATLAS_LEAD.id)}>
                  Open the report
                </button>
              </div>
            </li>
          )}
        </ol>
        <div ref={end} class="fend" />
      </div>
      {cards.failed && <p class="mono-s">the findings did not load; the page asks again when the filter is changed</p>}
      {!cards.loading && cards.done && cards.finds.length === 0 && !cards.failed && <p class="mono-s">no finds match these filters</p>}
      {cards.loading && <p class="mono-s">reading the findings…</p>}
      {atlas ? <LeadPanel lead={ATLAS_LEAD} onClose={() => setLeadId(null)} inline={false} /> : leadId && sel ? <Detail f={sel} onClose={() => setLeadId(null)} inline={false} /> : null}
    </>
  );
}
