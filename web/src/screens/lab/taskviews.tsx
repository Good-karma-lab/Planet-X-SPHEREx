// What the lab shows with a task's question (product spec §5e): always the object's own pictures. The finding's clip (its
// visits, or a peak's two sums), the stamps of an anomaly's odd exposures, the summary of a mover's Blink and ruler.
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { useSpectrum, type Anomaly, type Source } from '../spectrum';
import { useStamps } from '../spectrum/stamps';
import type { SpecPoint, Stamp } from '../spectrum/types';

const DET_HUE = [270, 220, 175, 95, 40, 5];
const detColour = (d: number) => `hsl(${DET_HUE[d - 1] ?? 0},80%,62%)`;

/** The single exposures inside the marked ranges, and how many of them each detector gave. */
export function pointsIn(points: SpecPoint[], ranges: { lo: number; hi: number }[]): { inside: SpecPoint[]; perDet: number[] } {
  const inside = points.filter((p) => p.ok && p.flux != null && ranges.some((r) => p.lam >= r.lo && p.lam <= r.hi));
  const perDet = [0, 0, 0, 0, 0, 0];
  for (const p of inside) if (p.det >= 1 && p.det <= 6) perDet[p.det - 1]!++;
  return { inside, perDet };
}

/** at most this many odd pictures are shown (the most deviant first) */
const MAX_STAMPS = 6;

/** One stamp: the file's own pixels around the source in one exposure, grey, faint to bright. */
function RawStamp({ stamp }: { stamp: Stamp }) {
  const cv = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = cv.current;
    const raw = stamp.raw;
    if (!c || !raw?.length) return;
    const h = raw.length;
    const w = raw[0]!.length;
    c.width = w;
    c.height = h;
    const vals = raw.flat().filter((v): v is number => v != null && Number.isFinite(v)).sort((a, b) => a - b);
    const lo = vals[Math.floor(vals.length * 0.02)] ?? 0;
    const hi = vals[Math.floor(vals.length * 0.995)] ?? 1;
    const g = c.getContext('2d')!;
    const img = g.createImageData(w, h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const v = raw[y]![x];
        const k = 4 * (y * w + x);
        // no value (beyond the detector's edge): white, as the spectrum's stamps draw it
        const t = v == null ? 1 : Math.sqrt(Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1))));
        img.data[k] = img.data[k + 1] = img.data[k + 2] = Math.round(255 * t);
        img.data[k + 3] = 255;
      }
    g.putImageData(img, 0, 0);
  }, [stamp]);
  return <canvas ref={cv} class="tv-stamp" aria-hidden="true" />;
}

/** An anomaly's odd pictures: the stamps (the file's own pixels around it) of the exposures of its odd bins. */
export function StampsView({ src, drivers, onShown }: { src: Source | null; drivers: Anomaly['drivers'] | null; onShown?: (on: boolean) => void }) {
  const own = useSpectrum(src);
  const ranges = drivers?.map((d) => ({ lo: d.lo, hi: d.hi })) ?? [];
  const { inside } = useMemo(() => pointsIn(own.points, ranges), [own.points, drivers]);
  const { stamps, absent } = useStamps(src, own.view?.state === 'running');
  // the odd pictures: the points of the odd bins farthest from the spectrum's median
  const med = useMemo(() => {
    const f = own.points.filter((p) => p.ok && p.flux != null).map((p) => p.flux!).sort((a, b) => a - b);
    return f[Math.floor(f.length / 2)] ?? 0;
  }, [own.points]);
  const odd = inside
    .filter((p) => stamps.get(p.i)?.raw)
    .sort((a, b) => Math.abs((b.flux ?? 0) - med) - Math.abs((a.flux ?? 0) - med))
    .slice(0, MAX_STAMPS);
  useEffect(() => {
    onShown?.(odd.length > 0 || absent);
    return () => onShown?.(false);
  }, [odd.length > 0, absent]);
  const v = own.view;
  const line = odd.length
    ? `${odd.length} of its ${inside.length} odd pictures · it is in the middle of each`
    : absent
      ? 'Its pictures are not kept'
      : !drivers
        ? 'looking for it in this field…'
        : v?.state === 'running' && v.total
          ? `getting its pictures: ${v.measured} of ${v.total}…`
          : 'getting its pictures…';
  return (
    <div class="tv tv-fixed" data-testid="task-stamps">
      <div class="tv-stamps">
        {Array.from({ length: MAX_STAMPS }, (_, k) => odd[k]).map((p, k) =>
          p ? (
            <figure key={p.i} class="tv-st" title={`${new Date((p.mjd - 40587) * 86400000).toISOString().slice(0, 10)} · D${p.det} · ${p.lam.toFixed(2)} µm`}>
              <RawStamp stamp={stamps.get(p.i)!} />
              <figcaption style={{ color: detColour(p.det) }}>D{p.det}</figcaption>
            </figure>
          ) : (
            <figure key={`e${k}`} class="tv-st empty" />
          ),
        )}
      </div>
      <div class="tv-line" data-testid="task-stamps-line">
        {line}
      </div>
    </div>
  );
}

