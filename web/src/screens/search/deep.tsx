import { useEffect, useRef, useState } from 'preact/hooks';
import { about, progressText, runJob, useProduct, type JobView } from './jobs';
import './search.css';

export interface DeepCandidate {
  sn: number;
  phot_sn: number | null;
  d: number;
  ra: number;
  dec: number;
  mu: [number, number];
  flux_mjy?: number;
  err_mjy?: number;
  ok?: boolean;
  alias?: boolean;
  by_pass: { sn: number | null; flux_mjy?: number | null }[];
  control_sn?: number[];
}

export interface DeepResult {
  status: string;
  notes?: string[];
  hypotheses?: number;
  n_peaks?: number;
  n_peaks_negated?: number;
  passes?: number;
  photometry_threshold: number;
  control?: { measured: number; reach_sn5: number };
  candidates?: DeepCandidate[];
  total_secs?: number;
}

interface Plan {
  plan: { hypotheses: number; exposures: number };
  /** the centre of every pass: ra, dec, mjd (a guess; editable) */
  fields: [number, number, number][];
  estimate_s: number;
  limit: number;
}

type Deep =
  | { state: 'idle' }
  | { state: 'planning'; text: string }
  | { state: 'plan'; p: Plan; dmin: number; dmax: number }
  | { state: 'running'; text: string; frac: number | null; estimate_s: number }
  | { state: 'done'; r: DeepResult; sel: number | null }
  | { state: 'none' | 'error'; text: string };

const ut = (m: number) => new Date((m - 40587) * 864e5).toISOString().slice(0, 10);
const deg = (v: number) => v.toFixed(4);

/**
 * The deep sum of the passes: a body too faint in every pass alone can be found by adding the passes along one orbit. The service plans it first (the orbits to try, the
 * exposures to read, the time it will take) and nothing starts until the person says so; then the job runs with its progress and the time left, and the peaks come back.
 * `passes`: the MJD of the middle of every pass (three or more).
 */
