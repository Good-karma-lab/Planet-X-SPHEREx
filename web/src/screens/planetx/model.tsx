import { useEffect, useRef, useState } from 'preact/hooks';
import { FIELDS, P9, PLANETS, TNOS, type Elements } from './data';

const RAD = Math.PI / 180;
const MAXR = 1000;
const OBL = 23.4393 * RAD;

export type Layer = 'planets' | 'dwarfs' | 'cluster' | 'p9' | 'dirs';
export const LAYERS: { id: Layer; name: string; colour: string }[] = [
  { id: 'planets', name: 'Planets', colour: '#6fb3ff' },
  { id: 'dwarfs', name: 'Dwarf planets', colour: '#e3cf9d' },
  { id: 'cluster', name: 'Clustered distant objects', colour: '#c4a1ff' },
  { id: 'p9', name: 'Planet Nine (predicted)', colour: '#ff7a59' },
  { id: 'dirs', name: 'Our search directions', colour: '#5fe3d6' },
];

type V3 = [number, number, number];

/** a point on the orbit in ecliptic au from the eccentric anomaly E */
function pos(el: Elements, E: number): V3 {
  const x = el.a * (Math.cos(E) - el.e);
  const y = el.a * Math.sqrt(1 - el.e * el.e) * Math.sin(E);
  const w = el.w * RAD, o = el.om * RAD, i = el.i * RAD;
  const cw = Math.cos(w), sw = Math.sin(w), co = Math.cos(o), so = Math.sin(o), ci = Math.cos(i), si = Math.sin(i);
  const x1 = x * cw - y * sw;
  const y1 = x * sw + y * cw;
  return [x1 * co - y1 * ci * so, x1 * so + y1 * ci * co, y1 * si];
}

function kepler(M: number, e: number): number {
  let E = M + e * Math.sin(M);
  for (let k = 0; k < 8; k++) E -= (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E));
  return E;
}

