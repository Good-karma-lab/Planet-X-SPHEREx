import { useEffect, useMemo, useState } from 'preact/hooks';
import { honestLines, useProduct } from '../search/jobs';
import { useSearched, type SearchedField } from '../search/searched';
import { DEPTH, FIELDS, P9, PROOF, RECORDED, type Field } from './data';
import { Model } from './model';
import './planetx.css';

interface Plan {
  exposures: number;
  visits: number;
  hypotheses: number;
  estimate_s: number;
}

const fmtDec = (d: number) => `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(3)}`;
const mins = (s: number) => (s < 90 ? 'about a minute' : s < 5400 ? `about ${Math.round(s / 60)} minutes` : `about ${(s / 3600).toFixed(1)} hours`);
const isFpx = (f: SearchedField) => f.kinds.includes('distant') || f.kinds.includes('deep');

function usePlan(ra: number, dec: number, size: number, dmin: number, dmax: number, ok: boolean): Plan | null {
  const has = useProduct('distant');
  const [plan, setPlan] = useState<Plan | null>(null);
  useEffect(() => {
    setPlan(null);
    if (has !== true || !ok) return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      const q = new URLSearchParams({ ra: String(ra), dec: String(dec), fov: String(size >= 20 ? 18 : size), dmin: String(dmin), dmax: String(dmax), plan: '1' });
      fetch(`/api/v1/distant?${q}`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: (Plan & { status?: string }) | null) => d && d.status === 'plan' && setPlan(d))
        .catch(() => {});
    }, 400);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [has, ra, dec, size, dmin, dmax, ok]);
  return plan;
}

function useHonest(): string[] {
  const [h, setH] = useState<string[]>([]);
  useEffect(() => {
    fetch('/api/v1/planetx/honest')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setH(honestLines(d).map((l) => l.replace(/\s*This job did not use D4\./, ''))))
      .catch(() => {});
  }, []);
  return h;
}

const LIGHT: { id: string; name: string; hint: string }[] = [
  { id: 'D4', name: 'D4', hint: '2.4–3.8 µm: the band where SPHEREx goes deeper than WISE' },
  { id: 'D1-D3', name: 'D1–D3', hint: '0.75–2.4 µm: reflected light' },
  { id: 'D1-D4', name: 'D1–D4', hint: 'all four short bands' },
];

