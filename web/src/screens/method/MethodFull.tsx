import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import './method.css';
import { renderBlocks, type Block, type Heading } from './md';
import { PART_META, type PartMeta } from './parts';
import { Figure } from './figures';

// The texts of the parts: ./text/NN-*.md (one per part). Where a part has no text of its own yet, the earlier account of that step fills it.
const NUMBERED = import.meta.glob('./text/[0-9][0-9]-*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
import legacyOverview from './text/overview.md?raw';
import legacyData from './text/data.md?raw';
import legacyStacks from './text/stacks.md?raw';
import legacyViews from './text/views.md?raw';
import legacySpectra from './text/spectra.md?raw';
import legacySearches from './text/searches.md?raw';
import legacyChecks from './text/checks.md?raw';
import legacyValidation from './text/validation.md?raw';
import legacyInjection from './text/report-injection.md?raw';
import legacyMost from './text/report-most.md?raw';
import legacyWavelength from './text/report-wavelength.md?raw';

const LEGACY: Record<string, string> = {
  '06': legacyOverview,
  '07': legacyData,
  '08': legacyStacks,
  '09': legacyViews,
  '10': legacySpectra,
  '11': legacySearches,
  '12': legacyChecks,
  '14': [legacyValidation, legacyInjection, legacyMost, legacyWavelength].join('\n\n'),
};

/** The text of a part: its own file, else the earlier account of that step. */
function textOf(id: string): string {
  const own = Object.entries(NUMBERED).find(([path]) => path.includes(`/${id}-`));
  return own ? own[1] : (LEGACY[id] ?? '');
}

/** Old addresses (#/method?sec=searches) and part numbers (#/method?sec=11). */
const ALIAS: Record<string, string> = { overview: '06', data: '07', stacks: '08', views: '09', spectra: '10', searches: '11', checks: '12', validation: '14', dated: '14' };
const wanted = (): string | null => {
  const w = new URLSearchParams(location.hash.split('?')[1] ?? '').get('sec');
  if (!w) return null;
  return /^\d\d?$/.test(w) ? w.padStart(2, '0') : (ALIAS[w] ?? null);
};

// Numbers on this page that the layout itself carries come from runs on the production service.
const NUM = {
  checks: '8',
  chance: '0.0',
  eris: '95.9 au',
  erisLong: '95.9 ± 0.6 au',
};

interface PartView {
  meta: PartMeta;
  blocks: Block[];
  headings: Heading[];
}

type ResultBlock = Extract<Block, { kind: 'result' }>;
type FigureBlock = Extract<Block, { kind: 'figure' }>;
type Row = { kind: 'row'; html: string[]; results: ResultBlock[] };

/** The blocks of a part grouped into rows: a run of text with its callouts in the margin, and figures between them at the full width. */
function layoutRows(blocks: Block[]): (Row | FigureBlock)[] {
  const out: (Row | FigureBlock)[] = [];
  let cur: Row | null = null;
  for (const b of blocks) {
    if (b.kind === 'figure') {
      cur = null;
      out.push(b);
    } else {
      if (!cur) {
        cur = { kind: 'row', html: [], results: [] };
        out.push(cur);
      }
      if (b.kind === 'html') cur.html.push(b.html);
      else cur.results.push(b);
    }
  }
  return out;
}

function Result({ r }: { r: ResultBlock }) {
  return (
    <div class={`res ${r.tone}`}>
      <div class="rk">Result</div>
      <b>{r.headline}</b>
      {r.text && <p>{r.text}</p>}
      {r.source && <div class="s">{r.source}</div>}
    </div>
  );
}

const CHECKS: [string, string, string, string, string][] = [
  ['Nearby star · Gaia', "Barnard's Star", '11.4 ± 0.5″/yr', 'Gaia DR3 10.39 · 2.1σ', 'barnard_5arcmin_aug26.jpg'],
  ['Brown dwarf · Gaia', 'ε Indi B', '4.6 ± 0.5″/yr', 'Gaia DR3 4.684 · 0.2σ', 'epsindi_b_sep26.jpg'],
  ['Coldest brown dwarf · SIMBAD', 'WISE 0855−0714', '7.9 ± 0.6″/yr', 'SIMBAD 8.15 · −0.4σ', ''],
  ['Asteroids · JPL', 'Found blind, Dec 2025', '6 of 8', 'to V 19.1 · 0 false tracks', 'film/sky_06.jpg'],
  ['Chance control', 'Shuffled data', NUM.chance, 'chance tracks per field', 'barnard_difference.jpg'],
  ['Beyond Neptune · JPL', 'Eris and Makemake', NUM.erisLong, 'JPL 95.5 · Makemake 52.4 (52.3)', 'eris_deep_sum_97au.jpg'],
  ['Planet X candidate · NEOWISE', '394 au candidate', 'ruled out', 'NEOWISE would show it at 13.6σ', ''],
  ['Spectra · 2MASS, WISE', 'A field star', 'within 3 %', 'J 0.99 · H 1.02 · Ks 1.03 · W1 0.985', 'spectrum-crop.jpg'],
];

function Validation() {
  return (
    <>
      <div class="checks">
        {CHECKS.map(([k, h, n, s, pic]) => (
          <div class="ck" key={k} style={pic ? undefined : 'grid-template-columns:1fr'}>
            {pic && <div class="pic" style={`background-image:url('/method/${pic}')`} />}
            <div>
              <div class="kk">{k}</div>
              <h4>{h}</h4>
              <div class="num">{n}</div>
              <div class="src">{s}</div>
            </div>
          </div>
        ))}
      </div>
      <div class="mf-scroll">
        <table class="v">
          <thead>
            <tr>
              <th>Check</th>
              <th>Number obtained</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["Barnard's Star motion", '11.37 ± 0.47″/yr', 'Gaia DR3 10.393″/yr · /movers, 5′'],
              ['ε Indi B motion', '4.59 ± 0.49″/yr', 'Gaia DR3 4.684″/yr · /movers, 5′'],
              ['WISE 0855−0714 motion', '7.93 ± 0.61″/yr', 'SIMBAD 8.15″/yr · /browndwarfs'],
              ['Known asteroids, blind', '6 of 8', 'JPL sb_ident + Horizons · /film, 60′, 24 visits'],
              ['Chance tracks', `${NUM.chance} / field`, '20 shuffled trials · film: 0.0 at ≥ 4 visits, 12 trials'],
              ['Eris distance', NUM.erisLong, 'JPL 95.5 · production link of two passes'],
              ['394 au candidate', 'ruled out', 'NEOWISE −0.0005 ± 0.0031 mJy where 13.6σ expected'],
              ['Spectrum vs catalogues', 'within 3 %', '2MASS and AllWISE · 357 of 385 exposures'],
            ].map(([a, b, c]) => (
              <tr key={a}>
                <td>{a}</td>
                <td class="n">{b}</td>
                <td>{c}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Limits() {
  return (
    <div class="limits">
      <div>
        <b>Bright stars saturate.</b> Brighter than about J = 10.5 the core is clipped: no spectrum, and the motion is measured on a wider field.
      </div>
      <div>
        <b>Asteroids to about V 19.</b> Fainter known bodies are not recovered inside a pass yet; a track needs four visits.
      </div>
      <div>
        <b>Find Planet X has a depth.</b> 0.039–0.047 mJy at 3 µm; below that, finding nothing proves nothing.
      </div>
      <div>
        <b>A lead is not a discovery.</b> It needs the link of passes, the known-body check and, for a body seen at 3 µm, the NEOWISE check.
      </div>
    </div>
  );
}

function Hero({ go }: { go: (id: string) => void }) {
  const link = (id: string) => (e: Event) => {
    e.preventDefault();
    go(id);
  };
  return (
    <header class="hero" id="top">
      <div class="k">Method</div>
      <h1>How we turn SPHEREx files into a sky you can watch move.</h1>
      <p class="lede">
        The whole account in one place: the challenge, the telescope, the idea that found Pluto, every step from a raw exposure to a film, every search with its numbers,
        and how we checked each one against another telescope.
      </p>
      <div class="paths">
        <a class="path" href="#m-01" onClick={link('01')}>
          <div class="pk">Story · parts 1–6</div>
          <h3>Why, and what you see</h3>
          <p>The challenge, SPHEREx, how Pluto was found, the hunt for Planet Nine, and what a video from raw data is.</p>
        </a>
        <a class="path" href="#m-07" onClick={link('07')}>
          <div class="pk">Methods · parts 7–13</div>
          <h3>Every step, with formulas</h3>
          <p>Reading, cleaning, stacks, views, spectra, the six searches, anomalies, chance controls, exports.</p>
        </a>
        <a class="path" href="#m-14" onClick={link('14')}>
          <div class="pk">Results · parts 14–15</div>
          <h3>How we know it works</h3>
          <p>Each check against Gaia, JPL, SIMBAD, 2MASS, WISE and NEOWISE, and the limits we state.</p>
          <div class="big">
            <span>
              <b>{NUM.checks}</b>checks
            </span>
            <span>
              <b>{NUM.chance}</b>chance tracks
            </span>
            <span>
              <b>{NUM.eris}</b>Eris
            </span>
          </div>
        </a>
      </div>
      <div class="pipe" aria-label="From a raw exposure to a checked find">
        {[
          ['05', 'one pass, stacked', 'barnard_5arcmin_aug25.jpg'],
          ['08', 'passes added, coloured', 'barnard_coadd.jpg'],
          ['09', 'named bands', 'barnard_wall_of_bands.jpg'],
          ['09', 'difference of passes', 'barnard_difference.jpg'],
          ['11', 'film and searches', 'film/sky_06.jpg'],
          ['14', 'checked', 'spectrum-crop.jpg'],
        ].map(([n, s, f]) => (
          <a key={s} href={`#m-${n}`} onClick={link(n!)} style={`background-image:url('/method/${f}')`}>
            <span>
              <b>{+n!}</b>
              {s}
            </span>
          </a>
        ))}
      </div>
    </header>
  );
}

/**
 * The Method page: a sticky contents column with the part on show and a progress bar, the top (title, three ways in, the six steps on real Barnard frames), and fifteen
 * parts with a reading column, Result callouts in the margin and figures at the full width. On a phone a sticky menu jumps to a part.
 */
export function MethodFull() {
  const root = useRef<HTMLDivElement>(null);
  const parts: PartView[] = useMemo(
    () =>
      PART_META.map((meta) => {
        const r = renderBlocks(textOf(meta.id), `p${meta.id}`);
        return { meta, blocks: r.blocks, headings: r.headings };
      }),
    [],
  );
  const [cur, setCur] = useState('01');
  const [prog, setProg] = useState(0);
  const scroller = () => (root.current?.closest('.screen') as HTMLElement | null) ?? null;

  const go = (id: string, smooth = true) => {
    const el = root.current?.querySelector(`#m-${id}`) as HTMLElement | null;
    if (!el) return;
    el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
  };
  const goHead = (hid: string) => {
    const el = root.current?.querySelector(`#${CSS.escape(hid)}`) as HTMLElement | null;
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // the part on show and the progress, from the scroll of the page
  useEffect(() => {
    const sc = scroller();
    if (!sc) return;
    let raf = 0;
    const spy = () => {
      raf = 0;
      const top = sc.getBoundingClientRect().top;
      const y = top + sc.clientHeight * 0.3;
      let c = '01';
      for (const p of PART_META) {
        const el = root.current?.querySelector(`#m-${p.id}`);
        if (el && el.getBoundingClientRect().top < y) c = p.id;
      }
      setCur(c);
      setProg((100 * sc.scrollTop) / Math.max(1, sc.scrollHeight - sc.clientHeight));
    };
    const on = () => {
      if (!raf) raf = requestAnimationFrame(spy);
    };
    sc.addEventListener('scroll', on, { passive: true });
    spy();
    return () => {
      sc.removeEventListener('scroll', on);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  useEffect(() => {
    const open = () => {
      const w = wanted();
      if (w) go(w, false);
    };
    const t = setTimeout(open, 60);
    const t2 = setTimeout(open, 500);
    window.addEventListener('hashchange', open);
    return () => {
      clearTimeout(t);
      clearTimeout(t2);
      window.removeEventListener('hashchange', open);
    };
  }, []);

  let lastGroup = '';
  return (
    <div class="mv2" ref={root}>
      <nav class="mv2-toc" aria-label="Contents of the Method">
        {parts.map(({ meta, headings }) => {
          const heads = headings.filter((h) => h.level === 2);
          const subs = heads.length >= 2 ? heads.map((h) => ({ t: h.title, id: h.id })) : meta.subs.map((t) => ({ t, id: '' }));
          const group = meta.group !== lastGroup ? meta.group : '';
          lastGroup = meta.group;
          return (
            <div key={meta.id}>
              {group && <div class="h">{group}</div>}
              <a
                href={`#m-${meta.id}`}
                data-id={meta.id}
                class={cur === meta.id ? 'on' : ''}
                onClick={(e) => {
                  e.preventDefault();
                  go(meta.id);
                }}
              >
                <span class="n">{meta.id}</span>
                <span>{meta.title}</span>
              </a>
              <div class={`subs ${cur === meta.id ? 'on' : ''}`}>
                {subs.map((s) => (
                  <a
                    key={s.t}
                    href={`#m-${meta.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      if (s.id) goHead(s.id);
                      else go(meta.id);
                    }}
                  >
                    {s.t}
                  </a>
                ))}
              </div>
            </div>
          );
        })}
        <div class="prog" aria-hidden="true">
          <i style={`width:${prog}%`} />
        </div>
      </nav>
      <div class="mv2-main">
        <div class="mtoc">
          <span>Method</span>
          <select aria-label="Jump to part" value={cur} onChange={(e) => go((e.target as HTMLSelectElement).value)}>
            {PART_META.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id} · {p.title}
              </option>
            ))}
          </select>
        </div>
        <div class="wrap">
          <Hero go={(id) => go(id)} />
          {parts.map(({ meta, blocks }) => (
            <section class="part" id={`m-${meta.id}`} key={meta.id}>
              <div class="grp">
                {meta.group} · <b>part {+meta.id}</b>
              </div>
              <h2>{meta.title}</h2>
              <p class="lead">{meta.lead}</p>
              {meta.id === '14' && <Validation />}
              {meta.id === '15' && <Limits />}
              {layoutRows(blocks).map((row, i) =>
                row.kind === 'figure' ? (
                  <Figure key={i} name={row.name} caption={row.caption} />
                ) : (
                  <div class="row" key={i}>
                    <div class="txt mf-prose">
                      {row.html.map((h, j) => (
                        <div key={j} dangerouslySetInnerHTML={{ __html: h }} />
                      ))}
                    </div>
                    <div class="mar">
                      {row.results.map((r, j) => (
                        <Result key={j} r={r} />
                      ))}
                    </div>
                  </div>
                ),
              )}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
