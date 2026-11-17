import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import './method.css';
import { renderPart, type Block, type Heading } from './md';
import { Figure } from './figures';

// The texts of the parts: ./text/NN-*.md, one per part (the title is the first line, the lead the first paragraph).
const NUMBERED = import.meta.glob('./text/[0-9][0-9]-*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

const groupOf = (id: string): 'Story' | 'Methods' | 'Results' => (+id <= 6 ? 'Story' : +id <= 14 ? 'Methods' : 'Results');

/** Old addresses (#/method?sec=searches) and part numbers (#/method?sec=11). */
const ALIAS: Record<string, string> = {
  overview: '06',
  data: '07',
  stacks: '08',
  views: '09',
  spectra: '10',
  searches: '11',
  checks: '13',
  validation: '15',
  dated: '15',
};
const wanted = (): string | null => {
  const w = new URLSearchParams(location.hash.split('?')[1] ?? '').get('sec');
  if (!w) return null;
  return /^\d\d?$/.test(w) ? w.padStart(2, '0') : (ALIAS[w] ?? null);
};

// Numbers on this page that the layout itself carries come from runs on the production service.
// the version of this account: bumped, with a line in CHANGES, whenever an algorithm or a constant it states changes
export const METHOD_VERSION = { id: 'M38', date: '2026-11-17' };
const CHANGES: [string, string, string][] = [
  [
    'M38',
    '2026-11-17',
    'The sky grid and the background sweep; known objects of a frame and the deep match; the checks before submit for slow and fast movers with the NEOWISE stack; "visible in the picture" measured against the ring\'s own neighbourhood; people\'s questions, agreement and score; the glow rule for "unknown" next to a bright star; Time-lapse for film.',
  ],
];

const NUM = {
  checks: '8',
  chance: '0.0',
  asteroids: '6 of 8',
};

interface PartView {
  meta: { id: string; group: 'Story' | 'Methods' | 'Results'; title: string; lead: string };
  blocks: Block[];
  headings: Heading[];
}

type ResultBlock = Extract<Block, { kind: 'result' }>;
type FigureBlock = Extract<Block, { kind: 'figure' }>;
type Row = { kind: 'row'; html: string[]; results: ResultBlock[] };

/** The blocks of a part grouped into rows: text with the Result that follows it in the margin, and figures between them at the full width. */
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
      else {
        cur.results.push(b);
        cur = null; // a Result closes its row: the callout stands beside the text it follows
      }
    }
  }
  return out;
}

