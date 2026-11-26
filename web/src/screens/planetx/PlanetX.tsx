import { visibleInterval } from '../../lib/idle';
import { useEffect, useRef, useState } from 'preact/hooks';
import { honestLines } from '../search/jobs';
import { useSearched, type SearchedField } from '../search/searched';
import { P9, RECORDED } from './data';
import { atKnown, echoOf } from './known';
import type { Group, Layers, PxField, PxScene, View } from './scene';
import './px3.css';

// Planet X, v3 (approved design): the model fills the screen; one panel with Candidates · Searched · Story. No forms and no settings:
// Find Planet X runs by itself on every field opened in the Lab and on the fields we search; this page shows what came out.

/** Known distant bodies found blind by the live service, then linked across passes (spherex.space production jobs, 2026-11-15:
 *  one-pass distance jobs on the body's place in each pass, then the link job over their keys). Nothing precomputed. */
const KNOWN: { who: string; d: string; jpl: string; how: string; ok: boolean; lab: string }[] = [
  {
    who: 'Eris',
    d: '95.6 ± 0.4 au',
    jpl: 'JPL 95.5',
    how: '3 passes, 18′ fields · signal 11.9–14.6× the noise in each · one orbit fits all three to 1.6″',
    ok: true,
    lab: '#/lab?ra=27.378&dec=-0.120&fov=20',
  },
  {
    who: 'Sedna',
    d: '83.0 au',
    jpl: 'JPL 83.0',
    how: '3 passes, 18′ field, 75–92 au · 6.0 million orbits tried · the sum is 8.2× the noise: just over the highest noise peak (8.07), under the 8.48 it needs · brightness 7.5× the noise · 0.5–0.8″ from JPL in every pass',
    ok: false,
    lab: '#/lab?ra=61.7725&dec=8.8408&fov=20',
  },
  {
    who: 'Makemake',
    d: '52.5 ± 0.2 au',
    jpl: 'JPL 52.3',
    how: '2 passes · signal 14.6–16.0× the noise in each · fits to 1.5″ · a third pass would confirm it',
    ok: false,
    lab: '#/lab?ra=202.317&dec=20.157&fov=20',
  },
];

const isFpx = (f: SearchedField) => f.kinds.includes('distant') || f.kinds.includes('deep');

/** names for the places people know; any other field is named by its coordinates */
const PLACES: [string, number, number][] = [
  ["Barnard's Star", 269.448, 4.769],
  ['ε Indi B', 331.077, -56.794],
  ['WISE 0855−0714', 133.76, -7.242],
  ['IRAS/AKARI field', 34.72, -49.79],
  ['Ecliptic D4 field', 60.712, 8.617],
  ['394 au candidate place', 60.94, 8.39],
  ['Sedna', 61.773, 8.841],
  ['Eris', 27.378, -0.12],
  ['Eris', 26.325, -0.431],
  ['Eris', 27.503, 0.149],
  ['Makemake', 199.876, 21.43],
  ['Makemake', 202.317, 20.157],
  ['RW Aurigae', 76.957, 30.401],
  ['R Draconis', 248.168, 66.755],
];
const sep = (a: [number, number], b: [number, number]) => {
  const r = Math.PI / 180;
  return (
    Math.acos(
      Math.min(1, Math.sin(a[1] * r) * Math.sin(b[1] * r) + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.cos((a[0] - b[0]) * r)),
    ) / r
  );
};
const fmtDec = (d: number) => `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(2)}`;
function nameOf(f: SearchedField): string {
  const hit = PLACES.find(([, ra, dec]) => sep([ra, dec], [f.ra, f.dec]) < 0.15);
  return hit ? hit[0] : `RA ${f.ra.toFixed(2)} · Dec ${fmtDec(f.dec)}`;
}
/** the lab's field sizes; a link uses the smallest that holds the searched field */
const SIZES = [2, 5, 10, 20, 60];
const snap = (fov: number) => SIZES.find((s) => s >= fov - 0.5) ?? 60;
const labHref = (f: SearchedField) => `#/lab?ra=${f.open.ra}&dec=${f.open.dec}&fov=${snap(f.open.fov)}`;

/** One Find Planet X peak, as Findings lists it (GET /api/v1/findings, kind peak). A LEAD is a peak above its field's bar that is not a
 *  known body; the same definition the Findings page and the Lab use. */
