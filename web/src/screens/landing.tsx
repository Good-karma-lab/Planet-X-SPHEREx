// Landing page: what SPHEREx Space is, in plain words, over the real sky.
// The background is NASA SVS's Deep Star Maps 2020 star map (Gaia DR2), the globe's default layer, as one equirectangular image
// drawn from inside the sphere with a stereographic camera that turns as the page scrolls. Nothing is drawn or modelled on it.
// Pictures on the page are real SPHEREx frames and DSS plates (web/public/landing/). Styles: ../landing.css (classes lp-).
import { visibleInterval } from '../lib/idle';
import { useEffect, useRef, useState } from 'preact/hooks';
import '../landing.css';

/** The app's main page (the globe). Links that do not open one object go here. */
const APP = '#/explore';
/** "Start searching", the one primary button: the globe with the Coverage layer on (the grid of squares, coloured by what is searched). */
const START = '#/explore?cov=1';
/** "Watch a star move": the globe with Barnard's Star selected and its path drawn. */
const STAR = `#/explore?sel=${encodeURIComponent("Barnard's Star")}&path=1`;
/** "See a brown dwarf move": the lab on ε Indi B, one of the five warmed test fields (its 2025-26 place). */
const BROWN_DWARF = '#/lab?ra=331.09645&dec=-56.80059&fov=5'; // its place at 2025.9: the warm field
/** Eris: the lab on its field, where Find Planet X runs. */
const ERIS = '#/lab?ra=27.378&dec=-0.120&fov=20';
/** The demo video (30 seconds), served as a static file of the web app. */
const VIDEO = '/demo/spherex-sky-demo.mp4';
const REPO = 'https://github.com/Good-karma-lab/Planet-X-SPHEREx';
const IMG = '/landing/';
/** NASA SVS Deep Star Maps 2020 (SVS 4851; Gaia DR2), baked by the service: equatorial J2000, equirectangular, RA 0° at the centre and
 *  increasing to the left, north up; the "lit" version (exposure ×3) shows the Milky Way. The 4k image comes first, the 8k one replaces it
 *  where the graphics card takes 8192-pixel textures. */
const SKY = '/tiles/svs-v1/equirect_4k_lit.webp';
const SKY_HI = '/tiles/svs-v1/equirect_8k_lit.webp';

const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const D2R = Math.PI / 180;

function jump(id: string) {
  return (e: Event) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
  };
}

/** The sections the top bar links to (id of the section, its name). */
const SECTIONS: [string, string][] = [
  ['how', 'How it works'],
  ['finds', 'What moves'],
  ['team', 'Team'],
  ['challenge', 'The challenge'],
];