/** "Does it really move?": what the Blink and the ruler were set to, and what the search measured. */
export function MoverView({ blink, ruler, search }: { blink: string; ruler: string; search: string }) {
  return (
    <div class="tv" data-testid="task-mover">
      <div class="tv-row">
        <b>Blink</b>
        <span>{blink}</span>
      </div>
      <div class="tv-row">
        <b>Ruler</b>
        <span>{ruler}</span>
      </div>
      <div class="tv-row">
        <b>Search</b>
        <span>{search}</span>
      </div>
    </div>
  );
}

interface ClipInfo {
  url?: string;
  state?: string;
  reason?: string;
  blink?: string[] | null;
  shows?: { frames?: { label?: string; sn?: number }[]; visits_added?: number } | null;
}

/** "Is this a real object?": the finding's own clip from the service. For a track or a Planet X peak it blinks the picture added
 *  along the motion against the same exposures added without it: a real mover is there in the first and gone in the second. */
export function FindingClip({ id, onShown }: { id: string | null; onShown?: (on: boolean) => void }) {
  const info = useClip(id);
  const pair = pairOf(id, info);
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [id]);
  const none = info?.state === 'none' || broken;
  // a find whose pictures are made now (minutes): said, with what to look at meanwhile
  const [late, setLate] = useState(false);
  useEffect(() => {
    setLate(false);
    const t = setTimeout(() => setLate(true), 12000);
    return () => clearTimeout(t);
  }, [id]);
  const [loaded, setLoaded] = useState(0);
  useEffect(() => setLoaded(0), [id]);
  const on = none || (pair ? loaded >= 2 : loaded >= 1);
  useEffect(() => {
    onShown?.(on);
    return () => onShown?.(false);
  }, [on]);
  const line = !id
    ? ''
    : none
      ? `These two pictures cannot be made here (${info?.reason ?? 'not kept'}) · judge it on the sky itself`
      : on
        ? ''
        : late
          ? 'Our server is making these two pictures (a few minutes) · the sky itself is on the left'
          : 'getting the pictures…';
  const up = () => setLoaded((n) => n + 1);
  return (
    <div class={`tv tv-clip${pair ? ' two' : ''}`} data-testid="task-clip">
      {pair ? (
        <div class="tv-pair">
          {pair.map((q) => (
            <figure key={q.url}>
              <img src={q.url} alt={q.word} onLoad={up} onError={() => setBroken(true)} />
              <figcaption>{q.word}</figcaption>
            </figure>
          ))}
        </div>
      ) : (
        <div class="tv-clipbox">{info?.state === 'ready' && info.url ? <img src={info.url} alt="The find, picture after picture" onLoad={up} /> : null}</div>
      )}
      {line && <div class="tv-line wrap">{line}</div>}
    </div>
  );
}

