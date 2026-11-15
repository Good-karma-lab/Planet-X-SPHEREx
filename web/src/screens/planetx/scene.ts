// The Planet X page's 3D model: the Sun, the planets with real textures (Solar System Scope, CC BY 4.0, from NASA imagery), the dwarf
// planets and the clustered distant bodies on their JPL orbits, the published range of Planet Nine orbits (Brown & Batygin 2021) with
// the most likely one bright, and the directions where Find Planet X has run (live /api/v1/searched), over the real infrared sky (2MASS).
// Loaded on demand by PlanetX.tsx, so three.js is only fetched on this page.
import * as THREE from 'three';
import { P9, PLANETS, TNOS, type Elements } from './data';

const D2R = Math.PI / 180;
const EPS = 23.4393 * D2R;
const JD_NOW = Date.now() / 86400000 + 2440587.5;
const TEX = '/planetx/';

export type Group = 'planets' | 'dwarfs' | 'etnos' | 'p9' | 'fields' | 'peri';
export type Layers = Record<Group, boolean>;
export type View = 'home' | 'cluster' | 'p9' | 'band' | 'spherex' | 'search';

export interface PxField {
  key: string;
  ra: number;
  dec: number;
  name: string;
  best: string;
  href: string;
  lead: boolean;
}

interface Tag {
  obj: THREE.Object3D;
  html: string;
  group: Group | null;
  prio: number;
  color?: string;
  dim?: boolean;
  href?: string;
  key?: string;
  /** shown only while this story chapter is open */
  chapter?: number;
  el?: HTMLElement;
  w?: number;
  h?: number;
}

interface Cam {
  yaw: number;
  pitch: number;
  dist: number;
  ty: number;
}

/** camera places for the story chapters (yaw, pitch above the ecliptic, distance, target height) */
const VIEWS: Record<View, Cam> = {
  home: { yaw: -0.367, pitch: 0.71, dist: 110, ty: 0 },
  cluster: { yaw: 0, pitch: 1.52, dist: 118, ty: 0 },
  p9: { yaw: -0.556, pitch: 0.59, dist: 105, ty: 0 },
  band: { yaw: 1.226, pitch: 0.16, dist: 138, ty: -6 },
  spherex: { yaw: -0.427, pitch: 0.55, dist: 57, ty: 0 },
  search: { yaw: -0.367, pitch: 0.71, dist: 110, ty: 0 },
};
const PORTRAIT_HOME: Cam = { yaw: -0.152, pitch: 1.23, dist: 159, ty: 0 };

function kepler(el: Elements, M: number): [number, number, number] {
  let E = M;
  for (let k = 0; k < 40; k++) E -= (E - el.e * Math.sin(E) - M) / (1 - el.e * Math.cos(E));
  const xv = el.a * (Math.cos(E) - el.e), yv = el.a * Math.sqrt(1 - el.e * el.e) * Math.sin(E);
  const ci = Math.cos(el.i * D2R), si = Math.sin(el.i * D2R), co = Math.cos(el.om * D2R), so = Math.sin(el.om * D2R);
  const cw = Math.cos(el.w * D2R), sw = Math.sin(el.w * D2R);
  return [
    (co * cw - so * sw * ci) * xv + (-co * sw - so * cw * ci) * yv,
    (so * cw + co * sw * ci) * xv + (-so * sw + co * cw * ci) * yv,
    sw * si * xv + cw * si * yv,
  ];
}

const eq2ecl = (ra: number, dec: number): [number, number, number] => {
  const cd = Math.cos(dec * D2R), xq = cd * Math.cos(ra * D2R), yq = cd * Math.sin(ra * D2R), zq = Math.sin(dec * D2R);
  return [xq, yq * Math.cos(EPS) + zq * Math.sin(EPS), -yq * Math.sin(EPS) + zq * Math.cos(EPS)];
};

