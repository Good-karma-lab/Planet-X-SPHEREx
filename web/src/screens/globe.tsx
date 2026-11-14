import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { aim, type AimState } from '../lib/aim';
import { ang2pix, faceXY, outline } from '../lib/healpix';
import { formatHash } from '../lib/route';
import { atEpoch, D2R, formatDec, formatRA, parseDec, parseRA, separation, SURVEY_EPOCH } from '../lib/sky';
import { icon } from '../ui/icons';

interface Obj {
  n: string;
  ra: number;
  dec: number;
  k: 'bd' | 'fast' | 'mira' | 'young' | 'var';
  pmra: number | null;
  pmdec: number | null;
  sp: string;
  c: string;
  b?: number;
}

const KIND: Record<string, { cls: string; dot: string }> = {
  bd: { cls: 'orange', dot: '#ff9a5a' },
  fast: { cls: 'yellow', dot: '#ffe066' },
  mira: { cls: 'purple', dot: '#c9a2ff' },
  var: { cls: 'purple', dot: '#c9a2ff' },
  young: { cls: 'blue', dot: '#6ad0ff' },
};
const TARGET_CLASS: Record<string, string> = {
  "Barnard's Star": 'Red dwarf',
  'ε Indi B': 'T dwarf binary',
  'WISE 0855−0714': 'Y dwarf',
  'RW Aurigae': 'Young variable (T Tauri)',
  'R Draconis': 'Mira variable',
};

const SURVEYS = [
  { id: 'dss2', name: 'Visible — DSS2', max: 9, gain: 2.4, black: 16, note: 'Digitized Sky Survey: red, blue and infrared photographic plates, colour' },
  { id: '2mass', name: 'Near-infrared — 2MASS', max: 9, gain: 2.2, black: 0, note: '2MASS J, H and Ks (1.2, 1.7, 2.2 µm), colour' },
  { id: 'wise', name: 'Infrared — WISE', max: 8, gain: 2.4, black: 0, note: 'AllWISE W1, W2 and W4 (3.4, 4.6, 22 µm), colour' },
  { id: 'dust', name: 'Dust map', max: 3, gain: 1.6, black: 0, note: 'IRAS far infrared (IRIS: 12, 60, 100 µm): the dust of the Galaxy, colour' },
] as const;
type SurveyId = (typeof SURVEYS)[number]['id'];
const SIZES = [2, 5, 10, 20, 60];

/** Tiles of the all-sky surveys, read through the service and decoded to pixels once. */
const tiles = (() => {
  const cache = new Map<string, { w: number; h: number; d: Uint8ClampedArray } | null>();
  const pending = new Set<string>();
  let onTile = () => {};
  const load = (key: string, url: string, soft: number) => {
    if (cache.has(key) || pending.has(key)) return;
    pending.add(key);
    const im = new Image();
    im.onload = () => {
      pending.delete(key);
      const c = document.createElement('canvas');
      c.width = im.width;
      c.height = im.height;
      const x = c.getContext('2d', { willReadFrequently: true })!;
      if (soft) x.filter = `blur(${soft}px)`;
      x.drawImage(im, 0, 0);
      cache.set(key, { w: im.width, h: im.height, d: x.getImageData(0, 0, im.width, im.height).data });
      if (cache.size > 180) for (const k of cache.keys()) if (!k.endsWith('/allsky')) { cache.delete(k); break; }
      onTile();
    };
    im.onerror = () => {
      pending.delete(key);
      cache.set(key, null);
    };
    im.src = url;
  };
  const px = (t: { w: number; d: Uint8ClampedArray }, row: number, col: number): [number, number, number] => {
    const q = (row * t.w + col) * 4;
    return [t.d[q]!, t.d[q + 1]!, t.d[q + 2]!];
  };
  /** Colour of a survey at a position, from the deepest tile at hand up to `order`; null until something has arrived. */
  function sample(id: SurveyId, ra: number, dec: number, order: number): [number, number, number] | null {
    for (let n = order; n >= 3 && order >= 3; n--) {
      const i = ang2pix(n, ra, dec);
      const key = `${id}/${n}/${i}`;
      const t = cache.get(key);
      if (t) {
        const bits = Math.round(Math.log2(t.w));
        const f = faceXY(n + bits, ra, dec);
        return px(t, f.ix & (t.w - 1), f.iy & (t.w - 1));
      }
      if (t === undefined && n === order) load(key, `/api/v1/hips/${id}/${n}/${i}`, 0);
    }
    const key = `${id}/3/allsky`;
    const a = cache.get(key);
    if (a === undefined) {
      load(key, `/api/v1/hips/${id}/3/allsky`, 0.9);
      return null;
    }
    if (!a) return null;
    const ts = Math.round(a.w / 27);
    const bits = Math.round(Math.log2(ts));
    const f = faceXY(3 + bits, ra, dec);
    const tile = f.face * 64 + ang2pixLocal(f.ix >> bits, f.iy >> bits);
    return px(a, Math.floor(tile / 27) * ts + (f.ix & (ts - 1)), (tile % 27) * ts + (f.iy & (ts - 1)));
  }
  return { sample, setOnTile: (f: () => void) => (onTile = f) };
})();