interface Known {
  state?: string;
  name?: string | null;
  words?: string;
  seps?: number[];
}
interface Peak {
  id: string;
  ra: number;
  dec: number;
  fov: number;
  place: string;
  snr: number | null;
  status?: string;
  epoch?: number;
  data: {
    peak?: {
      stamp?: string;
      d?: number;
      sn?: number;
      phot_sn?: number;
      ok?: boolean;
      edge?: boolean;
      d_lo?: number;
      known?: Known | null;
      by_pass?: { ra?: number; dec?: number; sn?: number }[] | null;
    };
  };
}
/** a peak JPL names (data.peak.known from the service, or status 'known'), one at a live known-body place, or a known body's echo */
const isKnown = (f: Peak) =>
  f.status === 'known' ||
  f.data.peak?.known?.state === 'ok' ||
  atKnown(f.ra, f.dec) ||
  echoOf(f) != null;
/** `faint`: with the faint peaks (far, detectors 1–3, under 6 sigma: the service hides them unless asked, as Findings does) */
function usePeaks(faint: boolean): { leads: Peak[]; known: Peak[]; faint: number } | null {
  const [p, setP] = useState<{ leads: Peak[]; known: Peak[]; faint: number } | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () =>
      fetch(`/api/v1/findings?status=all&limit=2000&kind=peak${faint ? '' : '&faint=hide'}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { finds?: (Peak & { kind: string })[]; faint?: number } | null) => {
          if (stop || !d?.finds) return;
          // the Findings rule: one peak with ok=true per object; known bodies are not leads (status 'known' from the service; until the service
          // marks the live Eris and Makemake detections, their places are matched here)
          // statuses the service keeps out of the leads (a star's residual or halo, a galaxy, below the bar) are not leads here either
          const ok = d.finds.filter(
            (f) => f.kind === 'peak' && f.data.peak?.ok !== false && !['residual', 'galaxy', 'below', 'halo'].includes(f.status ?? ''),
          );
          setP({ leads: ok.filter((f) => !isKnown(f)), known: ok.filter(isKnown), faint: d.faint ?? 0 });
        })
        .catch(() => {});
    void load();
    const off = visibleInterval(load, 60000); // a hidden tab asks nothing; seen again, it asks at once
    return () => {
      stop = true;
      off();
    };
  }, [faint]);
  return p;
}
const hhmm = (s?: string) => (s ? `${s.slice(0, 10)} ${s.slice(11, 16)} UTC` : '');

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

const CHIPS: { g: Group; name: string; colour: string }[] = [
  { g: 'planets', name: 'Planets', colour: '#cfd8e0' },
  { g: 'dwarfs', name: 'Dwarf planets', colour: '#6fdc8c' },
  { g: 'etnos', name: 'Clustered distant objects', colour: '#7dd3fc' },
  { g: 'p9', name: 'Planet Nine, predicted', colour: '#ff9b5c' },
  { g: 'fields', name: 'Where we searched', colour: '#5fe3d6' },
];

interface Chapter {
  t: string;
  sub: string;
  view: View;
  show: Layers;
  cap: [string, string];
}
const ALL: Layers = { planets: true, dwarfs: true, etnos: true, p9: true, fields: true, peri: false };
const CH: Chapter[] = [
  {
    t: 'Why astronomers predict it',
    sub: 'the farthest bodies point the same way',
    view: 'cluster',
    show: { planets: true, dwarfs: false, etnos: true, p9: false, fields: false, peri: true },
    cap: ['#7dd3fc', 'The clustered bodies: their closest points lie on one side'],
  },
  {
    t: 'What it would be',
    sub: 'a super-Earth hundreds of au out',
    view: 'p9',
    show: { planets: true, dwarfs: false, etnos: false, p9: true, fields: false, peri: false },
    cap: ['#ff9b5c', 'Planet Nine: the published range, the most likely orbit bright'],
  },
  {
    t: 'Why it is so hard to find',
    sub: 'faint, slow and somewhere in a long band',
    view: 'band',
    show: { planets: true, dwarfs: false, etnos: false, p9: true, fields: false, peri: false },
    cap: ['#ff9b5c', 'Seen almost edge-on: a long, thin band, tilted 16° to the planets'],
  },
  {
    t: 'Why SPHEREx helps',
    sub: 'the whole sky, twice a year, at 0.75–5 µm',
    view: 'spherex',
    show: { planets: true, dwarfs: true, etnos: false, p9: true, fields: true, peri: false },
    cap: ['#5fe3d6', 'Each field is searched along its line of sight, 30–2000 au'],
  },
  {
    t: 'What we searched and found',
    sub: 'no Planet Nine yet',
    view: 'search',
    show: ALL,
    cap: ['#5fe3d6', 'Where we searched, against where Planet Nine may be'],
  },
];

export function PlanetXPage() {
  const searched = useSearched(true);
  const honest = useHonest();
  const [faint, setFaint] = useState(false);
  const pk = usePeaks(faint);
  const leads = pk?.leads ?? null;
  const knownPeaks = pk?.known ?? [];
  // known bodies' echoes the service did not name (their strongest pass sits on the body)
  const echoes = knownPeaks.filter((f) => !f.data.peak?.known?.name && f.status !== 'known').map(echoOf).filter((x): x is string => !!x);
  const [tab, setTab] = useState<'cands' | 'searched' | 'story'>('cands');
  const [chapter, setChapter] = useState(-1);
  const [layers, setLayersState] = useState<Layers>(ALL);
  const [log, setLog] = useState(true);
  const [ready, setReady] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const tags = useRef<HTMLDivElement>(null);
  const model = useRef<HTMLElement>(null);
  const scene = useRef<PxScene | null>(null);

  const mine = (searched ?? []).filter(isFpx).sort((a, b) => (b.searched_at ?? '').localeCompare(a.searched_at ?? ''));
  /** the warm field a peak lies in (the lab opens it ready), else the peak's own place */
  /** a lead opens a 5′ field centred on the lead's own place at its reference date (quick to load), the lead named so the Lab marks it */
  const fieldOf = (pk: Peak) =>
    `#/lab?ra=${pk.ra.toFixed(5)}&dec=${pk.dec.toFixed(5)}&fov=5&find=${encodeURIComponent(pk.id)}&find_ra=${pk.ra.toFixed(5)}&find_dec=${pk.dec.toFixed(5)}`;
  /** the searched field the lead lies in, at its own size (20′ or 60′) */
  const searchedOf = (pk: Peak) => (searched ?? []).find((x) => sep([x.ra, x.dec], [pk.ra, pk.dec]) < x.fov / 120);
  const nLeads = leads?.length ?? 0;
  const card = (pk: Peak) => (
    <div key={pk.id} class="px3-cand lead">
      <div class="t">
        <b>
          {pk.data.peak?.known?.state === 'ok' && pk.data.peak.known.name
            ? `${pk.data.peak.known.name
                .split('(')[0]!
                .trim()
                .replace(/^\d+\s+/, '')} (JPL), found blind · ${(pk.data.peak.d ?? 0).toFixed(1)} au`
            : pk.data.peak?.edge
              ? `Peak · ${(pk.data.peak.d_lo ?? pk.data.peak.d ?? 0).toFixed(0)} au or farther`
              : `Peak · ${(pk.data.peak?.d ?? 0).toFixed(0)} au`}
          {pk.data.peak?.sn != null ? ` · search signal ${pk.data.peak.sn.toFixed(1)}× the noise` : ''}
        </b>
        <em class="pill lead">{pk.data.peak?.known?.state === 'ok' ? 'known body' : 'lead'}</em>
      </div>
      <div class="where">
        {/* the name of the searched field the peak lies in (a place people know), never the name of the peak itself */}
        {(() => {
          const near = PLACES.find(([, ra, dec]) => sep([ra, dec], [pk.ra, pk.dec]) < 0.3)?.[0];
          return near ? `in the ${near.replace(/ field$/, '')} field · ` : '';
        })()}
        RA {pk.ra.toFixed(2)} · Dec {fmtDec(pk.dec)}
        {pk.data.peak?.phot_sn != null ? ` · photometry ${pk.data.peak.phot_sn.toFixed(1)}× the noise` : ''}
      </div>
      {pk.data.peak?.known?.state === 'ok' && pk.data.peak.known.words && (
        <p>{`${pk.data.peak.known.words[0]!.toUpperCase()}${pk.data.peak.known.words.slice(1)}. JPL’s own place and the search’s agree to ${Math.max(...(pk.data.peak.known.seps ?? [0])).toFixed(1)}″.`}</p>
      )}
      {pk.data.peak?.stamp && (
        <figure class="px3-stamp">
          <img src={pk.data.peak.stamp} alt="The deep sum of the passes at the lead's place" loading="lazy" />
          <figcaption>deep sum at the lead’s place: where the body would be visible</figcaption>
        </figure>
      )}
      <div class="px3-links">
        <a class="go" href={fieldOf(pk)}>
          Open the lead in the Lab (5′) →
        </a>
        {searchedOf(pk) && (
          <a class="go faint" href={labHref(searchedOf(pk)!)}>
            the searched field ({snap(searchedOf(pk)!.open.fov)}′)
          </a>
        )}
      </div>
    </div>
  );
  /** a field's Find Planet X result, from the same peaks Findings lists: its leads, or the known bodies found in it, or nothing */
  const resultOf = (f: SearchedField): { text: string; lead: boolean } => {
    if (f.state === 'running') return { text: 'running', lead: false };
    const inF = (x: Peak) => sep([f.ra, f.dec], [x.ra, x.dec]) <= f.fov / 120;
    const nl = (leads ?? []).filter(inF).length;
    if (nl) return { text: `${nl} ${nl === 1 ? 'lead' : 'leads'}`, lead: true };
    const kn = knownPeaks
      .filter(inF)
      .map((x) => (x.data.peak?.known?.name ?? '').replace(/^\d+\s+/, '').replace(/\s*\(.*\)$/, ''))
      .filter(Boolean);
    if (kn.length) return { text: `${[...new Set(kn)].join(', ')}, found blind`, lead: false };
    if (/^(Eris|Makemake)$/.test(nameOf(f))) return { text: 'known body, found blind', lead: false };
    return { text: 'nothing found', lead: false };
  };
  // one label per place on the model (the newest search of it)
  const seen = new Set<string>();
  const onModel = mine.filter((f) => {
    const n = nameOf(f);
    if (seen.has(n)) return false;
    seen.add(n);
    return true;
  });
  const fields: PxField[] = onModel.map((f) => ({
    key: f.key,
    ra: f.ra,
    dec: f.dec,
    name: nameOf(f),
    best: resultOf(f).text,
    href: labHref(f),
    lead: resultOf(f).lead,
    // a field with no name and nothing found is a line of sight only: its coordinates are in the list (hundreds of them crowded the model)
    plain: nameOf(f).startsWith('RA ') && resultOf(f).text === 'nothing found',
  }));
  const fieldSig = fields.map((f) => f.key + f.best).join('|') + (leads?.length ?? '-');

  useEffect(() => {
    let dead = false;
    void import('./scene').then(({ PxScene }) => {
      if (dead || !canvas.current || !tags.current) return;
      const blocked = () =>
        [...(model.current?.querySelectorAll<HTMLElement>('.px3-ov') ?? [])]
          .filter((e) => e.offsetParent)
          .map((e) => e.getBoundingClientRect());
      scene.current = new PxScene(canvas.current, tags.current, blocked);
      setReady(true);
    });
    return () => {
      dead = true;
      scene.current?.dispose();
      scene.current = null;
    };
  }, []);
  useEffect(() => {
    if (ready) scene.current?.setFields(fields);
  }, [ready, fieldSig]);
  useEffect(() => {
    if (ready) scene.current?.setLayers(layers);
  }, [ready, layers]);
  useEffect(() => {
    if (ready) scene.current?.setLog(log);
  }, [ready, log]);

  const openChapter = (i: number) => {
    const next = chapter === i ? -1 : i;
    setChapter(next);
    const c = next >= 0 ? CH[next]! : null;
    setLayersState(c ? c.show : ALL);
    scene.current?.view(c ? c.view : 'home', next);
  };
  const go = (t: typeof tab) => {
    setTab(t);
    if (t !== 'story' && chapter >= 0) openChapter(chapter);
  };
  const reset = () => {
    setChapter(-1);
    setLayersState(ALL);
    scene.current?.view('home', -1);
  };
  const rec = RECORDED['ecl']!;
  const cap = chapter >= 0 ? CH[chapter]!.cap : null;

  return (
    <div class="screen px3">
      <section class="px3-model" ref={model} aria-label="Model of the Solar System with the predicted Planet Nine orbits">
        <canvas ref={canvas} class="px3-canvas" />
        <div ref={tags} class="px3-tags" />
        <div class="px3-ov px3-tl">
          <h1>Planet X</h1>
          <div class="px3-st" data-testid="px-stats">
            <span>
              {leads == null || searched == null ? (
                'loading what was searched…'
              ) : (
                <>
                  <b>{nLeads}</b> {nLeads === 1 ? 'spot' : 'spots'} to check in <b>{mine.length}</b>{' '}
                  <span title="Fields of any size where Find Planet X ran (most other searched fields are the time-lapse search only)">fields searched for Planet X</span>
                </>
              )}
            </span>
            <span>
              <b>0</b> confirmed
            </span>
          </div>
          <div class="px3-chips">
            {CHIPS.map((c) => (
              <button
                key={c.g}
                class="px3-chip"
                style={{ '--c': c.colour }}
                aria-pressed={layers[c.g]}
                onClick={() => setLayersState({ ...layers, [c.g]: !layers[c.g] })}
              >
                <i />
                {c.name}
              </button>
            ))}
            {(faint || (pk?.faint ?? 0) > 0) && (
              <button
                class="px3-chip"
                style={{ '--c': '#8a9aa6' }}
                aria-pressed={faint}
                data-testid="px-faint"
                title="Far peaks (beyond 200 au) on detectors 1–3 whose photometry is under 6σ: most are noise, so they are left out unless you ask. Search signal and photometry are the two numbers on each card."
                onClick={() => setFaint(!faint)}
              >
                <i />
                Faint peaks {faint ? 'shown' : 'hidden'}: {pk?.faint ?? 0}
              </button>
            )}
          </div>
          {cap && (
            <div class="px3-cap" style={{ '--c': cap[0] }}>
              <b>{chapter + 1}</b> · {cap[1]}
            </div>
          )}
        </div>
        <div class="px3-ov px3-tools">
          <span class="px3-seg">
            <button class={log ? 'on' : ''} onClick={() => setLog(true)}>
              Log
            </button>
            <button class={!log ? 'on' : ''} onClick={() => setLog(false)}>
              True
            </button>
          </span>
          <span class="px3-seg">
            <button onClick={reset}>Reset view</button>
          </span>
        </div>
        {layers.p9 && (
          <div class="px3-ov px3-p9">
            <div class="h">
              <b>Planet Nine · predicted, not found</b>
              <span>Brown &amp; Batygin 2021</span>
            </div>
            <div class="kv">
              <span>
                mass<b>6.2 M⊕</b>
              </span>
              <span>
                a
                <b>
                  {P9.aRange[0]}–{P9.aRange[1]} au
                </b>
              </span>
              <span>
                perihelion<b>240–385 au</b>
              </span>
              <span>
                tilt<b>16 ± 5°</b>
              </span>
            </div>
            <div class="lg">
              <span>
                <i />
                most likely orbit
              </span>
              <span>
                <i class="thin" />
                the published range
              </span>
              <span>
                <i class="glow" />
                where it most likely is now
              </span>
            </div>
          </div>
        )}
        <div class="px3-ov px3-credit">
          <span class="px3-hint">drag to turn · scroll to zoom</span>
          <span class="px3-hint touch">two fingers to turn and zoom</span>
        </div>
      </section>

      <aside class="px3-panel" aria-label="Planet X">
        <div class="px3-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'cands'} onClick={() => go('cands')} data-testid="px-tab-cands">
            Candidates <span class="n">{leads == null ? '…' : nLeads}</span>
          </button>
          <button role="tab" aria-selected={tab === 'searched'} onClick={() => go('searched')} data-testid="px-tab-searched">
            Searched <span class="n">{searched == null ? '…' : mine.length}</span>
          </button>
          <button role="tab" aria-selected={tab === 'story'} onClick={() => go('story')} data-testid="px-tab-story">
            Story
          </button>
        </div>
        <div class="px3-body">
          {tab === 'cands' && (
            <div data-testid="px-cands">
              <h4 class="px3-sub">Leads{leads == null ? '' : ` · ${nLeads}`}</h4>
              {leads == null && (
                <p class="px3-note" role="status" data-testid="px-leads-wait">
                  loading the candidates…
                </p>
              )}
              {nLeads > 0 && (
                <p class="px3-note" data-testid="px-lead-note">
                  Each lead is a peak that stands out of its field’s noise. A lead is not a body yet: it needs one orbit through two or three passes and the
                  check against known bodies.
                </p>
              )}
              {(leads ?? []).map(card)}
              {leads != null && nLeads === 0 && <p class="px3-note">No leads right now.</p>}
              <h4 class="px3-sub">Ruled out</h4>
              <div
                class="px3-cand rej"
                onMouseEnter={() => scene.current?.highlight(mine.find((f) => nameOf(f) === 'Ecliptic D4 field')?.key ?? null)}
                onMouseLeave={() => scene.current?.highlight(null)}
              >
                <div class="t">
                  <b>{rec.title}</b>
                  <em class="pill no">{rec.verdict}</em>
                </div>
                <div class="where">RA 60.94 · Dec +8.39 · ecliptic D4 field</div>
                <p>{rec.text}</p>
                {rec.rows && (
                  <table>
                    <tbody>
                      {rec.rows.map((r) => (
                        <tr key={r[0]}>
                          <td>{r[0]}</td>
                          <td class="r">{r[1]}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <h4 class="px3-sub">Known worlds found blind, then linked</h4>
              {KNOWN.map((k) => (
                <div key={k.who} class="px3-cand ok">
                  <div class="t">
                    <b>
                      {k.who} · {k.d}
                    </b>
                    <em class={`pill ${k.ok ? 'ok' : ''}`}>{k.ok ? 'confirmed' : k.who === 'Sedna' ? 'strong lead' : 'two passes'}</em>
                  </div>
                  <div class="where">
                    {k.jpl} · {k.how}
                  </div>
                  <a class="go" href={k.lab}>
                    Open in the Lab →
                  </a>
                </div>
              ))}
              {knownPeaks
                .filter((f) => f.data.peak?.known?.name && !KNOWN.some((k) => f.data.peak!.known!.name!.includes(k.who)))
                .map((f) => (
                  <div key={f.id} class="px3-cand ok">
                    <div class="t">
                      <b>
                        {f.data.peak!.known!.name} · {(f.data.peak?.d ?? 0).toFixed(0)} au
                      </b>
                      <em class="pill ok">named by JPL</em>
                    </div>
                    <div class="where">{f.data.peak!.known!.words ?? `RA ${f.ra.toFixed(2)} · Dec ${fmtDec(f.dec)}`}</div>
                    <a class="go" href={fieldOf(f)}>
                      Open in the Lab →
                    </a>
                  </div>
                ))}
              {echoes.length > 0 && (
                <p class="px3-note" data-testid="px-echoes">
                  {echoes.length === 1 ? '1 more peak is' : `${echoes.length} more peaks are`}{' '}
                  {[...new Set(echoes)].join(' and ')} seen in one of its passes: the search lined that pass up with noise in the others.
                  Not counted as {echoes.length === 1 ? 'a lead' : 'leads'}.
                </p>
              )}
              {honest.map((l, k) => (
                <p key={k} class="px3-note">
                  {l}
                </p>
              ))}
            </div>
          )}
          {tab === 'searched' && (
            <div data-testid="px-searched">
              <div class="px3-auto">
                <b>It runs by itself</b> on every field opened in the Lab and on the fields we search, from 30 to 2000 times the Earth’s
                distance from the Sun.
              </div>
              {searched == null && <p class="px3-note">Loading the searched fields…</p>}
              {mine.map((f) => (
                <a
                  key={f.key}
                  class={`px3-sf ${resultOf(f).lead ? 'lead' : ''}`}
                  href={labHref(f)}
                  onMouseEnter={() => scene.current?.highlight(f.key)}
                  onMouseLeave={() => scene.current?.highlight(null)}
                >
                  <span class="sd" />
                  <span>
                    <b>{nameOf(f)}</b>
                    <small>
                      RA {f.ra.toFixed(2)} · Dec {fmtDec(f.dec)} · {f.fov}′ · {hhmm(f.searched_at)}
                    </small>
                  </span>
                  <span class="sr">
                    {resultOf(f).text}
                    <i>open →</i>
                  </span>
                </a>
              ))}
              <p class="px3-note">Live from spherex.space, newest first. Click a field, here or on the model, to open it in the Lab.</p>
            </div>
          )}
          {tab === 'story' && (
            <div data-testid="px-story">
              {CH.map((c, i) => (
                <div key={i} class={`px3-ch ${chapter === i ? 'on' : ''}`}>
                  <button onClick={() => openChapter(i)} aria-expanded={chapter === i}>
                    <span class="k">{i + 1}</span>
                    <span>
                      <h3>{c.t}</h3>
                      <span class="sub">{c.sub}</span>
                    </span>
                    <span class="eye">◉ on the model</span>
                  </button>
                  {chapter === i && <div class="in">{body(i, mine.length, leads ?? [])}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
        <div class="px3-foot">
          Orbits: JPL · Planet Nine: Brown &amp; Batygin 2021 · textures: Solar System Scope (CC BY 4.0) · sky: 2MASS · bodies enlarged
        </div>
      </aside>
    </div>
  );
}

function body(i: number, nFields: number, peaks: Peak[]) {
  if (i === 0)
    return (
      <>
        <p>
          The most distant known bodies beyond Neptune, <b>Sedna, 2012 VP113</b> and others, travel on long, stretched orbits. Their closest
          points to the Sun all lie on one side, and their orbits lean the same way. On the model they are the blue orbits; the short blue
          lines point to where each one comes closest.
        </p>
        <p>
          Chance would scatter them. One explanation is the pull of an unseen planet whose own orbit points the opposite way:{' '}
          <b>Planet Nine</b>.
        </p>
        <div class="src">Batygin &amp; Brown 2016 · Brown &amp; Batygin 2019</div>
      </>
    );
  if (i === 1)
    return (
      <>
        <div class="px3-facts">
          <div>
            <b>6.2 M⊕</b>
            <span>mass, 4.9–8.4</span>
          </div>
          <div>
            <b>380 au</b>
            <span>average distance, 300–520</span>
          </div>
          <div>
            <b>300 au</b>
            <span>closest to the Sun, 240–385</span>
          </div>
          <div>
            <b>16 ± 5°</b>
            <span>tilt of its orbit</span>
          </div>
        </div>
        <p>
          One orbit takes about <b>7,400 years</b>. It spends most of that time near its far point, around <b>460 au</b>, so that is where
          it most likely is now: the bright stretch on the model. The thin orange orbits are the whole published range; the bright one is
          the most likely.
        </p>
        <div class="src">Brown &amp; Batygin 2021</div>
      </>
    );
  if (i === 2)
    return (
      <>
        <p>
          At 400–500 au sunlight is <b>160,000–250,000 times weaker</b> than at Earth, and the little it reflects fades again on the way
          back.
        </p>
        <p>
          Its orbit crosses a long band of sky, partly through the Milky Way, where stars crowd. And it barely moves: over six months it
          shifts only because the Earth has moved, about <b>0.3°</b> at 400 au.
        </p>
      </>
    );
  if (i === 3)
    return (
      <>
        <p>
          SPHEREx maps the <b>whole sky every six months</b> in 102 colours of infrared light. Two passes half a year apart see the Earth on
          opposite sides of the Sun: a distant body jumps by an amount that gives its distance.
        </p>
        <p>
          Adding up the exposures along every orbit a body could have reaches fainter than any single picture. Each search measures its own
          noise bar on its field and says it: a body fainter than that bar is not found there, and finding nothing does not show that
          nothing is there.
        </p>
      </>
    );
  return (
    <>
      <p>
        Find Planet X has run on <b>{nFields}</b> {nFields === 1 ? 'field' : 'fields'} so far, every one opened in the Lab or searched by
        us, shown on the model as lines of sight.{' '}
        {peaks.length ? (
          <>
            There {peaks.length === 1 ? 'is' : 'are'} <b>{peaks.length}</b> {peaks.length === 1 ? 'lead' : 'leads'}: peaks that stand out of
            the noise and are not known bodies, each needing one orbit through the passes before it counts.
          </>
        ) : (
          'No peak stands out of the noise right now.'
        )}
      </p>
      <p>
        In the ecliptic D4 field a 394 au candidate stood out, but ten years of NEOWISE images would show it at 13.6 times the noise and show nothing:{' '}
        <b>ruled out</b>.
      </p>
      <p>No Planet Nine has been found. A search that finds nothing does not show that nothing is there.</p>
    </>
  );
}
