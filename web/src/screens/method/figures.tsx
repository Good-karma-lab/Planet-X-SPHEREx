import '../planetx/planetx.css';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { ComponentChildren } from 'preact';
import filmDates from './film-dates.json';
import { Model } from '../planetx/model';
import { DEPTH } from '../planetx/data';

/** The figures of the Method page, by the name a text gives them in `[figure: name | caption]`. Every picture is a real product of the service. */

const M = (f: string) => `/method/${f}`;

function Fig({ name, cap, children }: { name: string; cap: string; children: ComponentChildren }) {
  return (
    <figure class="mf-fig" data-fig={name}>
      <div class="box">{children}</div>
      <figcaption>
        <b>{cap}</b>
        <span>{name}</span>
      </figcaption>
    </figure>
  );
}

/** Pictures in a row of 2 or 3, each with its tag. */
function Row({ items, cols }: { items: { src: string; alt: string; tag?: string; year?: string; sub?: string }[]; cols: 2 | 3 }) {
  return (
    <div class={`g${cols}`}>
      {items.map((it) => (
        <div key={it.src}>
          <img src={M(it.src)} alt={it.alt} loading="lazy" />
          {it.year && (
            <span class="y">
              {it.year}
              {it.sub && <small>{it.sub}</small>}
            </span>
          )}
          {it.tag && <span class="tagc">{it.tag}</span>}
        </div>
      ))}
    </div>
  );
}