/** The section links in a small menu, for a window too narrow for them in the bar (landing.css shows the button below 1080 px). */
function NavMenu() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => !box.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', away);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('pointerdown', away);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);
  const go = (id: string) => (e: Event) => {
    setOpen(false);
    jump(id)(e);
  };
  return (
    <div class="lp-menu" ref={box}>
      <button
        type="button"
        class="lp-menu-btn"
        aria-label="Sections"
        aria-expanded={open}
        aria-controls="lp-menu-list"
        onClick={() => setOpen((o) => !o)}
      >
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
        </svg>
      </button>
      {open && (
        <div class="lp-menu-list" id="lp-menu-list">
          {SECTIONS.map(([id, label]) => (
            <a key={id} href={`#${id}`} onClick={go(id)}>
              {label}
            </a>
          ))}
          <a href={REPO} target="_blank" rel="noopener noreferrer" onClick={() => setOpen(false)}>
            Source code
          </a>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ the sky behind the page */

const VS = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
const FS = `precision highp float;
uniform sampler2D sky;uniform vec2 res;uniform float ra0,dec0,k;
const float PI=3.14159265;
void main(){
  vec2 q=(gl_FragCoord.xy-res*.5)/k;
  float rho=length(q),c=2.*atan(rho*.5);
  float sd=sin(dec0),cd=cos(dec0),sc=sin(c),cc=cos(c);
  float dec=asin(clamp(cc*sd+(rho>0.?q.y*sc*cd/rho:0.),-1.,1.));
  float ra=ra0+atan(-q.x*sc,rho*cd*cc-q.y*sd*sc);
  vec3 col=texture2D(sky,vec2(fract(.5-ra/(2.*PI)),(PI*.5-dec)/PI)).rgb;
  vec2 e=gl_FragCoord.xy/res-.5;float vig=smoothstep(.95,.35,length(e*vec2(1.,.8)));
  float side=smoothstep(.0,.62,gl_FragCoord.x/res.x);
  gl_FragColor=vec4(col*(.55+.45*vig)*(.62+.38*side),1.);
}`;

/** The sky, from inside, turning with the scroll of `scroller` (and drifting slowly unless the reader asked for less motion). */
function SkyBackdrop({ scroller }: { scroller: () => HTMLElement | null }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const gl = cv.getContext('webgl', { antialias: false, alpha: false });
    if (!gl) return;
    const sh = (t: number, s: string) => {
      const o = gl.createShader(t)!;
      gl.shaderSource(o, s);
      gl.compileShader(o);
      return o;
    };
    const pr = gl.createProgram()!;
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, VS));
    gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) return;
    gl.useProgram(pr);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const u = (n: string) => gl.getUniformLocation(pr, n);
    let ready = false;
    let stop = false;
    let raf = 0;
    const t0 = performance.now();
    const still = reducedMotion();

    const draw = () => {
      raf = 0;
      if (stop || !ready) return;
      const dpr = Math.min(1.5, devicePixelRatio || 1);
      const W = Math.round(cv.clientWidth * dpr);
      const H = Math.round(cv.clientHeight * dpr);
      if (cv.width !== W || cv.height !== H) {
        cv.width = W;
        cv.height = H;
      }
      gl.viewport(0, 0, W, H);
      const el = scroller();
      const f = el ? el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight) : 0;
      const drift = still ? 0 : ((performance.now() - t0) / 1000) * 0.12;
      const fov = 104 * D2R;
      gl.uniform2f(u('res'), W, H);
      // the camera follows the Milky Way from Sagittarius to Cygnus as the page scrolls
      gl.uniform1f(u('ra0'), (266 + 46 * f + drift) * D2R);
      gl.uniform1f(u('dec0'), (-12 + 52 * f) * D2R);
      gl.uniform1f(u('k'), W / (4 * Math.tan(fov / 4)));
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (!still) raf = requestAnimationFrame(draw);
    };
    const kick = () => {
      if (!raf) raf = requestAnimationFrame(draw);
    };

    const tex = gl.createTexture();
    const load = (src: string, next?: string) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        if (stop) return;
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        ready = true;
        cv.classList.add('on');
        kick();
        if (next && (gl.getParameter(gl.MAX_TEXTURE_SIZE) as number) >= 8192) load(next);
      };
      img.src = src;
    };
    load(SKY, SKY_HI);
    const el = scroller();
    el?.addEventListener('scroll', kick, { passive: true });
    window.addEventListener('resize', kick);
    return () => {
      stop = true;
      if (raf) cancelAnimationFrame(raf);
      el?.removeEventListener('scroll', kick);
      window.removeEventListener('resize', kick);
    };
  }, []);
  return <canvas ref={ref} class="lp-sky" aria-hidden="true" />;
}

/* ------------------------------------------------------------------ Barnard's Star across 76 years */

/** Gaia DR3 proper motion of Barnard's Star, arcsec / yr, on a 24′ window centred on its J2000 place (north up, east left). */
const PM = { ra: -0.801551, dec: 10.362394 };
const at = (t: number): [number, number] => [50 - (PM.ra * (t - 2000)) / 14.4, 50 - (PM.dec * (t - 2000)) / 14.4];
/** Dates of real images of this place (the app's archive list); the path on the 1950 and 1991 plates was checked within 0.3 %. */
const EPOCHS: { t: number; lab: string; sub: string; future?: boolean }[] = [
  { t: 1950.52, lab: '1950', sub: 'POSS-I plate' },
  { t: 1988.36, lab: '', sub: '' },
  { t: 1991.46, lab: '1988–93', sub: 'POSS-II plates' },
  { t: 1993.57, lab: '', sub: '' },
  { t: 2000.41, lab: '2000', sub: '2MASS' },
  { t: 2010.7, lab: '2010', sub: 'AllWISE' },
  { t: 2050, lab: '2050', sub: 'where it will be', future: true },
];
const NOW = 2026.2;
const A = at(1950.52);
const B = at(2050);
const LEN = Math.hypot(B[0] - A[0], B[1] - A[1]);
const U: [number, number] = [(B[0] - A[0]) / LEN, (B[1] - A[1]) / LEN];