export function DeepSumPanel({
  ra,
  dec,
  fov,
  passes,
  onPick,
}: {
  ra: number;
  dec: number;
  fov: number;
  passes: number[];
  /** a peak was chosen: the owner opens its card with these rows */
  onPick?: (title: string, c: DeepCandidate, rows: [string, string, string][]) => void;
}) {
  const has = useProduct('deep');
  const [d, setD] = useState<Deep>({ state: 'idle' });
  const tok = useRef(0);
  const inputs = useRef<[string, string][]>([]);
  useEffect(() => {
    tok.current++;
    setD({ state: 'idle' });
  }, [ra, dec, fov]);
  if (has !== true || passes.length < 3) return null;
  const f = Math.min(fov, 18);
  const plan = async (dmin: number, dmax: number) => {
    const my = ++tok.current;
    setD({ state: 'planning', text: 'Planning: reading the times of the visits of every pass (about a minute)…' });
    try {
      const q = new URLSearchParams({ centre: `${ra.toFixed(5)},${dec.toFixed(5)},${passes[0]}`, passes: passes.join(';'), fov: String(f), dmin: String(dmin), dmax: String(dmax), plan: '1' });
      const r = await fetch(`/api/v1/deep?${q}`, { cache: 'no-store' });
      if (r.status === 404) return setD({ state: 'none', text: 'The service does not make this search yet.' });
      const j = (await r.json()) as { status?: string; error?: string; plan?: Plan['plan']; fields?: Plan['fields']; estimate_s?: number; limit?: number };
      if (my !== tok.current) return;
      if (j.status !== 'plan' || !j.plan || !j.fields) return setD({ state: 'none', text: j.error || 'No plan for this field.' });
      inputs.current = j.fields.map((x) => [deg(x[0]), deg(x[1])]);
      setD({ state: 'plan', p: { plan: j.plan, fields: j.fields, estimate_s: j.estimate_s ?? 0, limit: j.limit ?? 0 }, dmin, dmax });
    } catch (e) {
      if (my === tok.current) setD({ state: 'error', text: (e as Error).message });
    }
  };
  const run = async (dmin: number, dmax: number, est: number, fields: string) => {
    const my = ++tok.current;
    const t0 = Date.now();
    setD({ state: 'running', text: 'Starting…', frac: null, estimate_s: est });
    try {
      const q = new URLSearchParams({ fields, fov: String(f), dmin: String(dmin), dmax: String(dmax) });
      const j = await runJob(`/api/v1/deep?${q}`, {
        cancelled: () => my !== tok.current,
        every: 3000,
        max: 4000,
        onJob: (jj: JobView) => {
          const p = progressText({ ...jj, estimate_s: jj.estimate_s ?? est }, t0);
          if (my === tok.current) setD({ state: 'running', text: p.text, frac: p.frac, estimate_s: est });
        },
      });
      if (my !== tok.current) return;
      if (!j) return setD({ state: 'none', text: 'The service does not make this search yet.' });
      const r = j.result as unknown as DeepResult;
      setD(r?.status === 'ok' ? { state: 'done', r: { ...r, total_secs: r.total_secs ?? (Date.now() - t0) / 1000 }, sel: null } : { state: 'none', text: (r?.notes ?? []).join(' ') || 'No result.' });
    } catch (e) {
      if ((e as Error).message !== 'cancelled') setD({ state: 'error', text: (e as Error).message });
    }
  };
  const close = () => {
    tok.current++;
    setD({ state: 'idle' });
  };
  if (d.state === 'idle')
    return (
      <div class="dsbox">
        <div class="srrow" title="A body too faint in every pass alone can be found by adding the passes along one orbit. Reads every exposure of every pass and tries about a million orbits; the estimate of the time is shown before anything starts.">
          <span class="srt">Find Planet X · deep sum of the passes</span>
          <button class="btn sm" onClick={() => void plan(60, 110)}>
            Plan
          </button>
        </div>
      </div>
    );
  return (
    <div class="dsbox">
      <div class="search-h">Find Planet X · deep sum of the passes</div>
      {d.state === 'planning' && <div class="mono-s">{d.text}</div>}
      {(d.state === 'none' || d.state === 'error') && (
        <>
          <div class="mono-s">{d.state === 'none' ? d.text : `The deep sum failed: ${d.text}`}</div>
          <button class="btn sm" onClick={close}>
            Close
          </button>
        </>
      )}
      {d.state === 'plan' && (
        <PlanForm d={d} passes={passes} inputs={inputs} onStart={(fields) => void run(d.dmin, d.dmax, d.p.estimate_s, fields)} onCancel={close} />
      )}
      {d.state === 'running' && (
        <>
          <div class="mono-s">{d.text}</div>
          <div class={`ds-prog${d.frac == null ? ' indet' : ''}`}>
            <i style={d.frac == null ? '' : `width:${Math.round(d.frac * 100)}%`} />
          </div>
          <div class="mono-s">started with the estimate {about(d.estimate_s)}</div>
          <button class="btn sm" onClick={close}>
            Cancel
          </button>
        </>
      )}
      {d.state === 'done' && <DeepResultView d={d} onSel={(sel) => setD({ ...d, sel })} onPick={onPick} onClose={close} />}
    </div>
  );
}

function PlanForm({
  d,
  passes,
  inputs,
  onStart,
  onCancel,
}: {
  d: Extract<Deep, { state: 'plan' }>;
  passes: number[];
  inputs: { current: [string, string][] };
  onStart: (fields: string) => void;
  onCancel: () => void;
}) {
  const [, force] = useState(0);
  const over = d.p.plan.hypotheses > d.p.limit;
  const fields = () => inputs.current.map((x, i) => `${(+x[0]).toFixed(5)},${(+x[1]).toFixed(5)},${d.p.fields[i]![2]}`).join(';');
  return (
    <>
      <div class="mono-s stl" title="The centres of the passes are a guess (a circular prograde orbit at the middle distance); a body whose own motion is larger or smaller than that can leave the 18′ field. Change them if you know better.">
        distance {d.dmin}–{d.dmax} au · centres guessed, editable
      </div>
      {d.p.fields.map((f, i) => (
        <div class="mono-s" key={i}>
          pass {i + 1} · {ut(f[2] ?? passes[i]!)} · centre{' '}
          {[0, 1].map((k) => (
            <input
              key={k}
              type="text"
              class="ds-in"
              value={inputs.current[i]![k]}
              onInput={(e) => {
                inputs.current[i]![k] = (e.currentTarget as HTMLInputElement).value;
                force((x) => x + 1);
              }}
            />
          ))}
        </div>
      ))}
      <div class="mono-s">
        {d.p.plan.hypotheses.toLocaleString('en')} orbits · {d.p.plan.exposures} exposures to read · {over ? 'too many: narrow the distance range' : about(d.p.estimate_s)}
      </div>
      <div class="chips">
        <button class="btn sm" disabled={over} onClick={() => onStart(fields())}>
          {over ? 'Narrow the range first' : `Start the deep sum (${about(d.p.estimate_s)})`}
        </button>
        <button class="btn sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </>
  );
}