/** The Days film of the 60′ ecliptic field, December 2025: the 24 real visit pictures; it plays only while it is on screen. */
function FilmPlayer() {
  const [k, setK] = useState(0);
  const [play, setPlay] = useState(true);
  const [seen, setSeen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver((e) => setSeen(e.some((x) => x.isIntersecting)), { threshold: 0.2 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (!play || !seen || matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const t = setInterval(() => setK((v) => (v + 1) % filmDates.length), 500);
    return () => clearInterval(t);
  }, [play, seen]);
  return (
    <div class="player" ref={box}>
      <div class="pic">
        <img src={M(`film/sky_${String(k).padStart(2, '0')}.jpg`)} alt={`Days film, ecliptic field, visit ${k + 1} of ${filmDates.length}`} />
        <span class="tagc">{filmDates[k]} UTC</span>
      </div>
      <div class="side">
        <b>Days film · Dec 2025</b>
        <br />
        60′ ecliptic field, {filmDates.length} visits hours apart. The stars stand still; the asteroids hop.
        <div class="tl">
          {filmDates.map((_, i) => (
            <i key={i} class={i === k ? 'on' : ''} onClick={() => { setPlay(false); setK(i); }} />
          ))}
        </div>
        <div class="ctl">
          <button onClick={() => setPlay(!play)}>{play ? 'Pause' : 'Play'}</button>
          <button
            onClick={() => {
              setPlay(false);
              setK((v) => (v + 1) % filmDates.length);
            }}
          >
            Step
          </button>
        </div>
      </div>
    </div>
  );
}

type Maker = (cap: string) => preact.JSX.Element;

const FIGS: Record<string, { cap: string; make: (cap: string, name: string) => preact.JSX.Element }> = {
  'wall-detectors': {
    cap: 'The six detectors side by side, one field · Barnard’s Star',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <img src={M('barnard_wall_detectors.jpg')} alt="The six SPHEREx detectors side by side on Barnard's Star" loading="lazy" />
      </Fig>
    ),
  },
  'wall-views': {
    cap: 'Two views of the same field',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Row
          cols={2}
          items={[
            { src: 'barnard_wall_of_bands.jpg', alt: 'Wall of bands', tag: 'wall of bands' },
            { src: 'barnard_difference.jpg', alt: 'Difference of passes', tag: 'difference of passes' },
          ]}
        />
      </Fig>
    ),
  },
  'blink-pluto': {
    cap: 'Barnard’s Star field in 1950, 1991 and 2026: the same idea Tombaugh used',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Row
          cols={3}
          items={[
            { src: 'blink_1950_poss1.jpg', alt: "Barnard's Star field, 1950", year: '1950', sub: 'POSS-I plate' },
            { src: 'blink_1991_poss2.jpg', alt: "Barnard's Star field, 1991", year: '1991', sub: 'POSS-II plate' },
            { src: 'blink_2026_spherex.jpg', alt: "Barnard's Star field, 2026", year: '2026', sub: 'SPHEREx' },
          ]}
        />
      </Fig>
    ),
  },
  'pendulum-barnard': {
    cap: 'The pendulum: three SPHEREx passes, played 1 → 2 → 3 → 2 in the lab',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Row
          cols={3}
          items={[
            { src: 'barnard_5arcmin_aug25.jpg', alt: "Barnard's Star, SPHEREx Aug 2025", year: 'Aug 2025', sub: 'pass 1' },
            { src: 'barnard_5arcmin_mar26.jpg', alt: "Barnard's Star, SPHEREx Mar 2026", year: 'Mar 2026', sub: 'pass 2' },
            { src: 'barnard_5arcmin_aug26.jpg', alt: "Barnard's Star, SPHEREx Aug 2026", year: 'Aug 2026', sub: 'pass 3' },
          ]}
        />
      </Fig>
    ),
  },
  'raw-to-video': {
    cap: 'Barnard’s Star field: from the exposures of one pass to the difference between passes',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <div class="steps">
          {[
            ['1', 'one pass, stacked', 'barnard_5arcmin_aug25.jpg'],
            ['2', 'passes added, coloured by spectrum', 'barnard_coadd.jpg'],
            ['3', 'the same field in named bands', 'barnard_wall_of_bands.jpg'],
            ['4', 'difference of two passes', 'barnard_difference.jpg'],
          ].map(([n, s, f]) => (
            <div key={n}>
              <img src={M(f!)} alt={s} loading="lazy" />
              <span class="tagc">
                <b>{n}</b>
                {s}
              </span>
            </div>
          ))}
        </div>
      </Fig>
    ),
  },
  'spectrum-source': {
    cap: 'The spectrum of a field star: one point per exposure, coloured by detector',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <img src={M('spectrum-crop.jpg')} alt="The spectrum of a field star, points by detector" loading="lazy" />
      </Fig>
    ),
  },
  'film-ecliptic-dec2025': {
    cap: 'The film search on real visits',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <FilmPlayer />
      </Fig>
    ),
  },
  'slider-eris': {
    cap: 'Eris comes into focus as the distance is swept; the passes added give the deep sum',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <div class="g2">
          <div>
            <video src={M('px_focus.mp4')} poster={M('px_focus.jpg')} muted loop playsInline autoPlay />
            <span class="tagc">distance slider</span>
          </div>
          <div>
            <img src={M('eris_deep_sum_97au.jpg')} alt="Eris, the deep sum of three passes" loading="lazy" />
            <span class="tagc">deep sum</span>
          </div>
        </div>
      </Fig>
    ),
  },
  'p9-orbits': {
    cap: 'The Solar System with the predicted Planet Nine orbit and the fields searched for it',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <div class="p9model">
          <Model />
        </div>
        <a class="p9link" href="#/planetx">
          Open the Planet X page →
        </a>
      </Fig>
    ),
  },
  'depth-vs-wise': {
    cap: 'The faintest body found blind at 3 µm: SPHEREx D4 against the WISE Planet Nine search (mJy, lower is deeper)',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <div class="depthbars">
          {DEPTH.map((d) => (
            <div key={d.name} class="db">
              <span class="dn">
                {d.name}
                <em>{d.note}</em>
              </span>
              <span class="dt">
                <i style={{ width: `${(d.mjy / 0.07) * 100}%`, background: d.colour }} />
              </span>
              <b>{d.mjy.toFixed(3)}</b>
            </div>
          ))}
        </div>
      </Fig>
    ),
  },
  'anomaly-map': {
    cap: 'The ε Indi B field: anomaly 1 is the brown dwarf',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <img src={M('anomaly-crop.jpg')} alt="Anomaly map of the epsilon Indi B field" loading="lazy" />
      </Fig>
    ),
  },
};

/** The figure of a name, or nothing when the page has no such figure (a name a text uses before its picture exists is simply not drawn). */
export function Figure({ name, caption }: { name: string; caption: string }) {
  const f = FIGS[name];
  if (!f) return null;
  return f.make(caption || f.cap, name);
}

export const FIGURE_NAMES = Object.keys(FIGS);
export type { Maker };