export class PxScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(40, 1, 0.01, 8000);
  private groups = {} as Record<Group, THREE.Group>;
  private tags: Tag[] = [];
  private log = true;
  private cam: Cam;
  private goal: Cam | null = null;
  private goalT0 = 0;
  private goalFrom: Cam | null = null;
  private raf = 0;
  private bodies: { mesh: THREE.Mesh; el: Elements; M: number }[] = [];
  private lines: { line: THREE.Line; el: Elements }[] = [];
  private tubes: { mesh: THREE.Mesh; build: () => THREE.BufferGeometry }[] = [];
  private periLines: { line: THREE.Line; el: Elements }[] = [];
  private rays = new Map<string, { line: THREE.Line; u: [number, number, number]; end: THREE.Object3D }>();
  private fieldTags: Tag[] = [];
  private chapter = -1;
  private highlighted: string | null = null;
  private p9tag = new THREE.Object3D();
  private periTag = new THREE.Object3D();
  private clusterTag = new THREE.Object3D();
  private p9peri!: THREE.Line;
  private sun!: THREE.Mesh;
  private still: boolean;
  private portrait = false;
  private ro: ResizeObserver;
  private off: (() => void)[] = [];

  constructor(
    private canvas: HTMLCanvasElement,
    private tagHost: HTMLElement,
    private blocked: () => DOMRect[],
  ) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    for (const g of ['planets', 'dwarfs', 'etnos', 'p9', 'fields', 'peri'] as Group[]) {
      this.groups[g] = new THREE.Group();
      this.scene.add(this.groups[g]);
    }
    this.groups.peri.visible = false;
    this.build();
    this.portrait = canvas.clientWidth < canvas.clientHeight;
    this.cam = { ...(this.portrait ? PORTRAIT_HOME : VIEWS.home) };
    this.controls();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas);
    this.resize();
    const loop = (now: number) => {
      this.tick(now);
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  // ---------- geometry ----------
  private R = (r: number) => (this.log ? 13 * Math.log10(1 + r / 0.25) : r / 6);
  private toV(p: [number, number, number]): THREE.Vector3 {
    const r = Math.hypot(p[0], p[1], p[2]) || 1e-9, s = this.R(r) / r;
    return new THREE.Vector3(p[0] * s, p[2] * s, -p[1] * s);
  }
  private orbitPts(el: Elements, arc?: [number, number]): THREE.Vector3[] {
    const pts: THREE.Vector3[] = [];
    const [m0, m1] = arc ?? [0, 2 * Math.PI];
    const n = arc ? 120 : 900;
    for (let k = 0; k <= n; k++) pts.push(this.toV(kepler(el, m0 + ((m1 - m0) * k) / n)));
    return pts;
  }
  private orbitLine(el: Elements, color: number, opacity: number, g: Group): THREE.Line {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(this.orbitPts(el)), new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
    this.groups[g].add(line);
    this.lines.push({ line, el });
    return line;
  }
  private tex(name: string): THREE.Texture {
    const t = new THREE.TextureLoader().load(TEX + name);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  private build() {
    // the real sky: 2MASS colour, toned, turned to the ecliptic frame
    const N = 96, M = 48, pos: number[] = [], uv: number[] = [], idx: number[] = [];
    for (let j = 0; j <= M; j++)
      for (let i = 0; i <= N; i++) {
        const ra = (i / N) * 360, dec = 90 - (j / M) * 180;
        const [x, y, z] = eq2ecl(ra, dec);
        pos.push(x * 3000, z * 3000, -y * 3000);
        uv.push(ra / 360, (dec + 90) / 180);
      }
    for (let j = 0; j < M; j++)
      for (let i = 0; i < N; i++) {
        const a = j * (N + 1) + i, b = a + N + 1;
        idx.push(a, b, a + 1, b, b + 1, a + 1);
      }
    const sg = new THREE.BufferGeometry();
    sg.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    sg.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    sg.setIndex(idx);
    this.scene.add(new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ map: this.tex('sky.jpg'), side: THREE.DoubleSide, depthWrite: false, color: 0x8a96a4 })));
    this.scene.add(new THREE.AmbientLight(0x667788, 1.2));
    this.scene.add(new THREE.PointLight(0xffffff, 3, 0, 0));

    this.sun = new THREE.Mesh(new THREE.SphereGeometry(1.7, 48, 32), new THREE.MeshBasicMaterial({ map: this.tex('sun.jpg') }));
    this.scene.add(this.sun);
    this.tags.push({ obj: this.sun, html: 'Sun', group: null, prio: 3, dim: true });

    const T = (JD_NOW - 2451545) / 36525;
    const SIZE: Record<string, number> = { Mercury: 0.28, Venus: 0.42, Earth: 0.45, Mars: 0.34, Jupiter: 1, Saturn: 0.9, Uranus: 0.7, Neptune: 0.7 };
    for (const p of PLANETS) {
      this.orbitLine(p, 0x9fb3c2, 0.38, 'planets');
      const M = (((p.L + p.dL * T - p.vp) % 360) + 360) % 360;
      const size = SIZE[p.name] ?? 0.4;
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(size, 40, 24), new THREE.MeshStandardMaterial({ map: this.tex(p.name.toLowerCase() + '.jpg'), roughness: 1, metalness: 0 }));
      if (p.name === 'Saturn') {
        const ring = new THREE.Mesh(new THREE.RingGeometry(size * 1.3, size * 2.2, 64), new THREE.MeshBasicMaterial({ map: this.tex('ring.png'), side: THREE.DoubleSide, transparent: true, opacity: 0.85 }));
        ring.rotation.x = Math.PI / 2.4;
        mesh.add(ring);
      }
      this.groups.planets.add(mesh);
      this.bodies.push({ mesh, el: p, M: M * D2R });
      this.tags.push({ obj: mesh, html: p.name, group: 'planets', prio: 1 });
    }

    const etnos: Elements[] = [];
    for (const t of TNOS) {
      const name = ((t.n.match(/^\d+\s+(\S+)/) || [])[1] ?? t.n.replace(/[()]/g, '').trim()).replace('Leleakuhonua', 'Leleākūhonua');
      const dwarf = /Pluto|Eris|Makemake|Haumea/.test(t.n);
      const sedna = /Sedna/.test(t.n);
      const g: Group = dwarf ? 'dwarfs' : 'etnos';
      const col = dwarf ? 0x6fdc8c : 0x7dd3fc;
      this.orbitLine(t, col, dwarf ? 0.7 : 0.55, g);
      const n = 360 / (t.a ** 1.5 * 365.25);
      const M = ((((t.ma + n * (JD_NOW - t.epoch)) % 360) + 360) % 360) * D2R;
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(dwarf ? 0.34 : 0.3, 16, 12), new THREE.MeshBasicMaterial({ color: col }));
      this.groups[g].add(mesh);
      this.bodies.push({ mesh, el: t, M });
      this.tags.push({ obj: mesh, html: name, group: g, prio: dwarf || sedna ? 1 : 2, color: dwarf ? '#bff0cc' : '#bfe8fb', dim: !dwarf && !sedna, chapter: !dwarf && !sedna ? 0 : undefined });
      if (!dwarf) etnos.push(t);
    }
    for (const el of etnos) {
      const line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.9 }));
      this.groups.peri.add(line);
      this.periLines.push({ line, el });
    }

    // Planet Nine: the published range as a family of orbits (brighter = more likely), the most likely one as a tube,
    // and the stretch near aphelion where it most likely is now
    const gauss = (x: number, m: number, s: number) => Math.exp(-0.5 * ((x - m) / s) ** 2);
    for (const a of [300, 330, 360, 380, 410, 450, 490, 520])
      for (const q of [240, 270, 300, 340, 385])
        for (const inc of [11, 16, 21]) {
          if (q >= a * 0.95) continue;
          const wgt = gauss(a, 380, a < 380 ? 80 : 140) * gauss(q, 300, q < 300 ? 60 : 85) * gauss(inc, 16, 5);
          this.orbitLine({ a, e: 1 - q / a, i: inc, om: P9.om, w: P9.w }, 0xff9b5c, 0.035 + 0.2 * wgt, 'p9');
        }
    this.orbitLine(P9, 0xffa66b, 1, 'p9');
    const main = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: 0xff9b5c }));
    this.groups.p9.add(main);
    this.tubes.push({ mesh: main, build: () => new THREE.TubeGeometry(new THREE.CatmullRomCurve3(this.orbitPts(P9), true), 600, this.log ? 0.16 : 0.5, 6, true) });
    const glow = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: 0xffd9b3, transparent: true, opacity: 0.9 }));
    this.groups.p9.add(glow);
    this.tubes.push({ mesh: glow, build: () => new THREE.TubeGeometry(new THREE.CatmullRomCurve3(this.orbitPts(P9, [Math.PI - 0.8, Math.PI + 0.8])), 200, this.log ? 0.42 : 1.2, 8, false) });
    this.groups.p9.add(this.p9tag);
    this.tags.push({ obj: this.p9tag, html: 'Planet Nine? <small>most likely here now</small>', group: 'p9', prio: 0, color: '#ffb27a' });
    this.p9peri = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({ color: 0xff9b5c, dashSize: 1, gapSize: 0.7, transparent: true, opacity: 0.9 }));
    this.groups.peri.add(this.p9peri, this.periTag, this.clusterTag);
    this.tags.push({ obj: this.periTag, html: 'Planet Nine’s closest point <small>opposite the cluster</small>', group: 'peri', prio: 0, color: '#ffb27a' });
    this.tags.push({ obj: this.clusterTag, html: 'closest points of the clustered bodies', group: 'peri', prio: 0, color: '#bfe8fb' });
    this.relayout();
    for (const t of this.tags) this.mountTag(t);
  }

  private mountTag(t: Tag) {
    const el = document.createElement(t.href ? 'a' : 'div');
    el.className = 'px3-tag' + (t.dim ? ' dim' : '') + (t.href ? ' link' : '');
    el.innerHTML = t.html;
    if (t.color) el.style.color = t.color;
    if (t.href) (el as HTMLAnchorElement).href = t.href;
    el.style.display = 'none';
    this.tagHost.appendChild(el);
    t.el = el;
  }

  private relayout() {
    for (const { line, el } of this.lines) line.geometry.setFromPoints(this.orbitPts(el));
    for (const t of this.tubes) {
      t.mesh.geometry.dispose();
      t.mesh.geometry = t.build();
    }
    for (const b of this.bodies) b.mesh.position.copy(this.toV(kepler(b.el, b.M)));
    this.p9tag.position.copy(this.toV(kepler(P9, Math.PI + 0.55)));
    const c = new THREE.Vector3();
    for (const { line, el } of this.periLines) {
      const p = this.toV(kepler(el, 0));
      line.geometry.setFromPoints([new THREE.Vector3(), p]);
      c.add(p);
    }
    if (this.periLines.length) this.clusterTag.position.copy(c.multiplyScalar(1 / this.periLines.length));
    const pp = this.toV(kepler(P9, 0));
    this.p9peri.geometry.setFromPoints([new THREE.Vector3(), pp]);
    this.p9peri.computeLineDistances();
    this.periTag.position.copy(pp);
    for (const r of this.rays.values()) this.placeRay(r);
  }

  private placeRay(r: { line: THREE.Line; u: [number, number, number]; end: THREE.Object3D }) {
    const pts: THREE.Vector3[] = [];
    for (let s = 0; s <= 60; s++) {
      const d = 30 + ((2000 - 30) * s) / 60;
      pts.push(this.toV([r.u[0] * d, r.u[1] * d, r.u[2] * d]));
    }
    r.line.geometry.setFromPoints(pts);
    r.end.position.copy(pts[pts.length - 1]!);
  }

  // ---------- public ----------
  /** the directions where Find Planet X has run; each label opens its field in the Lab */
  setFields(list: PxField[]) {
    for (const r of this.rays.values()) {
      this.groups.fields.remove(r.line, r.end);
      r.line.geometry.dispose();
    }
    this.rays.clear();
    for (const t of this.fieldTags) t.el?.remove();
    this.tags = this.tags.filter((t) => !this.fieldTags.includes(t));
    this.fieldTags = [];
    for (const f of list) {
      const line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: f.lead ? 0xffb454 : 0x5fe3d6, transparent: true, opacity: 0.6 }));
      const end = new THREE.Object3D();
      const r = { line, u: eq2ecl(f.ra, f.dec), end };
      this.placeRay(r);
      this.groups.fields.add(line, end);
      this.rays.set(f.key, r);
      const t: Tag = { obj: end, html: `${f.name} <small>${f.best}</small>`, group: 'fields', prio: 1, color: f.lead ? '#ffc98a' : '#7ff0e4', href: f.href, key: f.key };
      this.mountTag(t);
      this.tags.push(t);
      this.fieldTags.push(t);
    }
    this.highlight(this.highlighted);
  }

  highlight(key: string | null) {
    this.highlighted = key;
    for (const [k, r] of this.rays) (r.line.material as THREE.LineBasicMaterial).opacity = key == null ? 0.6 : k === key ? 1 : 0.22;
  }

  setLayers(l: Partial<Layers>) {
    for (const [g, on] of Object.entries(l) as [Group, boolean][]) this.groups[g].visible = on;
  }

  layers(): Layers {
    const o = {} as Layers;
    for (const g of Object.keys(this.groups) as Group[]) o[g] = this.groups[g].visible;
    return o;
  }

  setLog(log: boolean) {
    this.log = log;
    this.relayout();
  }

  /** fly to a story view; chapter -1 = no chapter open */
  view(v: View, chapter: number) {
    this.chapter = chapter;
    const base = v === 'home' && this.portrait ? PORTRAIT_HOME : VIEWS[v];
    const goal = this.portrait && v !== 'home' ? { ...base, dist: base.dist * 1.25 } : { ...base };
    if (this.still) {
      this.cam = goal;
      return;
    }
    this.goalFrom = { ...this.cam };
    this.goal = goal;
    this.goalT0 = performance.now();
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    for (const f of this.off) f();
    for (const t of this.tags) t.el?.remove();
    this.renderer.dispose();
  }

  // ---------- camera and input ----------
  private resize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.fov = w / h < 1 ? 58 : 40;
    this.camera.updateProjectionMatrix();
  }

  private controls() {
    const c = this.canvas;
    const touches = new Map<number, [number, number]>();
    let drag: [number, number] | null = null;
    let pinch = 0;
    const turn = (dx: number, dy: number) => {
      this.goal = null;
      this.cam.yaw += dx * 0.008;
      // dragging down tilts the model the natural way: the near side goes down (as on the live page since 870bd5d)
      this.cam.pitch = Math.max(0.05, Math.min(1.53, this.cam.pitch - dy * 0.006));
    };
    const zoom = (f: number) => {
      this.goal = null;
      this.cam.dist = Math.max(6, Math.min(420, this.cam.dist * f));
    };
    const down = (e: PointerEvent) => {
      if (e.pointerType === 'touch') {
        touches.set(e.pointerId, [e.clientX, e.clientY]);
        if (touches.size === 2) {
          const [a, b] = [...touches.values()] as [[number, number], [number, number]];
          pinch = Math.hypot(a[0] - b[0], a[1] - b[1]);
        }
        return;
      }
      c.setPointerCapture(e.pointerId);
      drag = [e.clientX, e.clientY];
    };
    const move = (e: PointerEvent) => {
      if (e.pointerType === 'touch') {
        const prev = touches.get(e.pointerId);
        if (!prev) return;
        touches.set(e.pointerId, [e.clientX, e.clientY]);
        if (touches.size !== 2) return; // one finger scrolls the page
        const [a, b] = [...touches.values()] as [[number, number], [number, number]];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch > 0) zoom(pinch / d);
        pinch = d;
        turn((e.clientX - prev[0]) / 2, (e.clientY - prev[1]) / 2);
        e.preventDefault();
        return;
      }
      if (!drag) return;
      turn(e.clientX - drag[0], e.clientY - drag[1]);
      drag = [e.clientX, e.clientY];
    };
    const up = (e: PointerEvent) => {
      touches.delete(e.pointerId);
      if (touches.size < 2) pinch = 0;
      drag = null;
    };
    // the wheel zooms only where the page itself does not scroll (the full-height desktop layout), or with Ctrl/⌘ held
    const wheel = (e: WheelEvent) => {
      const pageScrolls = window.matchMedia('(max-width: 860px), (max-aspect-ratio: 1/1)').matches;
      if (pageScrolls && !e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      zoom(Math.exp(e.deltaY * 0.0012));
    };
    c.addEventListener('pointerdown', down);
    c.addEventListener('pointermove', move, { passive: false });
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('wheel', wheel, { passive: false });
    this.off.push(() => {
      c.removeEventListener('pointerdown', down);
      c.removeEventListener('pointermove', move);
      c.removeEventListener('pointerup', up);
      c.removeEventListener('pointercancel', up);
      c.removeEventListener('wheel', wheel);
    });
  }

  private tick(now: number) {
    if (this.goal && this.goalFrom) {
      const k = Math.min(1, (now - this.goalT0) / 1100), e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      const f = this.goalFrom, g = this.goal;
      let dy = g.yaw - f.yaw;
      dy = ((dy + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
      this.cam = { yaw: f.yaw + dy * e, pitch: f.pitch + (g.pitch - f.pitch) * e, dist: f.dist + (g.dist - f.dist) * e, ty: f.ty + (g.ty - f.ty) * e };
      if (k >= 1) this.goal = null;
    }
    const { yaw, pitch, dist, ty } = this.cam;
    this.camera.position.set(dist * Math.cos(pitch) * Math.sin(yaw), ty + dist * Math.sin(pitch), dist * Math.cos(pitch) * Math.cos(yaw));
    this.camera.lookAt(0, ty, 0);
    if (!this.still) {
      this.sun.rotation.y += 0.001;
      for (const b of this.bodies) if ((b.mesh.geometry as THREE.SphereGeometry).parameters.radius > 0.4) b.mesh.rotation.y += 0.004;
    }
    this.renderer.render(this.scene, this.camera);
    this.placeTags();
  }

  // ---------- labels: placed in priority order, each in the first free spot around its point, never over the page's panels ----------
  private placeTags() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const host = this.tagHost.getBoundingClientRect();
    const taken: [number, number, number, number][] = this.blocked().map((r) => [r.left - host.left, r.top - host.top, r.right - host.left, r.bottom - host.top]);
    const v = new THREE.Vector3();
    const order = [...this.tags].sort((a, b) => a.prio - b.prio);
    const hit = (x0: number, y0: number, x1: number, y1: number) => x0 < 2 || y0 < 2 || x1 > w - 2 || y1 > h - 2 || taken.some((r) => x0 < r[2] && x1 > r[0] && y0 < r[3] && y1 > r[1]);
    for (const t of order) {
      const el = t.el!;
      const groupOn = !t.group || this.groups[t.group].visible;
      const chapterOn = t.chapter == null || t.chapter === this.chapter;
      const fieldOn = !t.key || this.chapter < 0 || this.chapter === 3 || this.chapter === 4;
      t.obj.getWorldPosition(v);
      v.project(this.camera);
      if (!groupOn || !chapterOn || !fieldOn || v.z >= 1 || Math.abs(v.x) > 1.02 || Math.abs(v.y) > 1.02) {
        el.style.display = 'none';
        continue;
      }
      el.style.display = '';
      // measured every frame: a label measured before the web font arrives is too narrow and would run off the edge
      t.w = el.offsetWidth;
      t.h = el.offsetHeight;
      const x = ((v.x + 1) / 2) * w, y = ((1 - v.y) / 2) * h, tw = t.w!, th = t.h!;
      const spots: [number, number][] = [[x + 9, y - th / 2], [x - 9 - tw, y - th / 2], [x + 6, y - th - 3], [x + 6, y + 3], [x - tw - 6, y - th - 3], [x - tw - 6, y + 3]];
      const spot = spots.find(([sx, sy]) => !hit(sx, sy, sx + tw, sy + th));
      if (!spot) {
        el.style.display = 'none';
        continue;
      }
      taken.push([spot[0], spot[1], spot[0] + tw, spot[1] + th]);
      el.style.transform = `translate(${spot[0].toFixed(1)}px, ${spot[1].toFixed(1)}px)`;
    }
  }
}