/** Nested index of (x, y) inside a face at order 3. */
function ang2pixLocal(x: number, y: number): number {
  let r = 0;
  for (let b = 0; b < 3; b++) r += (((x >> b) & 1) << (2 * b)) + (((y >> b) & 1) << (2 * b + 1));
  return r;
}

function mollweideTheta(phi: number): number {
  let th = phi;
  for (let i = 0; i < 12; i++) {
    const d = 2 + 2 * Math.cos(2 * th);
    if (Math.abs(d) < 1e-9) break;
    th -= (2 * th + Math.sin(2 * th) - Math.PI * Math.sin(phi)) / d;
  }
  return th;
}

function openLab(ra: number, dec: number, fov: number) {
  location.hash = formatHash({ screen: 'lab', params: { ra: ra.toFixed(5), dec: dec.toFixed(5), fov: String(fov) } });
}

const epochPlace = (o: Obj): [number, number] => (o.pmra != null && o.pmdec != null ? atEpoch(o.ra, o.dec, o.pmra, o.pmdec, SURVEY_EPOCH) : [o.ra, o.dec]);

export function Globe() {
  const [catalog, setCatalog] = useState<Obj[]>([]);
  const [a, setA] = useState<AimState>(aim.get());
  const [survey, setSurvey] = useState<SurveyId>('dss2');
  const [proj, setProj] = useState<'globe' | 'moll'>('globe');
  const [covOn, setCovOn] = useState(false);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [per, setPer] = useState(25);
  const [sel, setSel] = useState<Obj | null>(null);
  const [count, setCount] = useState<{ n: number; by: Record<string, number> } | null>(null);
  const [raIn, setRaIn] = useState(formatRA(a.ra));
  const [decIn, setDecIn] = useState(formatDec(a.dec));
  const cv = useRef<HTMLCanvasElement>(null);
  const live = useRef({ survey, proj, covOn, a, sel, catalog, cov: null as null | { pts: [number, number][]; dr1: boolean; t: number }[] });
  live.current = { ...live.current, survey, proj, covOn, a, sel, catalog };
  const dirty = useRef(true);
  const view = useRef({ ra: a.ra, dec: a.dec, zoom: 1.6 });

  useEffect(() => {
    void fetch('/catalog.json')
      .then((r) => r.json())
      .then((d: { catalog: Obj[] }) => {
        // the test targets first, nearest to the coldest last
        const first = ["Barnard's Star", 'WISE 0855−0714', 'ε Indi B', 'RW Aurigae', 'R Draconis'];
        const rank = (o: Obj) => (o.b ? first.indexOf(o.n) : first.length);
        setCatalog([...d.catalog].sort((x, y) => rank(x) - rank(y)));
      });
    void fetch('/api/v1/coverage/map')
      .then((r) => (r.ok ? r.json() : null))
      .then((m: { order: number; releases: string[]; cells: Record<string, number[]> } | null) => {
        if (!m) return;
        let max = 1;
        for (const v of Object.values(m.cells)) max = Math.max(max, v.reduce((s, x) => s + x, 0));
        const di = m.releases.indexOf('dr1');
        live.current.cov = Object.entries(m.cells).map(([k, v]) => ({ pts: outline(m.order, Number(k), 4), dr1: di >= 0 && v[di]! > 0, t: Math.log(1 + v.reduce((s, x) => s + x, 0)) / Math.log(1 + max) }));
        dirty.current = true;
      })
      .catch(() => {});
    return aim.on((next, source) => {
      setA(next);
      if (source !== 'input') {
        setRaIn(formatRA(next.ra));
        setDecIn(formatDec(next.dec));
      }
      dirty.current = true;
    });
  }, []);

  // exposures under the aim frame, from the index
  useEffect(() => {
    setCount(null);
    const ctl = new AbortController();
    const t = setTimeout(() => {
      void fetch(`/api/v1/coverage?ra=${a.ra.toFixed(5)}&dec=${a.dec.toFixed(5)}&fov=${a.fov}`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((c: { n_exposures: number; by_release: Record<string, number> } | null) => c && setCount({ n: c.n_exposures, by: c.by_release }))
        .catch(() => {});
    }, 350);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [a.ra, a.dec, a.fov]);

  useEffect(() => {
    dirty.current = true;
  }, [survey, proj, covOn, sel, catalog]);

  // the sky canvas
  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    const ctx = c.getContext('2d')!;
    let W = 10;
    let H = 10;
    let dpr = 1;
    let raf = 0;
    let rest = 0;
    let lastSig = '';
    const resize = () => {
      const r = c.getBoundingClientRect();
      dpr = Math.min(2, devicePixelRatio || 1);
      W = Math.max(10, r.width);
      H = Math.max(10, r.height);
      c.width = W * dpr;
      c.height = H * dpr;
      dirty.current = true;
    };
    const ro = new ResizeObserver(resize);
    ro.observe(c.parentElement!);
    resize();
    tiles.setOnTile(() => (dirty.current = true));

    const R = () => (Math.min(W, H) / 2) * 0.9 * view.current.zoom;
    const mw = () => Math.min(W - 20, (H - 20) * 2);
    const basis = () => {
      const ra = view.current.ra * D2R;
      const de = view.current.dec * D2R;
      return {
        c: [Math.cos(de) * Math.cos(ra), Math.cos(de) * Math.sin(ra), Math.sin(de)],
        e: [-Math.sin(ra), Math.cos(ra), 0],
        n: [-Math.sin(de) * Math.cos(ra), -Math.sin(de) * Math.sin(ra), Math.cos(de)],
      };
    };
    type B = ReturnType<typeof basis>;
    const project = (ra: number, dec: number, b: B): [number, number, boolean] => {
      if (live.current.proj === 'moll') {
        const th = mollweideTheta(dec * D2R);
        const lam = ((((ra - 180 + 540) % 360) - 180) * D2R);
        return [W / 2 - ((lam * Math.cos(th)) / Math.PI) * (mw() / 2), H / 2 - Math.sin(th) * (mw() / 4), true];
      }
      const r = ra * D2R;
      const d = dec * D2R;
      const v = [Math.cos(d) * Math.cos(r), Math.cos(d) * Math.sin(r), Math.sin(d)];
      const z = v[0]! * b.c[0]! + v[1]! * b.c[1]! + v[2]! * b.c[2]!;
      if (z < 0.02) return [0, 0, false];
      return [W / 2 - (v[0]! * b.e[0]! + v[1]! * b.e[1]! + v[2]! * b.e[2]!) * R(), H / 2 - (v[0]! * b.n[0]! + v[1]! * b.n[1]! + v[2]! * b.n[2]!) * R(), true];
    };
    const unproject = (px: number, py: number): [number, number] | null => {
      if (live.current.proj === 'moll') {
        const X = (px - W / 2) / (mw() / 2);
        const Y = -(py - H / 2) / (mw() / 4);
        if (X * X + Y * Y > 1) return null;
        const th = Math.asin(Y);
        const phi = Math.asin((2 * th + Math.sin(2 * th)) / Math.PI);
        const lam = (Math.PI * X) / Math.cos(th);
        return [(((180 - lam / D2R) % 360) + 360) % 360, phi / D2R];
      }
      const sx = (W / 2 - px) / R();
      const sy = (H / 2 - py) / R();
      const rr = sx * sx + sy * sy;
      if (rr > 1) return null;
      const z = Math.sqrt(1 - rr);
      const b = basis();
      const v = [0, 1, 2].map((i) => z * b.c[i]! + sx * b.e[i]! + sy * b.n[i]!);
      return [(((Math.atan2(v[1]!, v[0]!) / D2R) % 360) + 360) % 360, Math.asin(Math.max(-1, Math.min(1, v[2]!))) / D2R];
    };

    const drawSky = () => {
      const st = live.current;
      const sig = [view.current.ra, view.current.dec, view.current.zoom, st.proj, W, H].join();
      const moving = sig !== lastSig;
      lastSig = sig;
      if (moving) {
        clearTimeout(rest);
        rest = window.setTimeout(() => (dirty.current = true), 220);
      }
      const s = moving ? 2 : 1;
      const sw = Math.ceil(W / s);
      const sh = Math.ceil(H / s);
      const img = ctx.createImageData(sw, sh);
      const o = img.data;
      const sv = SURVEYS.find((x) => x.id === st.survey)!;
      const order = st.proj === 'globe' && R() > 500 ? Math.max(3, Math.min(sv.max, Math.ceil(Math.log2(R() / 500)))) : 0;
      const b = basis();
      const Rs = R() / s;
      const g = (sv.gain * 255) / (255 - sv.black);
      for (let j = 0; j < sh; j++)
        for (let i = 0; i < sw; i++) {
          let ra = 0;
          let dec = 0;
          let ok = false;
          if (st.proj === 'moll') {
            const X = (i * s - W / 2) / (mw() / 2);
            const Y = -(j * s - H / 2) / (mw() / 4);
            if (X * X + Y * Y <= 1) {
              const th = Math.asin(Y);
              const phi = Math.asin((2 * th + Math.sin(2 * th)) / Math.PI);
              const lam = (Math.PI * X) / Math.cos(th);
              ra = (((180 - lam / D2R) % 360) + 360) % 360;
              dec = phi / D2R;
              ok = true;
            }
          } else {
            const sx = (sw / 2 - i) / Rs;
            const sy = (sh / 2 - j) / Rs;
            const rr = sx * sx + sy * sy;
            if (rr <= 1) {
              const z = Math.sqrt(1 - rr);
              const vx = z * b.c[0]! + sx * b.e[0]! + sy * b.n[0]!;
              const vy = z * b.c[1]! + sx * b.e[1]! + sy * b.n[1]!;
              const vz = z * b.c[2]! + sx * b.e[2]! + sy * b.n[2]!;
              ra = Math.atan2(vy, vx) / D2R;
              if (ra < 0) ra += 360;
              dec = Math.asin(Math.max(-1, Math.min(1, vz))) / D2R;
              ok = true;
            }
          }
          const p = (j * sw + i) * 4;
          if (!ok) continue;
          const col = tiles.sample(sv.id, ra, dec, order);
          o[p + 3] = 255;
          if (col) {
            o[p] = Math.max(0, col[0] - sv.black) * g;
            o[p + 1] = Math.max(0, col[1] - sv.black) * g;
            o[p + 2] = Math.max(0, col[2] - sv.black) * g;
          }
        }
      const tc = document.createElement('canvas');
      tc.width = sw;
      tc.height = sh;
      tc.getContext('2d')!.putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(tc, 0, 0, W, H);
    };

    const font = getComputedStyle(document.body).fontFamily;
    const draw = () => {
      const st = live.current;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      drawSky();
      const b = basis();
      // grid
      ctx.save();
      ctx.strokeStyle = 'rgba(255,255,255,.10)';
      ctx.lineWidth = 1;
      ctx.fillStyle = 'rgba(255,255,255,.45)';
      ctx.font = `11px ${font}`;
      const line = (pts: [number, number][]) => {
        ctx.beginPath();
        let pen = false;
        let lx = 0;
        for (const [ra, de] of pts) {
          const p = project(ra, de, b);
          if (!p[2] || (st.proj === 'moll' && pen && Math.abs(p[0] - lx) > W / 2)) {
            pen = false;
            if (!p[2]) continue;
          }
          if (pen) ctx.lineTo(p[0], p[1]);
          else ctx.moveTo(p[0], p[1]);
          pen = true;
          lx = p[0];
        }
        ctx.stroke();
      };
      for (let ra = 0; ra < 360; ra += 30) {
        const pts: [number, number][] = [];
        for (let de = -90; de <= 90; de += 3) pts.push([ra, de]);
        line(pts);
        const lp = project(ra, 2, b);
        if (lp[2]) ctx.fillText(`${ra / 15}h`, lp[0] + 3, lp[1] - 3);
      }
      for (let de = -60; de <= 60; de += 30) {
        const pts: [number, number][] = [];
        for (let ra = 0; ra <= 360; ra += 3) pts.push([ra, de]);
        line(pts);
        const lp = project(st.proj === 'moll' ? 170 : view.current.ra, de, b);
        if (lp[2]) ctx.fillText(`${de > 0 ? '+' : ''}${de}°`, lp[0] + 3, lp[1] - 3);
      }
      ctx.restore();
      ctx.strokeStyle = 'rgba(255,255,255,.22)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      if (st.proj === 'globe') ctx.arc(W / 2, H / 2, R(), 0, 7);
      else ctx.ellipse(W / 2, H / 2, mw() / 2, mw() / 4, 0, 0, 7);
      ctx.stroke();
      // coverage
      if (st.covOn && st.cov) {
        for (const cell of st.cov) {
          const pts = cell.pts.map(([ra, de]) => project(ra, de, b));
          if (st.proj === 'globe' && pts.some((q) => !q[2])) continue;
          if (st.proj === 'moll') {
            const xs = pts.map((q) => q[0]);
            if (Math.max(...xs) - Math.min(...xs) > W / 3) continue;
          }
          ctx.fillStyle = `hsla(${cell.dr1 ? 175 : 205},80%,60%,${0.08 + 0.3 * cell.t})`;
          ctx.beginPath();
          pts.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])));
          ctx.closePath();
          ctx.fill();
        }
      }
      // catalogue dots
      ctx.globalAlpha = 0.75;
      for (const o of st.catalog) {
        if (o.b) continue;
        const p = project(o.ra, o.dec, b);
        if (!p[2]) continue;
        ctx.fillStyle = KIND[o.k]?.dot ?? '#fff';
        ctx.fillRect(p[0] - 1, p[1] - 1, 2.2, 2.2);
      }
      ctx.globalAlpha = 1;
      // test targets
      ctx.font = `600 12.5px ${font}`;
      for (const o of st.catalog) {
        if (!o.b) continue;
        const [ra, dec] = epochPlace(o);
        const p = project(ra, dec, b);
        if (!p[2]) continue;
        const near = st.proj === 'globe' && view.current.zoom > 30;
        const lx = near ? 14 : 9;
        ctx.strokeStyle = '#000';
        ctx.fillStyle = '#ffb454';
        if (near) {
          ctx.lineWidth = 4;
          ctx.beginPath();
          ctx.arc(p[0], p[1], 9, 0, 7);
          ctx.stroke();
          ctx.strokeStyle = '#ffb454';
          ctx.lineWidth = 2;
          ctx.stroke();
          ctx.strokeStyle = '#000';
        } else {
          ctx.lineWidth = 3;
          ctx.beginPath();
          ctx.arc(p[0], p[1], 5, 0, 7);
          ctx.fill();
          ctx.stroke();
        }
        if (view.current.zoom > 1.5 || st.proj === 'moll' || o === st.sel) {
          ctx.lineWidth = 3;
          ctx.strokeText(o.n, p[0] + lx, p[1] + 4);
          ctx.fillStyle = '#fff';
          ctx.fillText(o.n, p[0] + lx, p[1] + 4);
        }
      }
      // the selected object's path: J2000 place moved by its proper motion, 2000 → 2050, SPHEREx years in green
      if (st.sel && st.sel.pmra != null && st.sel.pmdec != null && Math.hypot(st.sel.pmra, st.sel.pmdec) > 20) {
        const o = st.sel;
        const P = (y: number) => project(...atEpoch(o.ra, o.dec, o.pmra!, o.pmdec!, y), b);
        const p25 = P(2025.5);
        if (p25[2]) {
          const a0 = P(2000);
          const a1 = P(2050);
          const len = Math.hypot(a1[0] - a0[0], a1[1] - a0[1]);
          const m = len < 60 && len > 0 ? 60 / len : 1;
          const at = (y: number) => {
            const q = P(y);
            return [p25[0] + m * (q[0] - p25[0]), p25[1] + m * (q[1] - p25[1])] as const;
          };
          ctx.save();
          ctx.strokeStyle = '#ffb454';
          ctx.lineWidth = 3;
          ctx.setLineDash([5, 4]);
          ctx.beginPath();
          for (let y = 2000; y <= 2050; y += 2.5) {
            const q = at(y);
            if (y === 2000) ctx.moveTo(q[0], q[1]);
            else ctx.lineTo(q[0], q[1]);
          }
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.strokeStyle = '#6aff9e';
          ctx.lineWidth = 4;
          ctx.beginPath();
          const s0 = at(2025.3);
          const s1 = at(2026.7);
          ctx.moveTo(s0[0], s0[1]);
          ctx.lineTo(s1[0], s1[1]);
          ctx.stroke();
          ctx.font = `600 11px ${font}`;
          ctx.lineWidth = 3;
          for (const [y, t, right] of [[2000, '2000', false], [2050, '2050', true]] as const) {
            const q = at(y);
            ctx.fillStyle = '#ffb454';
            ctx.beginPath();
            ctx.arc(q[0], q[1], 3, 0, 7);
            ctx.fill();
            ctx.textAlign = right ? 'left' : 'right';
            ctx.strokeStyle = '#000';
            ctx.fillStyle = '#fff';
            ctx.strokeText(t, q[0] + (right ? 7 : -8), q[1] + 4);
            ctx.fillText(t, q[0] + (right ? 7 : -8), q[1] + 4);
          }
          ctx.textAlign = 'left';
          ctx.fillStyle = '#6aff9e';
          ctx.strokeText('SPHEREx 2025–26', p25[0] + 8, p25[1] + 16);
          ctx.fillText('SPHEREx 2025–26', p25[0] + 8, p25[1] + 16);
          if (m > 1) {
            ctx.fillStyle = '#9fb6c0';
            const t = `path 2000→2050, drawn ×${m.toFixed(0)} larger than true`;
            ctx.strokeText(t, p25[0] + 8, p25[1] + 30);
            ctx.fillText(t, p25[0] + 8, p25[1] + 30);
          }
          ctx.strokeStyle = '#6aff9e';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(p25[0], p25[1], 9, 0, 7);
          ctx.stroke();
          ctx.restore();
        }
      }
      // aim frame
      const p = project(st.a.ra, st.a.dec, b);
      if (p[2]) {
        const sky = st.a.fov / 60;
        const truePx = st.proj === 'globe' ? R() * sky * D2R : (mw() / 360) * sky;
        const s = Math.max(truePx, 26);
        ctx.save();
        ctx.translate(p[0], p[1]);
        ctx.strokeStyle = '#6aaaff';
        ctx.lineWidth = 2;
        ctx.shadowColor = '#000';
        ctx.shadowBlur = 6;
        ctx.strokeRect(-s / 2, -s / 2, s, s);
        ctx.beginPath();
        ctx.moveTo(-s, 0);
        ctx.lineTo(-s / 2 - 4, 0);
        ctx.moveTo(s, 0);
        ctx.lineTo(s / 2 + 4, 0);
        ctx.moveTo(0, -s);
        ctx.lineTo(0, -s / 2 - 4);
        ctx.moveTo(0, s);
        ctx.lineTo(0, s / 2 + 4);
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#6aaaff';
        ctx.font = `700 12px ${font}`;
        ctx.fillText(`${st.a.fov}′`, s / 2 + 6, -s / 2 - 4);
        ctx.restore();
        frame = { x: p[0], y: p[1], s };
      } else frame = null;
    };
    let frame: { x: number; y: number; s: number } | null = null;
    let anim: { t0: number; ra0: number; dra: number; de0: number; dde: number } | null = null;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      if (anim) {
        const t = Math.min(1, (performance.now() - anim.t0) / 900);
        const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
        view.current.ra = (((anim.ra0 + anim.dra * e) % 360) + 360) % 360;
        view.current.dec = anim.de0 + anim.dde * e;
        if (t >= 1) anim = null;
        dirty.current = true;
      }
      if (dirty.current) {
        dirty.current = false;
        draw();
      }
    };
    loop();
    const flyTo = (ra: number, dec: number) => {
      if (live.current.proj === 'moll') return;
      const th = (separation(ra, dec, view.current.ra, view.current.dec) * D2R);
      if (th < (Math.PI / 2) * 0.9 && R() * Math.sin(th) < Math.min(W, H) * 0.3) return;
      anim = { t0: performance.now(), ra0: view.current.ra, dra: ((ra - view.current.ra + 540) % 360) - 180, de0: view.current.dec, dde: dec - view.current.dec };
    };
    flyRef.current = flyTo;

    let drag: { x: number; y: number; ra: number; dec: number; moved: boolean } | null = null;
    c.onpointerdown = (e) => {
      c.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, ra: view.current.ra, dec: view.current.dec, moved: false };
      anim = null;
    };
    c.onpointermove = (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved && live.current.proj === 'globe') {
        const k = (90 / R()) * 0.9;
        view.current.ra = (((drag.ra + ((dx * k) / Math.max(0.2, Math.cos(view.current.dec * D2R))) * 0.6) % 360) + 360) % 360;
        view.current.dec = Math.max(-89, Math.min(89, drag.dec + dy * k));
        dirty.current = true;
      }
    };
    c.onpointerup = (e) => {
      const r = c.getBoundingClientRect();
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      if (drag && !drag.moved) {
        const b = basis();
        let near: Obj | null = null;
        let bd = 12;
        for (const o of live.current.catalog) {
          const p = o.b ? project(...epochPlace(o), b) : project(o.ra, o.dec, b);
          if (!p[2]) continue;
          const d = Math.hypot(p[0] - px, p[1] - py);
          if (d < bd && (o.b || view.current.zoom > 3)) {
            bd = d;
            near = o;
          }
        }
        if (near) {
          setSel(near);
          const [ra, dec] = epochPlace(near);
          aim.set({ ra, dec }, 'map');
        } else {
          const s = unproject(px, py);
          if (s) {
            setSel(null);
            aim.set({ ra: s[0], dec: s[1] }, 'map');
          }
        }
      }
      drag = null;
    };
    c.ondblclick = (e) => {
      const r = c.getBoundingClientRect();
      const f = frame;
      if (f && Math.abs(e.clientX - r.left - f.x) < f.s && Math.abs(e.clientY - r.top - f.y) < f.s) openLab(live.current.a.ra, live.current.a.dec, live.current.a.fov);
    };
    c.onwheel = (e) => {
      e.preventDefault();
      if (live.current.proj !== 'globe') return;
      view.current.zoom = Math.max(0.8, Math.min(400, view.current.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      dirty.current = true;
    };
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      clearTimeout(rest);
    };
  }, []);
  const flyRef = useRef<(ra: number, dec: number) => void>(() => {});

  const list = useMemo(() => {
    let L = catalog;
    if (filter === 'bench') L = L.filter((o) => o.b);
    else if (filter === 'bd') L = L.filter((o) => o.k === 'bd');
    else if (filter === 'fast') L = L.filter((o) => o.k === 'fast');
    else if (filter === 'var') L = L.filter((o) => o.k === 'mira' || o.k === 'young' || o.k === 'var');
    const q = query.trim().toLowerCase();
    if (q) {
      const m = q.match(/^(.+?)[,;]\s*(.+)$/);
      const ra = m ? parseRA(m[1]!) : null;
      const de = m ? parseDec(m[2]!) : null;
      if (ra !== null && de !== null) L = [...L].sort((x, y) => separation(x.ra, x.dec, ra, de) - separation(y.ra, y.dec, ra, de)).slice(0, 200);
      else L = L.filter((o) => o.n.toLowerCase().includes(q) || o.c.toLowerCase().includes(q) || o.sp.toLowerCase().includes(q));
    }
    return L;
  }, [catalog, filter, query]);
  const pages = Math.max(1, Math.ceil(list.length / per));
  const pg = Math.min(page, pages);
  const slice = list.slice((pg - 1) * per, pg * per);
  const nBench = catalog.filter((o) => o.b).length;

  const select = (o: Obj) => {
    setSel(o);
    const [ra, dec] = epochPlace(o);
    aim.set({ ra, dec }, 'catalog');
    flyRef.current(ra, dec);
  };
  const openObj = (o: Obj) => {
    const [ra, dec] = epochPlace(o);
    aim.set({ ra, dec }, 'catalog');
    openLab(ra, dec, a.fov);
  };
  const goAim = () => {
    const ra = parseRA(raIn);
    const dec = parseDec(decIn);
    if (ra === null || dec === null) return;
    setSel(null);
    aim.set({ ra, dec }, 'input');
    flyRef.current(ra, dec);
  };
  const motion = (o: Obj) => {
    if (o.k === 'mira' || o.k === 'var') return 'Variable';
    if (o.pmra == null || o.pmdec == null) return 'Motion: —';
    const pm = Math.hypot(o.pmra, o.pmdec);
    return `Motion: ${pm >= 1000 ? `${(pm / 1000).toFixed(2)} ″/yr` : `${Math.round(pm)} mas/yr`}`;
  };
  const pagerNums = [...new Set([1, 2, 3, pg - 1, pg, pg + 1, pages])].filter((p) => p >= 1 && p <= pages).sort((x, y) => x - y);
  const sv = SURVEYS.find((x) => x.id === survey)!;

  return (
    <div class="screen">
      <div class="globe-wrap">
        <canvas ref={cv} aria-label="Sky globe" data-testid="sky" />
        <div class="map-ui">
          <div class="seg sm" role="group" aria-label="Sky background">
            {SURVEYS.map((s) => (
              <button key={s.id} class={s.id === survey ? 'on' : ''} title={s.note} onClick={() => setSurvey(s.id)}>
                {s.name}
              </button>
            ))}
          </div>
          <div class="seg" role="group" aria-label="Projection">
            <button class={proj === 'globe' ? 'on' : ''} onClick={() => setProj('globe')}>
              <span dangerouslySetInnerHTML={{ __html: icon('globe', 16) }} style={{ display: 'contents' }} />
              3D globe
            </button>
            <button class={proj === 'moll' ? 'on' : ''} onClick={() => setProj('moll')}>
              <span dangerouslySetInnerHTML={{ __html: icon('map-trifold', 16) }} style={{ display: 'contents' }} />
              Mollweide map
            </button>
          </div>
          <div class="map-read">
            Aim: <b>{formatRA(a.ra)}</b> · <b>{formatDec(a.dec)}</b> · {a.fov}′
            {count && (count.n ? <span style={{ color: 'var(--good)' }}> · {count.n.toLocaleString('en-US')} exposures (DR1 {count.by['dr1'] ?? 0}, QR3 {count.by['qr3'] ?? 0})</span> : <span class="faint"> · no SPHEREx exposures here</span>)}
            {sel && (
              <>
                {' · '}
                <button class="lnk" onClick={() => openObj(sel)} title="Open this field in the spectral lab">
                  Open {sel.n.length > 26 ? `${sel.n.slice(0, 24)}…` : sel.n} in the lab
                </button>
              </>
            )}
          </div>
        </div>
        <label class="chk covsw dark-glass" title="Where SPHEREx exposures exist, from the exposure index. Teal: DR1 exposures; blue: QR3 only; more opaque = more exposures (log scale)">
          <input type="checkbox" checked={covOn} onChange={(e) => setCovOn((e.target as HTMLInputElement).checked)} />
          Exposure coverage
          <span class="covkey">teal: DR1 exposures · blue: QR3 only · more opaque = more exposures (log scale)</span>
        </label>
        <div class="map-help" title={`Background: ${sv.note} · IRSA / CDS HiPS`}>
          <b>Drag</b> to rotate · <b>scroll</b> to zoom · <b>click</b> to aim · <b>double-click</b> the frame to open it in the lab. Orange ★ are test targets.
        </div>
        <button class="btn primary big map-open" onClick={() => (sel ? openObj(sel) : openLab(a.ra, a.dec, a.fov))}>
          <span dangerouslySetInnerHTML={{ __html: icon('scan', 18) }} style={{ display: 'contents' }} />
          Show area
        </button>
      </div>
      <aside class="catalog glass">
        <div class="cat-top">
          <section class="aim">
            <div class="coords">
              <div class="field">
                <label for="gRa">RA</label>
                <input id="gRa" class={`inp coord ${parseRA(raIn) === null ? 'bad' : ''}`} spellcheck={false} value={raIn} onInput={(e) => setRaIn((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === 'Enter' && goAim()} onBlur={goAim} />
              </div>
              <div class="field">
                <label for="gDec">Dec</label>
                <input id="gDec" class={`inp coord ${parseDec(decIn) === null ? 'bad' : ''}`} spellcheck={false} value={decIn} onInput={(e) => setDecIn((e.target as HTMLInputElement).value)} onKeyDown={(e) => e.key === 'Enter' && goAim()} onBlur={goAim} />
              </div>
            </div>
            <div class="field">
              <label>FOV</label>
              <div class="seg fov">
                {SIZES.map((s) => (
                  <button key={s} class={s === a.fov ? 'on' : ''} onClick={() => aim.set({ fov: s }, 'fov')} title={`${s}′ field`}>
                    {s}′
                  </button>
                ))}
              </div>
            </div>
          </section>
          <input class="inp search" placeholder="Search a name or coordinates" autocomplete="off" value={query} onInput={(e) => { setQuery((e.target as HTMLInputElement).value); setPage(1); }} />
          <div class="chips">
            {(
              [
                ['all', 'SIMBAD snapshot'],
                ['bench', `Test targets (${nBench})`],
                ['bd', 'Brown dwarfs'],
                ['fast', 'Fast stars'],
                ['var', 'Variables'],
              ] as const
            ).map(([k, l]) => (
              <button key={k} class={`chip ${filter === k ? 'on' : ''}`} onClick={() => { setFilter(k); setPage(1); }} data-testid={`chip-${k}`}>
                {l}
              </button>
            ))}
          </div>
        </div>
        <div class="cat-list">
          {!slice.length && catalog.length > 0 && <div class="empty">Not in the local SIMBAD snapshot. Coordinates work too, e.g. “17h57m48s, +04d41m”.</div>}
          {slice.map((o) => {
            const { ra, dec } = o;
            return (
              <div key={o.n} class={`row ${sel === o ? 'on' : ''}`} onClick={() => select(o)} onDblClick={() => openObj(o)} data-testid={o.b ? `target-${o.n}` : undefined}>
                <div class="nm">
                  {o.b ? <span title="Test target of this project">★</span> : null}
                  <span class="t" title={o.n}>
                    {o.n}
                  </span>
                  <span class={`badge ${KIND[o.k]?.cls ?? ''}`}>{o.b ? TARGET_CLASS[o.n] ?? o.c : o.c}</span>
                </div>
                <div class="co">
                  {formatRA(ra)} {formatDec(dec)}
                </div>
                <div class="mv">
                  <span>{motion(o)}</span>
                </div>
                <div class="gobtns">
                  <button class="btn sm primary go" title="Fly to the object on the map and draw its path" onClick={(e) => { e.stopPropagation(); select(o); }}>
                    <span dangerouslySetInnerHTML={{ __html: icon('target', 16) }} style={{ display: 'contents' }} />
                    Show on map
                  </button>
                  <button class="btn sm go" title="Open this field in the spectral lab" onClick={(e) => { e.stopPropagation(); openObj(o); }}>
                    <span dangerouslySetInnerHTML={{ __html: icon('chart-line-up', 16) }} style={{ display: 'contents' }} />
                    Open in lab
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        <div class="pager">
          <button class="pg" disabled={pg === 1} onClick={() => setPage(pg - 1)}>
            ‹
          </button>
          {pagerNums.map((p, i) => (
            <>
              {i > 0 && p - pagerNums[i - 1]! > 1 && <span class="faint">…</span>}
              <button key={p} class={`pg ${p === pg ? 'on' : ''}`} onClick={() => setPage(p)}>
                {p}
              </button>
            </>
          ))}
          <button class="pg" disabled={pg === pages} onClick={() => setPage(pg + 1)}>
            ›
          </button>
          <select title="Objects per page" value={String(per)} onChange={(e) => { setPer(Number((e.target as HTMLSelectElement).value)); setPage(1); }}>
            {[25, 50, 100].map((v) => (
              <option value={String(v)}>{v} / page</option>
            ))}
          </select>
          <span class="cnt">Total: {list.length.toLocaleString('en-US')}</span>
        </div>
      </aside>
    </div>
  );
}
