import '../planetx/planetx.css';
import type { ComponentChildren } from 'preact';

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

/** A short muted loop recorded from spherex.space (every number in it is the live site's), with its poster. */
function Vid({ f, label }: { f: string; label: string }) {
  return <video class="mf-vid" src={M(`live/${f}.mp4`)} poster={M(`live/${f}.jpg`)} muted loop playsInline autoPlay preload="metadata" aria-label={label} />;
}

type Maker = (cap: string) => preact.JSX.Element;

const FIGS: Record<string, { cap: string; make: (cap: string, name: string) => preact.JSX.Element }> = {
  'wall-detectors': {
    cap: 'The six detectors side by side on Barnard’s Star, the passes playing: the star jumps the same way in all of them',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="wall-detectors" label='The six SPHEREx detectors side by side on Barnard’s Star, playing through the passes' />
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
    cap: 'The blink idea on our own plates: Barnard’s field in 1950, 1991 and 2026, one star moving',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="blink-pluto" label='Barnard’s Star field in 1950, 1991 and 2026 blinked' />
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
    cap: 'From the raw exposures to a film: raw frames, the stack of the passes, colour by wavelength, then the passes played',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="raw-to-video" label='Raw exposures, the stack, the coloured stack and the passes as a film' />
      </Fig>
    ),
  },
  'spectrum-source': {
    cap: 'A click on a star: its spectrum is measured in every exposure that covers it, point by point, coloured by detector',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="spectrum-source" label='The spectrum of a field star filling in point by point' />
      </Fig>
    ),
  },
  'film-ecliptic-dec2025': {
    cap: 'The Days film of the 60′ ecliptic field, December 2025: the visits hours apart, the known asteroids ringed and named',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="film-ecliptic-dec2025" label='The Days film of the ecliptic field with the asteroid tracks' />
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
    cap: 'The Solar System with the published range of Planet Nine orbits and the fields searched for it',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="p9-orbits" label='The Planet X model slowly turning' />
        <a class="p9link" href="#/planetx">
          Open the Planet X page →
        </a>
      </Fig>
    ),
  },
  'raw-exposures': {
    cap: 'The raw exposures of one place played in a row: the pixel grid, the wavelength and the brightness change from frame to frame',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <video src={M('raw-exposures.mp4')} poster={M('raw-exposures.jpg')} muted loop playsinline controls preload="none" aria-label="Raw SPHEREx exposures of one place played one after another" />
      </Fig>
    ),
  },
  'spectral-colour': {
    cap: 'One pass stack coloured by wavelength: each exposure is tinted by its own wavelength, a flat spectrum is white',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <video src={M('spectral-colour.mp4')} poster={M('spectral-colour.jpg')} muted loop playsinline controls preload="none" aria-label="A pass stack coloured by wavelength" />
      </Fig>
    ),
  },
  'anomaly-map': {
    cap: 'The ε Indi B field: the anomalies and red sources ringed in the picture and listed with their scores',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="anomaly-map" label='The anomaly map of the epsilon Indi B field' />
      </Fig>
    ),
  },
  'difference': {
    cap: 'The difference of passes: what moved shows as a pair of colours, what stayed cancels out',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="difference" label='The difference view of Barnard’s Star' />
      </Fig>
    ),
  },
  'archive-images': {
    cap: 'Archive images over time: the photographic plates and the infrared surveys before SPHEREx, in the same field',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="archive-over-time" label='Archive images of Barnard’s field played in time order' />
      </Fig>
    ),
  },
  'object-portrait': {
    cap: 'A click on an object: its portrait, position, proper motion and what the catalogues know',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="object-portrait" label='The object card of Barnard’s Star' />
      </Fig>
    ),
  },
  'field-size': {
    cap: 'The same place at 2′, 5′, 10′, 20′ and 60′: one click changes how much sky the lab reads',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="field-size-zoom" label='Barnard’s Star field from 2 to 60 arcminutes' />
      </Fig>
    ),
  },
  'epsindi-pendulum': {
    cap: 'ε Indi B in the pendulum: the orange-red brown dwarf steps between the four passes',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="epsindi-pendulum" label='Epsilon Indi B stepping between passes' />
      </Fig>
    ),
  },
  'epsindi-bands': {
    cap: 'ε Indi B in named bands: water, methane, ice, dust, CO₂ and CO side by side',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="epsindi-bands" label='Epsilon Indi B in the named bands' />
      </Fig>
    ),
  },
  'epsindi-wall': {
    cap: 'ε Indi B on the six detectors: the brown dwarf is brightest at the long wavelengths',
    make: (cap, name) => (
      <Fig name={name} cap={cap}>
        <Vid f="epsindi-wall" label='Epsilon Indi B on the six detectors' />
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