/** The clip of a find as the service says it, asked until it is made. */
function useClip(id: string | null): ClipInfo | null {
  const [info, setInfo] = useState<ClipInfo | null>(null);
  useEffect(() => {
    setInfo(null);
    if (!id) return;
    let stop = false;
    void (async () => {
      for (let i = 0; i < 60 && !stop; i++) {
        try {
          const r = await fetch(`/api/findings/${encodeURIComponent(id)}/clip`, { cache: 'no-store' });
          const d = r.ok ? ((await r.json()) as ClipInfo) : ({ state: 'none', reason: `the service answered ${r.status}` } as ClipInfo);
          if (stop) return;
          setInfo(d);
          if (d.state === 'ready' || d.state === 'none') return;
          // not made yet: asking for the picture itself puts it in the service's queue
          void fetch(`/api/findings/${encodeURIComponent(id)}/clip.webp`).catch(() => {});
        } catch {
          if (!stop) setInfo({ state: 'none', reason: 'the service did not answer' });
          return;
        }
        await new Promise((res) => setTimeout(res, 5000));
      }
    })();
    return () => {
      stop = true;
    };
  }, [id]);
  return info;
}

/** The two still pictures of a peak's or a track's clip, side by side: a moving pair of pictures was judged as one (24 Nov 2026). */
export function pairOf(id: string | null, info: ClipInfo | null): { url: string; word: string }[] | null {
  if (!id || info?.state !== 'ready' || !info.url) return null;
  const track = id.startsWith('film:');
  if (!track && !(id.startsWith('peak:') && info.blink?.length === 2)) return null;
  const at = (k: number) => `${info.url}${info.url!.includes('?') ? '&' : '?'}frame=${k}`;
  return [
    { url: at(0), word: 'Added up along its path' },
    { url: at(1), word: track ? 'Added up in place' : 'One picture' },
  ];
}

/** A Planet X peak on the picture: it stands only in the visits added along its orbit, never on one picture, so the sum (the find's
 *  own clip) is what the picture opens with; one button gives the sky back. Nothing where the sum is not kept. */
export function PeakSum({ id, track = false, tool = false, loading = false }: { id: string; track?: boolean; tool?: boolean; loading?: boolean }) {
  const pair = pairOf(id, useClip(id));
  const [sky, setSky] = useState(false);
  // a tap on one of the two makes it as large as the picture, another tap brings both back
  const [big, setBig] = useState(-1);
  useEffect(() => {
    setSky(false);
    setBig(-1);
  }, [id]);
  // Mark or the ruler needs the sky itself
  useEffect(() => {
    if (tool) setSky(true);
  }, [tool]);
  // the sky is asked for and still on its way: the loading card alone (one card over the picture, never two)
  if (!pair || (sky && loading)) return null;
  return (
    // while the sums stand over the sky, a click is not a click on the sky under them
    <div class={`peaksum${sky ? ' sky' : ''}`} data-testid="peak-sum" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
      {!sky && (
        <div class={`ps-pair${big >= 0 ? ' one' : ''}`}>
          {pair.map((q, k) => (
            <figure key={q.url} hidden={big >= 0 && big !== k} onClick={() => setBig(big === k ? -1 : k)} title={big === k ? 'Both pictures' : 'Larger'} data-testid={`sum-${k}`}>
              <img src={q.url} alt={q.word} />
              <figcaption>{q.word}</figcaption>
            </figure>
          ))}
        </div>
      )}
      <div class="ps-bar">
        <span>{sky ? 'This is the sky itself' : loading ? 'The sky itself is still loading' : 'The same place, two ways'}</span>
        <button class="btn primary" onClick={() => setSky(!sky)} data-testid="peak-sum-button">
          {sky ? 'Back to the two pictures' : track ? 'Show the visits' : 'Show the sky'}
        </button>
      </div>
    </div>
  );
}
