import { useEffect, useRef } from 'preact/hooks';
import { formatDec, formatRA } from '../../lib/sky';
import './search.css';

interface Core {
  ra: number;
  dec: number;
  /** the amplitude of a Gaussian of 10″ fitted at its place in each pass's white stack */
  flux_per_pass: (number | null)[];
  simbad?: { name?: string; otype?: string } | null;
  gaia?: { source_id?: string; g?: number | string | null } | null;
}

/** Two sources close together whose relative brightness changed between the passes: the peak seems to move, nothing does. */
export interface Pair {
  separation_arcsec: number;
  components: [Core, Core];
  verdict?: string;
  peak_shift_arcsec?: number | null;
  centroid_shift_arcsec?: number | null;
  ratio_per_pass?: (number | null)[];
  /** per detector, the change of ln(ratio) from pass to pass */
  dln_ratio_per_detector?: Record<string, (number | null)[]>;
  /** the same place in every pass: size × size bytes per pass as base64 (0 = no data, 1..255 grey), box_arcsec wide */
  crops?: { size: number; box_arcsec: number; data: (string | null)[] };
  note?: string;
}

const fluxText = (a?: (number | null)[]) =>
  (a ?? []).map((v) => (v == null ? '–' : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1))).join(' → ');
const coreName = (c: Core) => c.simbad?.name || (c.gaia?.source_id ? `Gaia DR3 ${c.gaia.source_id}` : null);
const ut = (m: number) => new Date((m - 40587) * 864e5).toISOString().slice(0, 10);