function Result({ r }: { r: ResultBlock }) {
  return (
    <div class="res">
      <div class="rk">Result</div>
      <p>{r.text}</p>
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
  ['Nearby star · Gaia, 20′ field', "Barnard's Star", '10.7 ± 0.8″/yr', 'Gaia DR3 10.4 · live /movers', 'barnard_5arcmin_aug25.jpg'],
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
              ['Chance tracks', `${NUM.chance} / field`, '20 shuffled trials · time-lapse: 0.0 at ≥ 4 visits, 12 trials'],
              ["Barnard's Star motion, 20′", '10.7 ± 0.8″/yr', 'Gaia DR3 10.4″/yr · /movers, 20′'],
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
        <b>Bright stars saturate.</b> Brighter than about J = 10.5 the core is clipped: no spectrum, and the motion is measured on a wider
        field.
      </div>
      <div>
        <b>Asteroids to about V 19.</b> Fainter known bodies are not recovered inside a pass yet; a track needs four visits.
      </div>
      <div>
        <b>Find Planet X has a depth.</b> It is the measured noise bar of the search on that field, shown with every result; below it,
        finding nothing proves nothing.
      </div>
      <div>
        <b>A lead is not a discovery.</b> It needs the link of passes and the known-body check.
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
      <div class="k">
        Method · {METHOD_VERSION.id} · {METHOD_VERSION.date}
      </div>
      <h1>How we turn SPHEREx files into a sky you can watch move.</h1>
      <p class="lede">
        The whole account in one place: the challenge, the telescope, the idea that found Pluto, every step from a raw exposure to a
        time-lapse, every search with its numbers, and how we checked each one against another telescope.
      </p>
      <details class="mchanges">
        <summary>What changed in {METHOD_VERSION.id}</summary>
        <ul>
          {CHANGES.map(([id, date, text]) => (
            <li key={id}>
              <b>
                {id} · {date}
              </b>{' '}
              {text}
            </li>
          ))}
        </ul>
      </details>
      <div class="paths">
        <a class="path" href="#m-01" onClick={link('01')}>
          <div class="pk">Story · parts 1–6</div>
          <h3>Why, and what you see</h3>
          <p>The challenge, SPHEREx, how Pluto was found, the hunt for Planet Nine, and what a video from raw data is.</p>
        </a>
        <a class="path" href="#m-07" onClick={link('07')}>
          <div class="pk">Methods · parts 7–14</div>
          <h3>Every step, with formulas</h3>
          <p>Reading, cleaning, stacks, views, spectra, the six searches, anomalies, chance controls, exports.</p>
        </a>
        <a class="path" href="#m-15" onClick={link('15')}>
          <div class="pk">Results · parts 15–16</div>
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
              <b>{NUM.asteroids}</b>asteroids
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
          ['11', 'Time-lapse and searches', 'film/sky_06.jpg'],
          ['15', 'checked', 'spectrum-crop.jpg'],
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
      Object.entries(NUMBERED)
        .map(([path, text]) => ({ id: /\/(\d\d)-/.exec(path)![1]!, text }))
        .sort((x, y) => x.id.localeCompare(y.id))
        .map(({ id, text }) => {
          const r = renderPart(text, `p${id}`);
          return { meta: { id, group: groupOf(id), title: r.title, lead: r.lead }, blocks: r.blocks, headings: r.headings };
        }),
    [],
  );
  const partsRef = useRef(parts);
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

  // the part on show and the progress, from the scroll of the page. What scrolls depends on the screen: the page's own container on a
  // desktop, the whole document on a phone; it is asked at every turn, and again once the layout and the pictures have settled
  useEffect(() => {
    const sc = scroller();
    let raf = 0;
    const spy = () => {
      raf = 0;
      const own = !!sc && sc.scrollHeight > sc.clientHeight + 2 && getComputedStyle(sc).overflowY !== 'visible';
      const top = own ? sc!.getBoundingClientRect().top : 0;
      const h = own ? sc!.clientHeight : window.innerHeight;
      const y = top + h * 0.3;
      let c = '01';
      for (const p of partsRef.current) {
        const el = root.current?.querySelector(`#m-${p.meta.id}`);
        if (el && el.getBoundingClientRect().top < y) c = p.meta.id;
      }
      setCur(c);
      const st = own ? sc!.scrollTop : window.scrollY;
      const range = own ? sc!.scrollHeight - sc!.clientHeight : document.documentElement.scrollHeight - window.innerHeight;
      setProg(Math.max(0, Math.min(100, (100 * st) / Math.max(1, range))));
    };
    const on = () => {
      if (!raf) raf = requestAnimationFrame(spy);
    };
    sc?.addEventListener('scroll', on, { passive: true });
    window.addEventListener('scroll', on, { passive: true });
    window.addEventListener('resize', on);
    root.current?.addEventListener('load', on, true);
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(on);
    if (root.current) ro?.observe(root.current);
    spy();
    const t = [setTimeout(on, 300), setTimeout(on, 1200), setTimeout(on, 3000)];
    return () => {
      sc?.removeEventListener('scroll', on);
      window.removeEventListener('scroll', on);
      window.removeEventListener('resize', on);
      root.current?.removeEventListener('load', on, true);
      ro?.disconnect();
      t.forEach(clearTimeout);
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
          const subs = heads.map((h) => ({ t: h.title, id: h.id }));
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
            {parts.map(({ meta: p }) => (
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
              {meta.id === '15' && <Validation />}
              {meta.id === '16' && <Limits />}
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