function PathHero() {
  const [t, setT] = useState(reducedMotion() ? NOW : 1950.5);
  useEffect(() => {
    if (reducedMotion()) return;
    return visibleInterval(() => setT((v) => (v + 0.5 > 2054 ? 1950.5 : v + 0.5)), 45);
  }, []);
  const [nx, ny] = at(NOW);
  const R = 4.2;
  const ts = Math.min(t, 2050);
  const [mx, my] = at(ts);
  const inCore = Math.hypot(mx - nx, my - ny) < R;
  const last = EPOCHS.filter((e) => e.t <= ts + 0.3 && e.lab).pop();
  // the dot is in the SPHEREx years (inside the ring): the card and the ring light up, the strongest highlight of the path
  const nowHit = Math.abs(ts - NOW) < 3;
  const sub = Math.abs(ts - NOW) < 3 ? 'SPHEREx' : ts > 2027 ? 'the future' : last ? last.sub : '';
  return (
    <figure class="lp-live-fig">
      <div
        class="lp-live"
        role="img"
        aria-label="SPHEREx picture of 24 arcminutes of sky around Barnard's Star with its real path from 1950 to 2050: dots mark the dates of real sky images; the star moves north while every other star stays put."
      >
        <div class="lp-path-pic" style={{ backgroundImage: `url(${IMG}barnard_path_spherex_24arcmin.jpg)` }} />
        <svg class="lp-ov" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {/* the past solid up to the ring, the future dashed from it: nothing is drawn inside the saturated core */}
          <line x1={A[0]} y1={A[1]} x2={nx - U[0] * R} y2={ny - U[1] * R} stroke="#ffb454" stroke-width="0.45" />
          <line x1={nx + U[0] * R} y1={ny + U[1] * R} x2={B[0]} y2={B[1]} stroke="#ffb454" stroke-width="0.45" stroke-dasharray="1.2 1" />
          {EPOCHS.map((e) => {
            const [x, y] = at(e.t);
            return e.future ? (
              <circle key={e.t} cx={x} cy={y} r="0.9" fill="#04080c" stroke="#ffb454" stroke-width="0.4" />
            ) : (
              <circle key={e.t} cx={x} cy={y} r="0.75" fill="#ffb454" />
            );
          })}
          {nowHit && <circle cx={nx} cy={ny} r={R + 0.9} fill="none" stroke="#ffd9a0" stroke-width="1.4" opacity="0.35" />}
          <circle cx={nx} cy={ny} r={R} fill="none" stroke={nowHit ? '#ffe2b8' : '#ffb454'} stroke-width={nowHit ? 0.9 : 0.5} />
          {!inCore && ts > 1950.5 && <circle cx={mx} cy={my} r="1.1" fill="#fff" stroke="#ffb454" stroke-width="0.4" />}
        </svg>
        {EPOCHS.filter((e) => e.lab).map((e) => {
          const [x, y] = at(e.t);
          return (
            <span key={e.t} class={`lp-plab ${Math.abs(e.t - ts) < 3 ? 'hit' : ''}`} style={{ left: `${x + 2.6}%`, top: `${y}%` }}>
              {e.lab} <small>{e.sub}</small>
            </span>
          );
        })}
        {/* the card stands on the side of the star with room for it (a narrow picture would cut it off at its right edge) */}
        <div
          class={`lp-pnow${nx > 48 ? ' flip' : ''}${nowHit ? ' hit' : ''}`}
          style={{ left: `${nx > 48 ? nx - R - 2.4 : nx + R + 2.4}%`, top: `${ny}%` }}
        >
          <b>Barnard's Star</b>seen by SPHEREx 2025–26
          <br />
          moves 10.4″ a year (Gaia)
        </div>
        <div class="lp-pyear">
          {Math.round(ts)}
          <small>{sub}</small>
        </div>
      </div>
      <figcaption class="lp-live-cap">
        <span>
          <b>The real path of Barnard's Star,</b> 1950 → 2050, drawn on SPHEREx
        </span>
        <span>24′ of sky</span>
      </figcaption>
    </figure>
  );
}

const BLINK = [
  { t: 1950.52, src: 'blink_1950_poss1.jpg', txt: '1950 · photographic plate', r: 3.6 },
  { t: 1991.46, src: 'blink_1991_poss2.jpg', txt: '1991 · photographic plate', r: 3.6 },
  { t: NOW, src: 'blink_2026_spherex.jpg', txt: '2026 · SPHEREx infrared', r: 5 },
];