function Crop({ b64, n, title, caption }: { b64: string; n: number; title: string; caption: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const bin = atob(b64);
    const c = cv.getContext('2d')!;
    const im = c.createImageData(n, n);
    for (let q = 0; q < n * n; q++) {
      const v = bin.charCodeAt(q);
      if (!v) im.data.set([10, 22, 30, 255], q * 4);
      else {
        const g = ((v - 1) / 254) * 255;
        im.data.set([g, g, g * 0.98, 255], q * 4);
      }
    }
    c.putImageData(im, 0, 0);
  }, [b64, n]);
  return (
    <figure class="pair-crop">
      <canvas ref={ref} width={n} height={n} title={title} />
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

/**
 * The card of a changing pair: what the data say, why nothing moves, the two cores with their catalogue names, the ratio by pass and by detector, the same
 * place in every pass. Renders from the pair object only; the owner decides what "Spectrum of core" and "Zoom on the pair" do.
 */
export function PairCard({
  pair: p,
  mjds,
  zoomed,
  onSpectrum,
  onZoom,
  onBack,
}: {
  pair: Pair;
  /** the dates of the passes (MJD), for the captions of the cut-outs */
  mjds?: number[];
  zoomed?: boolean;
  onSpectrum: (ra: number, dec: number) => void;
  onZoom?: () => void;
  onBack?: () => void;
}) {
  const [A, B] = p.components;
  const perDet = Object.entries(p.dln_ratio_per_detector ?? {})
    .map(([d, a]) => {
      const v = a && a[a.length - 1];
      return v == null ? null : `${d} ${v >= 0 ? '+' : ''}${Math.round((Math.exp(v) - 1) * 100)} %`;
    })
    .filter((x): x is string => x != null);
  const row = (k: string, v: string, tip: string) => (
    <div class="orow" title={tip}>
      <span class="faint">{k}</span>
      <span>{v}</span>
    </div>
  );
  return (
    <div class="paircard">
      <div class="ohead">
        <div>
          <b class="oname">Brightness changed between the passes</b>
          <div class="mono-s">a pair of sources {p.separation_arcsec.toFixed(1)}″ apart · nothing moves</div>
        </div>
        <span class="badge orange" title="A lead: the search first saw a track here; the two cores are the explanation, not a moving body">
          Pair
        </span>
      </div>
      <div class="okv">
        {row('Kind', 'Change of brightness in place', 'one of the four kinds of finds')}
        {row(
          'What the data say',
          p.verdict || 'unknown',
          'steady: the ratio of the two cores changes by less than 10 %; “component varies”: the same change in every detector (a variable star); “depends on the detector”: a variable with a colour, or a different wavelength mix in the passes: compare the two spectra pass by pass',
        )}
        {row(
          'Moving?',
          `no: the peak hops ${p.peak_shift_arcsec != null ? `${p.peak_shift_arcsec.toFixed(1)}″` : ''} between the cores; the light-weighted centre moves ${p.centroid_shift_arcsec != null ? `${p.centroid_shift_arcsec.toFixed(1)}″` : 'little'}`,
          'a mover shifts the whole blob; a changing pair shifts only the peak',
        )}
        {[A, B].map((c, i) =>
          row(
            `Core ${i + 1}${coreName(c) ? ` · ${coreName(c)}` : ''}`,
            `${fluxText(c.flux_per_pass)} (per pass)`,
            `the amplitude of a Gaussian of 10″ fitted at its place in each pass's white stack${c.simbad?.otype ? ` · SIMBAD: ${c.simbad.otype}` : ''}${c.gaia?.g ? ` · Gaia G ${(+c.gaia.g).toFixed(2)}` : ''}`,
          ),
        )}
        {p.ratio_per_pass && row('Core 2 ÷ core 1', p.ratio_per_pass.map((v) => (v == null ? '–' : v.toFixed(2))).join(' → '), 'per pass, in the white stack')}
        {perDet.length > 0 &&
          row('By detector', perDet.join(' · '), 'the change of the ratio from the first to the last pass in each detector alone: the same in all six = one variable star; different = a colour')}
        {row('Position', `${formatRA(A.ra)}  ${formatDec(A.dec)}`, 'ICRS, core 1')}
      </div>
      <div class="chips" style="margin:8px 0">
        {[A, B].map((c, i) => (
          <button key={i} class="chip" title="Measure a spectrum at the place of this core" onClick={() => onSpectrum(c.ra, c.dec)}>
            Spectrum of core {i + 1}
          </button>
        ))}
      </div>
      {p.crops?.data && (
        <>
          <div class="search-h">The same place in every pass</div>
          <div class="pair-crops">
            {p.crops.data.map((b, e) =>
              b ? (
                <Crop key={e} b64={b} n={p.crops!.size} title={`Pass ${e + 1}`} caption={`pass ${e + 1}${mjds?.[e] != null ? ` · ${ut(mjds[e]!)}` : ''}`} />
              ) : null,
            )}
          </div>
          <div class="mono-s">{(p.crops.box_arcsec / 60).toFixed(1)}′ wide</div>
        </>
      )}
      {p.note && <div class="mono-s" style="margin-top:8px">{p.note}</div>}
      <div class="chips" style="margin-top:10px">
        {onZoom && (
          <button class="btn sm" title="The inset shows the pair from 4 to 12 times magnified, so that both cores can be seen" onClick={onZoom}>
            {zoomed ? 'Normal inset' : 'Zoom on the pair'}
          </button>
        )}
        {onBack && (
          <button class="btn sm" onClick={onBack}>
            Back to the list
          </button>
        )}
      </div>
    </div>
  );
}

/** One line of the list of pairs: "Pair · brightness changed · cores 4.2″ apart · steady". */
export function PairButton({ pair: p, on, onClick }: { pair: Pair; on?: boolean; onClick: () => void }) {
  return (
    <button
      class={`btn sm leadbtn${on ? ' on' : ''}`}
      onClick={onClick}
      title="Two sources close together whose relative brightness changed between the passes: the peak seems to move, nothing does"
    >
      <b>Pair · brightness changed</b> · cores {p.separation_arcsec.toFixed(1)}″ apart · {p.verdict ? p.verdict.replace(/ \(.*$/, '') : ''}
    </button>
  );
}