const wrap = (x: number) => ((x % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);

function orbit(el: Elements, n = 220): V3[] {
  const out: V3[] = [];
  for (let k = 0; k <= n; k++) out.push(pos(el, (k / n) * 2 * Math.PI));
  return out;
}

/** equatorial RA/Dec (degrees) to a unit vector in ecliptic coordinates */
export function eclipticDir(ra: number, dec: number): V3 {
  const a = ra * RAD, d = dec * RAD;
  const x = Math.cos(d) * Math.cos(a), y = Math.cos(d) * Math.sin(a), z = Math.sin(d);
  return [x, y * Math.cos(OBL) + z * Math.sin(OBL), -y * Math.sin(OBL) + z * Math.cos(OBL)];
}

const JD_NOW = Date.now() / 86400000 + 2440587.5;

interface Body { name: string; layer: Layer; colour: string; size: number; a: number; orb: V3[]; at: V3 }

function bodies(): Body[] {
  const T = (JD_NOW - 2451545.0) / 36525;
  const out: Body[] = [];
  for (const p of PLANETS) {
    const M = wrap((p.L + p.dL * T - p.vp) * RAD);
    out.push({ name: p.name, layer: 'planets', colour: p.colour, size: p.size, a: p.a, orb: orbit(p), at: pos(p, kepler(M, p.e)) });
  }
  for (const t of TNOS) {
    const nName = t.n.replace(/^\d+\s+/, '').replace(/\s*\(.*\)/, '');
    const dwarf = /Pluto|Eris|Haumea|Makemake|Gonggong|Quaoar|Sedna|Orcus/i.test(nName);
    const n = 0.9856076686 / Math.pow(t.a, 1.5);
    const M = wrap((t.ma + n * (JD_NOW - t.epoch)) * RAD);
    const cluster = !dwarf || /Sedna/i.test(nName);
    out.push({ name: nName, layer: dwarf && !/Sedna/i.test(nName) ? 'dwarfs' : 'cluster', colour: dwarf && !/Sedna/i.test(nName) ? '#e3cf9d' : '#c4a1ff', size: 3.2, a: t.a, orb: orbit(t), at: pos(t, kepler(M, t.e)) });
    void cluster;
  }
  return out;
}

export function Model({ onHover }: { onHover?: (s: string | null) => void }) {
  const cv = useRef<HTMLCanvasElement>(null);
  const [on, setOn] = useState<Record<Layer, boolean>>({ planets: true, dwarfs: true, cluster: true, p9: true, dirs: true });
  const [scale, setScale] = useState<'log' | 'true'>('log');
  const [tip, setTip] = useState<{ x: number; y: number; t: string } | null>(null);
  const st = useRef({ yaw: -0.6, pitch: 1.2, zoom: 1.2, drag: null as null | [number, number], raf: 0, hit: [] as { x: number; y: number; t: string }[] });
  const model = useRef<{ bodies: Body[]; p9: V3[][]; p9n: V3[] }>();
  if (!model.current) {
    const nom = orbit(P9);
    const lo = orbit({ ...P9, a: P9.aRange[0], e: 1 - P9.peri / P9.aRange[0] });
    const hi = orbit({ ...P9, a: P9.aRange[1], e: 1 - P9.peri / P9.aRange[1] });
    model.current = { bodies: bodies(), p9: [lo, hi], p9n: nom };
  }
  const opts = useRef({ on, scale });
  opts.current = { on, scale };

  useEffect(() => {
    const c = cv.current!;
    const ctx = c.getContext('2d')!;
    if (c.clientWidth < 500) st.current.zoom = 1;
    let dirty = true;
    const draw = () => {
      st.current.raf = requestAnimationFrame(draw);
      if (!dirty) return;
      dirty = false;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = c.clientWidth, h = c.clientHeight;
      if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
        c.width = Math.round(w * dpr);
        c.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const { yaw, pitch, zoom } = st.current;
      const { on, scale } = opts.current;
      const R = Math.min(w, h) * 0.46 * zoom;
      const f = (r: number) => (scale === 'log' ? Math.log(1 + r / 1.5) / Math.log(1 + MAXR / 1.5) : r / MAXR) * R;
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const proj = (p: V3): [number, number, number] => {
        const r = Math.hypot(p[0], p[1], p[2]);
        const k = r > 0 ? f(r) / r : 0;
        const x = p[0] * k, y = p[1] * k, z = p[2] * k * (scale === 'log' ? 1 : 1);
        const x1 = x * cy - y * sy, y1 = x * sy + y * cy;
        return [w / 2 + x1, h / 2 - (y1 * cp + z * sp) * 1, y1 * sp - z * cp];
      };
      // distance rings
      ctx.lineWidth = 1;
      ctx.font = '10px ui-monospace, monospace';
      for (const r of [1, 10, 30, 100, 300, 1000]) {
        ctx.strokeStyle = 'rgba(160,200,215,0.14)';
        ctx.beginPath();
        for (let k = 0; k <= 90; k++) {
          const a = (k / 90) * 2 * Math.PI;
          const q = proj([r * Math.cos(a), r * Math.sin(a), 0]);
          if (k === 0) ctx.moveTo(q[0], q[1]);
          else ctx.lineTo(q[0], q[1]);
        }
        ctx.stroke();
        const q = proj([r * Math.cos(-0.9), r * Math.sin(-0.9), 0]);
        ctx.fillStyle = 'rgba(160,200,215,0.55)';
        ctx.fillText(`${r} au`, q[0] + 3, q[1] - 3);
      }
      const hits: { x: number; y: number; t: string }[] = [];
      // labels are placed in priority order and skipped when they would overlap one already drawn (every body still answers on hover)
      const placed: [number, number, number, number][] = [];
      const label = (t: string, x: number, y: number, dx: number, font = '11px system-ui, sans-serif'): boolean => {
        ctx.font = font;
        const tw = ctx.measureText(t).width;
        const lx = x + dx + tw > w - 4 ? x - dx - tw : x + dx;
        const r: [number, number, number, number] = [lx - 2, y - 11, lx + tw + 2, y + 3];
        if (r[0] < 2 || placed.some((p) => r[0] < p[2] && r[2] > p[0] && r[1] < p[3] && r[3] > p[1])) return false;
        placed.push(r);
        ctx.fillText(t, lx, y);
        return true;
      };
      // every planet is named: its label takes the nearest free place around the dot (further out, with a thin leader line, where the inner planets crowd together)
      const spotFor = (t: string, x: number, y: number, dx: number, font: string, k0 = 0) => {
        ctx.font = font;
        const tw = ctx.measureText(t).width;
        for (let k = k0; k < 9; k++) {
          for (const deg of [0, 180, -40, 40, -140, 140, -90, 90]) {
            const rad = dx + k * 20;
            const px = x + Math.cos((deg * Math.PI) / 180) * rad;
            const py = y + Math.sin((deg * Math.PI) / 180) * rad;
            const c = Math.cos((deg * Math.PI) / 180);
            const lx = c > 0.3 ? px : c < -0.3 ? px - tw : px - tw / 2;
            const r: [number, number, number, number] = [lx - 2, py - 7, lx + tw + 2, py + 6];
            if (r[0] < 4 || r[2] > w - 4 || r[1] < 4 || r[3] > h - 4) continue;
            if (placed.some((p) => r[0] < p[2] && r[2] > p[0] && r[1] < p[3] && r[3] > p[1])) continue;
            placed.push(r);
            return { t, lx, ly: py + 4, font, from: [x, y] as [number, number], to: [Math.max(r[0], Math.min(r[2], x)), Math.max(r[1] + 2, Math.min(r[3] - 2, y))] as [number, number], lead: k > 0 };
          }
        }
        return null;
      };
      const planetSpots: NonNullable<ReturnType<typeof spotFor>>[] = [];
      if (on.planets) {
        for (const b of model.current!.bodies.filter((b) => b.layer === 'planets')) {
          const q = proj(b.at);
          const sp = spotFor(b.name, q[0], q[1], b.size + 5, '11px system-ui, sans-serif', b.a < 2 ? 2 : 0);
          if (sp) planetSpots.push(sp);
        }
      }
      const line = (pts: V3[], col: string, wd: number, dash?: number[]) => {
        ctx.strokeStyle = col;
        ctx.lineWidth = wd;
        ctx.setLineDash(dash ?? []);
        ctx.beginPath();
        pts.forEach((p, k) => {
          const q = proj(p);
          if (k === 0) ctx.moveTo(q[0], q[1]);
          else ctx.lineTo(q[0], q[1]);
        });
        ctx.stroke();
        ctx.setLineDash([]);
      };
      const m = model.current!;
      if (on.p9) {
        for (const o of m.p9) line(o, 'rgba(255,122,89,0.28)', 1, [3, 4]);
        line(m.p9n, 'rgba(255,122,89,0.85)', 1.6, [6, 4]);
        const q = proj(pos(P9, Math.PI)); // aphelion, where the body spends most of its time
        ctx.fillStyle = 'rgba(255,122,89,0.9)';
        ctx.font = '11px system-ui, sans-serif';
        label('Planet Nine orbit (predicted)', q[0], q[1] + 3, 8);
        hits.push({ x: q[0], y: q[1], t: `Planet Nine, predicted: a ${P9.a} au (${P9.aRange[0]}–${P9.aRange[1]}), perihelion ${P9.peri} au, i ${P9.i}°, ${P9.mass}. Not discovered.` });
      }
      for (const b of m.bodies) {
        if (!on[b.layer]) continue;
        line(b.orb, b.layer === 'planets' ? 'rgba(111,179,255,0.35)' : b.layer === 'cluster' ? 'rgba(196,161,255,0.42)' : 'rgba(227,207,157,0.4)', 1);
      }
      if (on.dirs) {
        for (const fd of FIELDS) {
          const d = eclipticDir(fd.ra, fd.dec);
          const lo = fd.dmin, hi = Math.min(fd.dmax, MAXR);
          const a = proj([d[0] * lo, d[1] * lo, d[2] * lo]);
          const b = proj([d[0] * hi, d[1] * hi, d[2] * hi]);
          ctx.strokeStyle = fd.colour;
          ctx.lineWidth = 3;
          ctx.globalAlpha = 0.8;
          ctx.beginPath();
          ctx.moveTo(a[0], a[1]);
          ctx.lineTo(b[0], b[1]);
          ctx.stroke();
          ctx.globalAlpha = 1;
          ctx.fillStyle = fd.colour;
          ctx.beginPath();
          ctx.arc(b[0], b[1], 3.5, 0, 7);
          ctx.fill();
          ctx.font = '11px system-ui, sans-serif';
          label(fd.name, b[0], b[1] + 3, 7);
          hits.push({ x: b[0], y: b[1], t: `${fd.name}: ${fd.dmin}–${fd.dmax} au along RA ${fd.ra}°, Dec ${fd.dec >= 0 ? '+' : ''}${fd.dec}°` });
        }
      }
      // the Sun
      const s = proj([0, 0, 0]);
      const g = ctx.createRadialGradient(s[0], s[1], 0, s[0], s[1], 14);
      g.addColorStop(0, 'rgba(255,230,160,1)');
      g.addColorStop(1, 'rgba(255,200,90,0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(s[0], s[1], 14, 0, 7);
      ctx.fill();
      hits.push({ x: s[0], y: s[1], t: 'Sun' });
      const prio = (b: Body) => (/Pluto|Eris|Makemake|Haumea|Sedna/.test(b.name) ? 0 : b.layer === 'planets' ? 1 : 2);
      const shown = m.bodies.filter((b) => on[b.layer]);
      for (const b of shown) {
        const q = proj(b.at);
        ctx.fillStyle = b.colour;
        ctx.beginPath();
        ctx.arc(q[0], q[1], b.size, 0, 7);
        ctx.fill();
        hits.push({ x: q[0], y: q[1], t: `${b.name} · a ${b.a < 10 ? b.a.toFixed(2) : Math.round(b.a)} au` });
      }
      ctx.fillStyle = 'rgba(225,240,245,0.85)';
      for (const b of [...shown].filter((b) => b.layer !== 'planets').sort((p, q) => prio(p) - prio(q))) {
        const q = proj(b.at);
        label(b.name, q[0], q[1] + 3, b.size + 3, '10.5px system-ui, sans-serif');
      }
      for (const sp of planetSpots) {
        if (sp.lead) {
          ctx.strokeStyle = 'rgba(225,240,245,0.28)';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(sp.from[0], sp.from[1]);
          ctx.lineTo(sp.to[0], sp.to[1]);
          ctx.stroke();
        }
        ctx.font = sp.font;
        ctx.fillStyle = 'rgba(240,248,250,0.95)';
        ctx.fillText(sp.t, sp.lx, sp.ly);
      }
      st.current.hit = hits;
    };
    const mark = () => (dirty = true);
    (c as unknown as { _mark: () => void })._mark = mark;
    const ro = new ResizeObserver(mark);
    ro.observe(c);
    st.current.raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(st.current.raf);
      ro.disconnect();
    };
  }, []);
  useEffect(() => {
    (cv.current as unknown as { _mark?: () => void })._mark?.();
  }, [on, scale]);

  const mark = () => (cv.current as unknown as { _mark?: () => void })._mark?.();
  const down = (e: PointerEvent) => {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    st.current.drag = [e.clientX, e.clientY];
  };
  const move = (e: PointerEvent) => {
    const s = st.current;
    const r = cv.current!.getBoundingClientRect();
    if (s.drag) {
      s.yaw += (e.clientX - s.drag[0]) * 0.008;
      s.pitch = Math.max(0.05, Math.min(1.5, s.pitch + (e.clientY - s.drag[1]) * 0.006));
      s.drag = [e.clientX, e.clientY];
      mark();
      setTip(null);
      return;
    }
    const x = e.clientX - r.left, y = e.clientY - r.top;
    let best: { x: number; y: number; t: string } | null = null, bd = 12;
    for (const h of s.hit) {
      const d = Math.hypot(h.x - x, h.y - y);
      if (d < bd) { bd = d; best = h; }
    }
    setTip(best ? { x: best.x, y: best.y, t: best.t } : null);
    onHover?.(best?.t ?? null);
  };
  const up = () => (st.current.drag = null);
  const wheel = (e: WheelEvent) => {
    e.preventDefault();
    st.current.zoom = Math.max(0.5, Math.min(12, st.current.zoom * Math.exp(-e.deltaY * 0.0012)));
    mark();
  };
  useEffect(() => {
    const c = cv.current!;
    c.addEventListener('wheel', wheel, { passive: false });
    return () => c.removeEventListener('wheel', wheel);
  }, []);
  const reset = () => {
    Object.assign(st.current, { yaw: -0.6, pitch: 1.2, zoom: cv.current!.clientWidth < 500 ? 1 : 1.2 });
    mark();
  };

  return (
    <div class="px-model">
      <div class="px-chips">
        {LAYERS.map((l) => (
          <button key={l.id} class={`px-chip ${on[l.id] ? 'on' : ''}`} onClick={() => setOn({ ...on, [l.id]: !on[l.id] })} aria-pressed={on[l.id]}>
            <i style={{ background: l.colour }} />
            {l.name}
          </button>
        ))}
        <span class="px-sp" />
        <span class="px-seg" role="group" aria-label="Distance scale">
          <button class={scale === 'log' ? 'on' : ''} onClick={() => setScale('log')}>Log</button>
          <button class={scale === 'true' ? 'on' : ''} onClick={() => setScale('true')}>True</button>
        </span>
        <button class="px-chip" onClick={reset}>Reset view</button>
      </div>
      <div class="px-stage">
        <canvas ref={cv} class="px-canvas" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={() => { up(); setTip(null); }} data-testid="px-model" />
        {tip && <div class="px-tip" style={{ left: tip.x + 12, top: tip.y + 12 }}>{tip.t}</div>}
      </div>
    </div>
  );
}