function ThenAndNow() {
  const [i, setI] = useState(1);
  useEffect(() => {
    if (reducedMotion()) return;
    return visibleInterval(() => setI((v) => (v + 1) % 3), 1500);
  }, []);
  const b = BLINK[i]!;
  const [x, y] = at(b.t);
  return (
    <div
      class="lp-pic lp-then"
      role="img"
      aria-label="The same 24 arcminutes of sky in 1950, 1991 and 2026: Barnard's Star, ringed, walks north along its path; the other stars stay put."
    >
      {BLINK.map((f, j) => (
        <img key={f.src} src={IMG + f.src} alt="" class={j === i ? 'on' : ''} loading="lazy" />
      ))}
      <span class="lp-yr">{b.txt}</span>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {i > 0 && <line x1={A[0]} y1={A[1]} x2={x - U[0] * b.r} y2={y - U[1] * b.r} stroke="#ffb454" stroke-width="0.5" />}
        <line
          x1={x + U[0] * b.r}
          y1={y + U[1] * b.r}
          x2={B[0]}
          y2={B[1]}
          stroke="#ffb454"
          stroke-width="0.5"
          stroke-dasharray="1.2 1"
          opacity="0.7"
        />
        {BLINK.map((f, j) => {
          if (j === i) return null;
          const [px, py] = at(f.t);
          return <circle key={f.t} cx={px} cy={py} r="0.9" fill="#ffb454" />;
        })}
        <circle cx={x} cy={y} r={b.r} fill="none" stroke="#ffb454" stroke-width="0.6" />
      </svg>
    </div>
  );
}

function Blink({ frames, label, badge, ok }: { frames: string[]; label: string; badge: string; ok?: boolean }) {
  return (
    <div class="lp-pic lp-blink" role="img" aria-label={label}>
      {frames.map((f) => (
        <div key={f} style={{ backgroundImage: `url(${IMG}${f})` }} />
      ))}
      <span class={`lp-badge ${ok ? 'lp-ok' : ''}`}>{badge}</span>
    </div>
  );
}