export function PlanetXPage() {
  const searched = useSearched(true);
  const honest = useHonest();
  const [hover, setHover] = useState<string | null>(null);
  const [fid, setFid] = useState<string>('ecl');
  const f0 = FIELDS[0]!;
  const [ra, setRa] = useState(String(f0.ra));
  const [dec, setDec] = useState(String(f0.dec));
  const [size, setSize] = useState(String(f0.size));
  const [far, setFar] = useState(true);
  const [light, setLight] = useState('D4');
  const [prior, setPrior] = useState(true);
  const pick = (f: Field) => {
    setFid(f.id);
    setRa(String(f.ra));
    setDec(String(f.dec));
    setSize(String(f.size));
    setFar(f.dmin >= 200);
  };
  const raN = Number(ra), decN = Number(dec), sizeN = Number(size);
  const valid = Number.isFinite(raN) && raN >= 0 && raN < 360 && Number.isFinite(decN) && Math.abs(decN) <= 90 && Number.isFinite(sizeN) && sizeN >= 1 && sizeN <= 120;
  const [dmin, dmax] = far ? [250, 2000] : [30, 300];
  const plan = usePlan(raN, decN, sizeN, dmin, dmax, valid);
  const href = useMemo(() => {
    const q = new URLSearchParams({ ra: raN.toFixed(4), dec: decN.toFixed(4), fov: String(sizeN > 20 ? 60 : sizeN > 10 ? 20 : sizeN > 5 ? 10 : sizeN > 2 ? 5 : 2), fpx: '1', dmin: String(dmin), dmax: String(dmax), det: light });
    if (prior) q.set('prior', '1');
    return `#/lab?${q}`;
  }, [raN, decN, sizeN, dmin, dmax, light, prior]);

  const mine = (searched ?? []).filter(isFpx);
  const area = mine.reduce((s, f) => s + (f.fov / 60) ** 2, 0);
  const peaks = mine.filter((f) => f.best && f.best.kind === 'peak');

  return (
    <div class="screen">
      <div class="ipage px-page">
        <header class="pg-h">
          <h1>Planet X</h1>
          <p class="hint">Where a ninth planet could be, and what this site has searched for it.</p>
        </header>
        <div class="fsum" data-testid="px-stats">
          <div>
            <b>{searched == null ? '—' : mine.length}</b>
            <span>fields searched for distant bodies</span>
          </div>
          <div>
            <b>{searched == null ? '—' : area < 0.1 ? area.toFixed(2) : area.toFixed(1)}</b>
            <span>square degrees</span>
          </div>
          <div>
            <b>0.039–0.047</b>
            <span>mJy blind limit in D4 (WISE: 0.065)</span>
          </div>
          <div>
            <b>{PROOF.length}</b>
            <span>known worlds found blind</span>
          </div>
          <div>
            <b>0</b>
            <span>confirmed Planet Nine candidates</span>
          </div>
        </div>
        <div class="px-grid">
          <section class="card glass px-left">
            <h3>THE SOLAR SYSTEM</h3>
            <Model onHover={setHover} />
            <div class="px-status" data-testid="px-status">{hover ?? 'Drag to turn, scroll to zoom. Orbits are JPL elements; positions are for today.'}</div>
            <div class="px-p9">
              <b>Planet Nine, predicted</b>
              <span>semi-major axis {P9.a} au ({P9.aRange[0]}–{P9.aRange[1]}) · perihelion {P9.peri} au · inclination {P9.i}° · {P9.mass}</span>
              <span class="hint">Not discovered. Its position on the orbit is unknown, so the search covers every direction a field can look.</span>
            </div>
          </section>
          <section class="card glass px-right">
            <h3>FIND PLANET X ON A FIELD</h3>
            <div class="px-presets" role="group" aria-label="Field presets">
              {FIELDS.map((f) => (
                <button key={f.id} class={`chip ${fid === f.id ? 'on' : ''}`} onClick={() => pick(f)} title={f.why}>
                  {f.name}
                </button>
              ))}
            </div>
            <div class="px-form">
              <label>RA°<input value={ra} onInput={(e) => { setFid(''); setRa((e.target as HTMLInputElement).value); }} inputMode="decimal" data-testid="px-ra" /></label>
              <label>Dec°<input value={dec} onInput={(e) => { setFid(''); setDec((e.target as HTMLInputElement).value); }} inputMode="decimal" data-testid="px-dec" /></label>
              <label>Size ′<input value={size} onInput={(e) => { setFid(''); setSize((e.target as HTMLInputElement).value); }} inputMode="decimal" data-testid="px-size" /></label>
            </div>
            <div class="px-row">
              <span class="px-k">Distance</span>
              <span class="px-seg">
                <button class={!far ? 'on' : ''} onClick={() => setFar(false)}>30–300 au</button>
                <button class={far ? 'on' : ''} onClick={() => setFar(true)}>250–2000 au</button>
              </span>
            </div>
            <div class="px-row">
              <span class="px-k">Light</span>
              <span class="px-seg">
                {LIGHT.map((l) => (
                  <button key={l.id} class={light === l.id ? 'on' : ''} onClick={() => setLight(l.id)} title={l.hint}>{l.name}</button>
                ))}
              </span>
            </div>
            <label class="px-row px-check">
              <input type="checkbox" checked={prior} onChange={(e) => setPrior((e.target as HTMLInputElement).checked)} />
              <span>Planet Nine orbits only (the prediction as a prior)</span>
            </label>
            <div class="px-plan" data-testid="px-plan">
              {!valid ? 'Enter RA 0–360°, Dec −90…+90° and a size of 1–120′.' : plan ? `reads ${plan.exposures} exposures in ${plan.visits} visits · ${mins(plan.estimate_s)}` : 'planning…'}
            </div>
            <a class={`btn primary wide ${valid ? '' : 'disabled'}`} href={valid ? href : undefined} aria-disabled={!valid} data-testid="px-run">
              Run Find Planet X in the Lab
            </a>
            <h3 class="px-h3">CANDIDATES</h3>
            <ul class="px-cands" data-testid="px-cands">
              <li class="rej">
                <div class="px-ct"><b>{RECORDED['ecl']!.title}</b><em class="px-v bad">{RECORDED['ecl']!.verdict}</em></div>
                <p>{RECORDED['ecl']!.text}</p>
                <table class="px-rows">
                  <tbody>
                    {RECORDED['ecl']!.rows!.map((r) => (
                      <tr key={r[0]}><td>{r[0]}</td><td>{r[1]}</td></tr>
                    ))}
                  </tbody>
                </table>
              </li>
              {peaks.map((f) => (
                <li key={f.key}>
                  <div class="px-ct">
                    <b>{f.best!.label}{f.best!.sn != null ? ` · ${f.best!.sn.toFixed(1)}σ` : ''}</b>
                    <em class="px-v">lead</em>
                  </div>
                  <p>RA {f.ra.toFixed(3)}, Dec {fmtDec(f.dec)} · {f.fov}′ field · deep sum of the passes. A peak is a lead: it needs a closed orbit across the passes and a check against NEOWISE before it counts.</p>
                  <a class="px-open" href={`#/lab?ra=${f.open.ra}&dec=${f.open.dec}&fov=${f.open.fov}`}>Open in the Lab</a>
                </li>
              ))}
              {searched != null && peaks.length === 0 && <li class="px-none">No live peaks above the bar right now.</li>}
            </ul>
            <div class="px-honest">
              {honest.map((l, k) => (
                <p key={k} class="hint">{l}</p>
              ))}
            </div>
          </section>
        </div>
        <section class="card glass px-bottom">
          <div class="px-cols">
            <div>
              <h3>DEPTH AGAINST WISE</h3>
              <div class="px-bars" data-testid="px-depth">
                {DEPTH.map((d) => (
                  <div key={d.name} class="px-bar">
                    <span class="px-bn">{d.name}<em>{d.note}</em></span>
                    <span class="px-bt"><i style={{ width: `${(d.mjy / 0.07) * 100}%`, background: d.colour }} /></span>
                    <b>{d.mjy.toFixed(3)}</b>
                  </div>
                ))}
              </div>
              <p class="hint">Faintest body found blind, mJy at 3 µm, measured on artificial bodies in 1° fields at 250–2000 au. Lower is deeper.</p>
            </div>
            <div>
              <h3>KNOWN WORLDS FOUND BLIND</h3>
              <ul class="px-proof" data-testid="px-proof">
                {PROOF.map((p) => (
                  <li key={p.who}><b>{p.who}</b><span>{p.what}</span><em>{p.got}</em></li>
                ))}
              </ul>
              <p class="hint">{RECORDED['sedna']!.text}</p>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
