import { useEffect, useRef, useState } from 'preact/hooks';
import { halfToFloat, parseSxp } from '../../lib/sxp';

const halfCache = new Map<number, number>();
const half = (h: number) => {
  let v = halfCache.get(h);
  if (v === undefined) halfCache.set(h, (v = halfToFloat(h)));
  return v;
};

/**
 * The picture of a finished field, as the service keeps it (the white frame of the last pass, `quick_p<last>`): grey, asinh stretch between the 1st and 99.8th percentile,
 * north up and east left like the lab. `fieldKey` is the key of a field that is DONE (nothing is started here); null shows an empty frame with `empty` as its text.
 */
export function FieldThumb({ fieldKey, passes, empty = 'field not built yet', size = 160 }: { fieldKey: string | null; passes: number; empty?: string; size?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<'idle' | 'ok' | 'none'>('idle');
  useEffect(() => {
    setState('idle');
    if (!fieldKey || passes < 1) return;
    let stop = false;
    (async () => {
      const r = await fetch(`/api/v1/fields/${fieldKey}/parts/quick_p${passes - 1}`);
      if (!r.ok) return setState('none');
      const p = parseSxp(await r.arrayBuffer());
      const a = p.arrays['white'];
      const cv = ref.current;
      if (!a || !cv || stop) return setState('none');
      const n = a.shape[0]!;
      const raw = a.data as Uint16Array;
      const v = new Float32Array(n * n);
      const f: number[] = [];
      for (let i = 0; i < n * n; i++) {
        const x = half(raw[i]!);
        v[i] = x;
        if (Number.isFinite(x) && i % 7 === 0) f.push(x);
      }
      f.sort((x, y) => x - y);
      const lo = f[Math.floor(f.length * 0.01)] ?? 0;
      const hi = f[Math.floor(f.length * 0.998)] ?? 1;
      cv.width = cv.height = n;
      const c = cv.getContext('2d')!;
      const im = c.createImageData(n, n);
      const k = Math.asinh(10);
      for (let i = 0; i < n * n; i++) {
        const x = Number.isFinite(v[i]!) ? Math.max(0, Math.min(1, (v[i]! - lo) / (hi - lo || 1))) : 0;
        const g = Math.round((255 * Math.asinh(10 * x)) / k);
        im.data.set([g, g, g, 255], i * 4);
      }
      c.putImageData(im, 0, 0);
      setState('ok');
    })().catch(() => !stop && setState('none'));
    return () => {
      stop = true;
    };
  }, [fieldKey, passes]);
  return (
    <div class="fthumb" style={{ width: `${size}px`, height: `${size}px` }}>
      <canvas ref={ref} style={{ display: state === 'ok' ? 'block' : 'none' }} aria-label="The field as SPHEREx saw it" />
      {state !== 'ok' && <span class="mono-s">{fieldKey ? (state === 'idle' ? 'loading…' : 'no picture') : empty}</span>}
    </div>
  );
}

/** The lab's own picture of a test target's field (a capture kept with the site, the target ringed); when there is none, the picture drawn here from the service. */
export function FindingPicture({ name, href, fieldKey, passes, size = 160 }: { name: string; href: string; fieldKey: string | null; passes: number; size?: number }) {
  const [broken, setBroken] = useState(false);
  const slug = name
    .normalize('NFKD')
    .replace(/[^\w]+/g, '-')
    .replace(/^-|-$/g, '')
    .toLowerCase();
  if (broken) return <FieldThumb fieldKey={fieldKey} passes={passes} size={size} />;
  return (
    <a href={href} title="Open this field in the lab" class="fthumb" style={{ width: `${size}px`, height: `${size}px`, padding: 0 }}>
      <img
        src={`/findings/${slug}.jpg`}
        alt={`The lab's picture of the 20′ field of ${name}: SPHEREx stacks, the target ringed`}
        loading="lazy"
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        onError={() => setBroken(true)}
      />
    </a>
  );
}