function Video() {
  const [ok, setOk] = useState(true);
  return (
    <div class="lp-video">
      {ok ? (
        <video
          controls
          playsInline
          preload="metadata"
          poster="/demo/spherex-sky-demo.jpg"
          src={VIDEO}
          onError={() => setOk(false)}
          aria-label="30-second demo video of SPHEREx Space with a voice-over and captions"
        />
      ) : (
        <a class="lp-video-slot" href={APP} aria-label="Open SPHEREx Space">
          <span class="lp-play">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
          </span>
        </a>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ the page */

/** The latest discovery (GET /api/discoveries/latest, from the accounts): a finding confirmed as new, with its finder. Nothing is shown
 * while there is none. */
interface Discovery {
  finding_id: string;
  title: string;
  finder: string | null;
  summary: string | null;
  url: string | null;
  page: string | null;
  image: string | null;
}

function LatestDiscovery() {
  const [d, setD] = useState<Discovery | null>(null);
  useEffect(() => {
    let stop = false;
    void fetch('/api/discoveries/latest')
      .then(async (r) => (r.ok ? ((await r.json()) as { discovery?: Discovery | null }) : null))
      .then((b) => !stop && setD(b?.discovery ?? null))
      .catch(() => undefined);
    return () => {
      stop = true;
    };
  }, []);
  if (!d) return null;
  const href = d.page || `#/findings/${encodeURIComponent(d.finding_id)}`;
  return (
    <a class="lp-disc" href={href} data-testid="landing-discovery">
      {d.image && <img class="lp-disc-pic" src={d.image} alt="" loading="lazy" />}
      <span class="lp-disc-text">
        <span class="lp-disc-eyebrow">Latest discovery</span>
        <b>{d.title}</b>
        {d.finder && <span>Found by {d.finder}</span>}
      </span>
    </a>
  );
}

export function Landing() {
  const scroller = useRef<HTMLDivElement>(null);
  return (
    <div class="lp" ref={scroller} data-testid="landing">
      <SkyBackdrop scroller={() => scroller.current} />
      <header class="lp-top">
        <div class="lp-wrap">
          <a class="lp-brand" href={APP} aria-label="SPHEREx Space, open the app">
            <svg class="lp-mark" viewBox="0 0 32 32" aria-hidden="true">
              <circle cx="16" cy="16" r="13" fill="none" stroke="#5fe3d6" stroke-width="2" />
              <circle cx="16" cy="16" r="6.5" fill="none" stroke="#5fe3d6" stroke-width="1.5" opacity=".55" />
              <circle cx="21.5" cy="11" r="2.6" fill="#ffb454" />
            </svg>
            SPHEREx Space <small>by Good Karma Lab</small>
          </a>
          <nav class="lp-nav" aria-label="Sections">
            {SECTIONS.map(([id, label]) => (
              <a key={id} href={`#${id}`} onClick={jump(id)}>
                {label}
              </a>
            ))}
            <a href={REPO} target="_blank" rel="noopener noreferrer" data-testid="landing-source">
              Source code
            </a>
            <a class="lp-btn lp-btn-primary lp-btn-sm" href={START} data-testid="landing-open">
              Start searching
            </a>
            <NavMenu />
          </nav>
        </div>
      </header>

      <main>
        <section class="lp-hero" aria-labelledby="lp-h1">
          <div class="lp-wrap lp-hero-grid">
            <div>
              <p class="lp-eyebrow">Real images from NASA’s SPHEREx space telescope</p>
              <h1 id="lp-h1">
                Flip through the sky.
                <br />
                Spot <em>what moves</em>.
              </h1>
              <p class="lp-lede">
                Pluto was found by comparing two photographs of the same stars and noticing one dot that had shifted. Since 2025, SPHEREx
                has photographed the entire sky every six months in 102 colours of infrared light, far more than anyone can check alone.
                Pick any spot, play its images as a Time-lapse, and see what moved: nearby stars and brown dwarfs stepping across the sky,
                worlds beyond Neptune, maybe a planet nobody has found yet.
              </p>
              <div class="lp-cta">
                <a class="lp-btn lp-btn-primary lp-btn-lg" href={START} data-testid="landing-start">
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.2"
                    stroke-linecap="round"
                    aria-hidden="true"
                  >
                    <circle cx="11" cy="11" r="7" />
                    <path d="M16.5 16.5 21 21" />
                  </svg>
                  Start searching
                </a>
              </div>
              <ul class="lp-trust">
                <li>1.6 million real SPHEREx exposures</li>
                <li>Every mover checked against ESA Gaia</li>
                <li>
                  {/* one text run: in the flex item the link would be an item of its own, with the gap as a second space */}
                  <span>
                    Free, no account,{' '}
                    <a href={REPO} target="_blank" rel="noopener noreferrer">
                      open source
                    </a>
                  </span>
                </li>
              </ul>
              <p class="lp-or" data-testid="landing-more">
                Or first{' '}
                <a href={STAR} data-testid="landing-cta-star">
                  watch a star move
                </a>
                ,{' '}
                <a href={BROWN_DWARF} data-testid="landing-cta-dwarf">
                  see a brown dwarf move
                </a>
                , or{' '}
                <a href={APP} data-testid="landing-map">
                  open the sky map
                </a>
                .
              </p>
              <LatestDiscovery />
            </div>
            <PathHero />
          </div>
        </section>

        <section class="lp-sec lp-sec-tight" aria-labelledby="lp-vh">
          <div class="lp-wrap">
            <div class="lp-head">
              <div>
                <p class="lp-eyebrow">See it in 30 seconds</p>
                <h2 id="lp-vh">From the whole sky to one moving star.</h2>
              </div>
            </div>
            <Video />
          </div>
        </section>

        <section class="lp-sec" id="how" aria-labelledby="lp-howh">
          <div class="lp-wrap">
            <div class="lp-head">
              <div>
                <p class="lp-eyebrow">How it works</p>
                <h2 id="lp-howh">Three steps. No astronomy degree needed.</h2>
                <p class="lp-lede">
                  The app reads the real SPHEREx files from NASA’s public archive and turns them into pictures you can play, so change over
                  time is something you see, not something you calculate.
                </p>
              </div>
              <a class="lp-link" href={APP}>
                Try it now →
              </a>
            </div>
            <div class="lp-steps">
              <article class="lp-card">
                <div
                  class="lp-pic"
                  style={{ backgroundImage: `url(${IMG}sky_svs_milky_way.jpg)` }}
                  role="img"
                  aria-label="The Milky Way towards Sagittarius on NASA's Deep Star Maps, the sky behind this page"
                >
                  <span class="lp-badge">the sky behind this page · NASA SVS star map</span>
                </div>
                <div class="lp-body">
                  <span class="lp-n">01</span>
                  <h3>Pick a patch of sky</h3>
                  <p>
                    Spin the globe, type a star’s name or its coordinates, or start from our picks. Choose how much sky you see, from 2 to
                    60 arcminutes.
                  </p>
                </div>
              </article>
              <article class="lp-card">
                <Blink
                  frames={['barnard_5arcmin_aug25.jpg', 'barnard_5arcmin_mar26.jpg', 'barnard_5arcmin_aug26.jpg']}
                  label="Barnard's Star in three SPHEREx passes, shown one after another"
                  badge="3 passes · Aug 2025 – Aug 2026"
                />
                <div class="lp-body">
                  <span class="lp-n">02</span>
                  <h3>Play it as a Time-lapse</h3>
                  <p>
                    SPHEREx returns to every spot about every six months. Play the passes and nearby stars and brown dwarfs step between
                    them; the days inside a pass show asteroids too.
                  </p>
                </div>
              </article>
              <article class="lp-card">
                <ThenAndNow />
                <div class="lp-body">
                  <span class="lp-n">03</span>
                  <h3>Check what you see</h3>
                  <p>
                    Sky photographs from 1950 and 1991 line up with SPHEREx, so a slow mover shows its whole path. Every mover is checked
                    against ESA Gaia, and any dot gives its spectrum in 102 colours.
                  </p>
                </div>
              </article>
            </div>
            <div class="lp-numbers">
              <div>
                <b>1.6 million</b>
                <span>SPHEREx exposures indexed</span>
              </div>
              <div>
                <b>102</b>
                <span>colours of infrared, 0.75–5 µm</span>
              </div>
              <div>
                <b>6 months</b>
                <span>for SPHEREx to map the whole sky again</span>
              </div>
              <div>
                <b>1950 → 2026</b>
                <span>old sky plates lined up with SPHEREx</span>
              </div>
            </div>
          </div>
        </section>

        <section class="lp-sec lp-alt" id="finds" aria-labelledby="lp-fh">
          <div class="lp-wrap">
            <div class="lp-head">
              <div>
                <p class="lp-eyebrow">What you can find</p>
                <h2 id="lp-fh">What moves in the SPHEREx sky.</h2>
                <p class="lp-lede">
                  Nearby stars, brown dwarfs, comets, asteroids, even unknown planets give themselves away by shifting between images. Seen
                  in real SPHEREx data: a nearby star and a brown dwarf stepping between passes, a dwarf planet found by Find Planet X, and,
                  as an extra, an asteroid. The globe lists 2,000 brown dwarfs and 700 fast-moving stars more.
                </p>
              </div>
            </div>
            <div class="lp-finds">
              <a class="lp-find" href={STAR}>
                <Blink
                  frames={['barnard_5arcmin_aug25.jpg', 'barnard_5arcmin_mar26.jpg', 'barnard_5arcmin_aug26.jpg']}
                  label="Barnard's Star moving between passes"
                  badge="Gaia: same motion"
                  ok
                />
                <div class="lp-body">
                  <span class="lp-kind">Nearby star</span>
                  <h3>Barnard's Star</h3>
                  <p>
                    The fastest star in our sky, 6 light-years away. In a 20′ field three SPHEREx passes measure 10.7 ± 0.8″ a year; ESA
                    Gaia gives 10.4. (On a 5′ field the star's saturated core pulls the measure to 11.4 ± 0.5, and the lab says so.)
                  </p>
                  <span class="lp-go">Watch it move →</span>
                </div>
              </a>
              <a class="lp-find" href={BROWN_DWARF}>
                <Blink
                  frames={['epsindi_b_apr25.jpg', 'epsindi_b_apr26.jpg', 'epsindi_b_sep26.jpg']}
                  label="The brown dwarf epsilon Indi B, orange-red, in three SPHEREx passes: it shifts down and to the left among white stars"
                  badge="Gaia: same motion"
                  ok
                />
                <div class="lp-body">
                  <span class="lp-kind">Brown dwarf</span>
                  <h3>ε Indi B</h3>
                  <p>
                    Two brown dwarfs, 12 light-years away: too heavy to be planets, too light to shine like stars. The orange-red dot
                    crosses the white stars at 4.7″ a year.
                  </p>
                  <span class="lp-go">See it move in the lab →</span>
                </div>
              </a>
              <a class="lp-find" href={ERIS}>
                <div
                  class="lp-pic"
                  style={{ backgroundImage: `url(${IMG}eris_deep_sum_97au.jpg)` }}
                  role="img"
                  aria-label="A faint orange dot in a noisy field: Eris coming into focus when its exposures are added along its path"
                >
                  <span class="lp-badge">Found blind · 3 passes</span>
                </div>
                <div class="lp-body">
                  <span class="lp-kind">Beyond Neptune</span>
                  <h3>Eris, the dwarf planet</h3>
                  <p>
                    Too faint for one picture. Add the exposures along its slow path and it comes into focus; linking three passes puts it
                    95.6 ± 0.4 au from the Sun. A planet far beyond Neptune would be found the same way.
                  </p>
                  <span class="lp-go">Run Find Planet X →</span>
                </div>
              </a>
              <a class="lp-find" href={APP}>
                <div
                  class="lp-pic lp-hop"
                  style={{ backgroundImage: `url(${IMG}asteroid_9078_orwell_visits.jpg)` }}
                  role="img"
                  aria-label="Five visits on one day, centred on the asteroid 9078 Orwell: the masked background stars slide past it"
                >
                  <span class="lp-badge">5 visits · one day</span>
                </div>
                <div class="lp-body">
                  <span class="lp-kind">Also: asteroids</span>
                  <h3>9078 Orwell</h3>
                  <p>A known asteroid, caught in five SPHEREx visits on one day. The stars stand still; the asteroid hops.</p>
                  <span class="lp-go">Open the globe →</span>
                </div>
              </a>
            </div>
          </div>
        </section>

        <section class="lp-explore" aria-labelledby="lp-eh">
          <div class="lp-wrap">
            <p class="lp-eyebrow">Why your eyes matter</p>
            <h2 id="lp-eh">Someone has to be the first to look.</h2>
            <p class="lp-lede">
              Volunteers comparing sky images by eye have found asteroids, comets and many brown dwarfs, in projects like Backyard Worlds:
              Planet 9. SPHEREx is a new sky to search, in 102 colours. Start from the globe, or click any empty spot.
            </p>
            <a class="lp-btn lp-btn-primary" href={START}>
              Start searching
            </a>
          </div>
        </section>

        <section class="lp-sec" id="team" aria-labelledby="lp-th">
          <div class="lp-wrap lp-team">
            <div>
              <p class="lp-eyebrow">The team</p>
              <h2 id="lp-th">Good Karma Lab</h2>
              <p class="lp-lede">
                We bring 15+ years of software engineering and architecture experience, with expertise in AI, autonomous agents, advanced
                information retrieval, and delivering solutions from concept to production.
              </p>
              <p class="lp-text">
                Our strengths combine hands-on development with technical leadership: designing scalable systems, building AI workflows,
                connecting complex data, and creating reliable, usable applications. Our experience spans aerospace, healthcare, banking,
                and other demanding industries.
              </p>
            </div>
            <div class="lp-members">
              <a class="lp-member" href="https://www.linkedin.com/in/alexanderostapenko/" target="_blank" rel="noopener noreferrer">
                <span class="lp-avatar" aria-hidden="true">
                  AO
                </span>
                <span>
                  <b>Aleksandr Ostapenko</b>
                  <span class="lp-role">Team owner</span>
                  <span class="lp-li">LinkedIn ↗</span>
                </span>
              </a>
              <a class="lp-member" href="https://www.linkedin.com/in/katherinekarmatskikh/" target="_blank" rel="noopener noreferrer">
                <span class="lp-avatar" aria-hidden="true">
                  KK
                </span>
                <span>
                  <b>Katherine Karmatskikh</b>
                  <span class="lp-role">Team member</span>
                  <span class="lp-li">LinkedIn ↗</span>
                </span>
              </a>
            </div>
          </div>
        </section>

        <section class="lp-sec lp-alt" id="challenge" aria-labelledby="lp-ch">
          <div class="lp-wrap lp-challenge">
            <div>
              <p class="lp-eyebrow">Made for</p>
              <h2 id="lp-ch">NASA Space Apps Challenge 2026</h2>
              <p class="lp-lede">
                The world's largest annual hackathon for space and Earth science. We took part at the Barcelona Local Event and built
                SPHEREx Space for the challenge “Planet X and SPHEREx”.
              </p>
              <dl class="lp-facts">
                <dt>Event</dt>
                <dd>NASA Space Apps Challenge 2026</dd>
                <dt>Location</dt>
                <dd>Barcelona Local Event</dd>
                <dt>Challenge</dt>
                <dd>Planet X and SPHEREx</dd>
                <dt>Team</dt>
                <dd>Good Karma Lab</dd>
                <dt>Code</dt>
                <dd>
                  <a href={REPO} target="_blank" rel="noopener noreferrer">
                    github.com/Good-karma-lab/Planet-X-SPHEREx
                  </a>{' '}
                  (Apache-2.0)
                </dd>
              </dl>
            </div>
            <div class="lp-brief">
              <p class="lp-brief-h">The challenge: Planet X and SPHEREx</p>
              <blockquote>
                Since 2025, NASA’s SPHEREx mission has been mapping the entire sky every six months in 102 bands of near-infrared light,
                returning images of more than a billion objects. Comets, asteroids, stars, brown dwarfs, and even new planets reveal
                themselves by shifting position between images, but no one person can sift through all that data alone. Your challenge is to
                create a public-facing web tool to display images of the sky from SPHEREx mission, making it easy for anyone to quickly see
                how they change over time.
              </blockquote>
              <p class="lp-brief-h">Our answer, point by point</p>
              <ul class="lp-checks">
                <li>
                  <b>Coordinates in, a little video out:</b> type RA and Dec, or a name, and the images of that patch play as a Time-lapse.
                  The address keeps them, so any view can be shared or embedded.
                </li>
                <li>
                  <b>Easy to see change:</b> blink, add or subtract passes, choose the field of view; nearby stars and brown dwarfs step
                  between passes, checked against ESA Gaia.
                </li>
                <li>
                  <b>Moving objects found for you:</b> movers between passes, Find Planet X for bodies beyond Neptune, and asteroids inside
                  a pass.
                </li>
                <li>
                  <b>102 bands made usable:</b> plain-named colours (water, methane, CO₂…), each detector added up on its own, and a
                  spectrum for any dot, measured on the fly.
                </li>
              </ul>
              <a class="lp-link" href={APP}>
                Open the app →
              </a>
            </div>
          </div>
        </section>
      </main>

      <footer class="lp-foot">
        <div class="lp-wrap">
          <div class="lp-cols">
            <div>
              <a class="lp-brand" href={APP}>
                SPHEREx Space
              </a>
              <p>
                A free public tool by Good Karma Lab. Independent project, not affiliated with or endorsed by NASA, JPL, Caltech or ESA.
              </p>
              <p>
                <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a>
              </p>
            </div>
            <div>
              <h4>Data and credits</h4>
              <ul>
                <li>SPHEREx Level-2 images: NASA / JPL-Caltech, via NASA/IPAC IRSA</li>
                <li>Star motions: ESA Gaia DR3 (ESA/Gaia/DPAC)</li>
                <li>Object names: SIMBAD, CDS Strasbourg</li>
                <li>Known asteroids: JPL Small-Body Identification, JPL Horizons</li>
                <li>Sky behind this page: NASA/Goddard Space Flight Center Scientific Visualization Studio. Gaia DR2: ESA/Gaia/DPAC.</li>
                <li>Globe layers: 2MASS, DSS2, AllWISE, IRIS (HiPS, CDS / IRSA)</li>
              </ul>
            </div>
            <div>
              <h4>The project</h4>
              <ul>
                <li>
                  <a href={APP}>Open the app</a>
                </li>
                <li>
                  <a href={REPO} target="_blank" rel="noopener noreferrer">
                    Source code
                  </a>
                </li>
                <li>
                  <a href="#challenge" onClick={jump('challenge')}>
                    NASA Space Apps 2026
                  </a>
                </li>
              </ul>
            </div>
          </div>
          <p class="lp-ack">
            This site makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices
            Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is
            funded by the National Aeronautics and Space Administration.
          </p>
          <div class="lp-legal">
            <span>Code: Apache-2.0 · Icons: Phosphor (MIT)</span>
            <span>Pictures: real SPHEREx data captured from the app; 1950 and 1991 plates from the Digitized Sky Surveys (STScI)</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