function DeepResultView({
  d,
  onSel,
  onPick,
  onClose,
}: {
  d: Extract<Deep, { state: 'done' }>;
  onSel: (i: number | null) => void;
  onPick?: (title: string, c: DeepCandidate, rows: [string, string, string][]) => void;
  onClose: () => void;
}) {
  const r = d.r;
  const C = r.candidates ?? [];
  const L = (t: string) => <div class="mono-s stl">{t}</div>;
  return (
    <>
      {L(`done in ${about(r.total_secs ?? 0).replace('about ', '')} · ${r.hypotheses ? r.hypotheses.toLocaleString('en') : '?'} orbits`)}
      {L(`${r.n_peaks ?? 0} peaks measured · ${r.n_peaks_negated ?? 0} of the negated sum`)}
      {L(`bar: S/N ${r.photometry_threshold} · control tracks: ${r.control ? `${r.control.reach_sn5} of ${r.control.measured} reach S/N 5` : 'n/a'}`)}
      {L('blind limit about V 20–21 · reflected light')}
      {L('Planet Nine: margin 1.4–1.7× only in D4 (3 µm)')}
      {!C.length && L('nothing above the bar')}
      {C.map((c, i) => {
        if (!c.ok && !c.alias && !((c.phot_sn ?? 0) >= r.photometry_threshold)) return null;
        return (
          <div key={i}>
            <button
              class={`btn sm leadbtn${d.sel === i ? ' on' : ''}`}
              onClick={() => {
                onSel(d.sel === i ? null : i);
                if (d.sel !== i) onPick?.(`Deep sum · peak ${i + 1}`, c, peakRows(c, r));
              }}
            >
              <b>Peak {i + 1}</b> · sum S/N {c.sn} · photometry {c.phot_sn != null ? `${c.phot_sn}σ` : 'n/a'} · {c.d} au{c.ok ? ' · above the threshold' : ''}
            </button>
            {d.sel === i && (
              <div class="mono-s" style="margin:2px 0 8px 8px">
                {c.ra.toFixed(4)} {c.dec.toFixed(4)} (middle pass) · own motion {c.mu[0]}, {c.mu[1]} ″/day{c.flux_mjy != null ? ` · ${c.flux_mjy} ± ${c.err_mjy} mJy` : ''}
                <br />
                {c.by_pass.map((q, k) => `pass ${k + 1}: ${q.sn != null ? `${q.sn}σ` : 'n/a'}${q.flux_mjy != null ? ` (${q.flux_mjy} mJy)` : ''}`).join(' · ')}
                <br />
                control tracks: {c.control_sn?.length ? `${c.control_sn.join(', ')}σ` : 'n/a'}
              </div>
            )}
          </div>
        );
      })}
      <button class="btn sm" onClick={onClose}>
        Close
      </button>
    </>
  );
}

/** The rows of the card of a deep-sum peak. */
export function peakRows(c: DeepCandidate, r: DeepResult): [string, string, string][] {
  return [
    ['Signal', `${c.sn} in the sum · ${c.phot_sn != null ? `${c.phot_sn} σ` : 'n/a'} in photometry`, 'the sum over the passes along one orbit, and PSF photometry along it'],
    ['Bar and noise', `bar ${r.photometry_threshold} · from the negated sum`, 'the highest peak of the negated sum'],
    ['Control tracks', c.control_sn?.length ? `${c.control_sn.join(', ')} σ` : 'n/a', 'tracks beside the peak'],
    ['Distance', `${c.d} au`, 'of the best orbit'],
    ['Own motion', `${c.mu[0]}, ${c.mu[1]} ″/day`, 'carried to every pass'],
    ['By pass', c.by_pass.map((q, k) => `${k + 1}: ${q.sn != null ? `${q.sn}σ` : 'n/a'}`).join(' · '), 'photometry in each pass'],
    ['Reach', 'a blind search reaches about V 20–21; Planet Nine margin 1.4–1.7× only in D4', 'in reflected light'],
  ];
}
