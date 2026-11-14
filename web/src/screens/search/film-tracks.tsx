import { useEffect, useState } from 'preact/hooks';
import './search.css';
import { trackName, type FilmControl, type FilmTrack } from './tracks';

type Ask =
  | { state: 'loading'; phase: string }
  | { state: 'absent' }
  | { state: 'error'; error: string }
  | { state: 'ok'; tracks: FilmTrack[]; control: FilmControl | null };

/**
 * The tracks the search found inside a finished film (GET /api/v1/film/<key>/tracks: {state: running|done, phase, tracks, control}).
 * `absent`: the service has no such route yet, so nothing is drawn and the panel renders nothing.
 */
export function useFilmTracks(filmKey: string | null): Ask {
  const [a, setA] = useState<Ask>({ state: 'loading', phase: '' });
  useEffect(() => {
    setA({ state: 'loading', phase: '' });
    if (!filmKey) return;
    let stop = false;
    (async () => {
      for (let i = 0; i < 600 && !stop; i++) {
        const r = await fetch(`/api/v1/film/${filmKey}/tracks`, { cache: 'no-store' });
        if (stop) return;
        if (r.status === 404) return setA({ state: 'absent' });
        if (!r.ok) throw new Error(`service answered ${r.status}`);
        const d = (await r.json()) as { state: string; phase?: string; error?: string; tracks?: FilmTrack[]; control?: FilmControl | null };
        if (d.state === 'done') return setA({ state: 'ok', tracks: d.tracks ?? [], control: d.control ?? null });
        if (d.state === 'error') return setA({ state: 'error', error: d.error ?? 'the search failed' });
        setA({ state: 'loading', phase: d.phase ?? 'searching the visits' });
        await new Promise((res) => setTimeout(res, 2000));
      }
    })().catch((e) => {
      if (!stop) setA({ state: 'error', error: String(e.message ?? e) });
    });
    return () => {
      stop = true;
    };
  }, [filmKey]);
  return a;
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

/** What chance makes: the tracks of at least `min_visits_drawn` visits per field in the control runs. */
export function chanceText(c: FilmControl | null, nt: number): string {
  if (!c) return '';
  const ch = c.chance[String(c.min_visits_drawn)] ?? 0;
  return `${nt ? `${plural(nt, 'track')} through ${c.min_visits_drawn} visits or more` : `no track through ${c.min_visits_drawn} visits or more`} · chance makes ${ch.toFixed(ch < 10 ? 1 : 0)} such per field (${c.trials} control trials)`;
}

/** A track in one line: rate, visits, how it was verified, and whether a known body is there. */
export function trackLine(t: FilmTrack): string {
  const how =
    t.method === 'orbit'
      ? ` (on − off ${t.z_together}σ together, an orbit at ${t.dist_au} au fits to ${t.rms?.toFixed(1)}″)`
      : t.verified === false
        ? ' (a straight line, not verified by photometry)'
        : '';
  return `${t.rate.toFixed(1)}″/h through ${t.n} visits${how} — ${t.known ? `known: ${t.known.name}${t.known.V != null ? ` (V ${t.known.V})` : ''}` : 'no known body there (a lead, not a discovery)'}`;
}

/**
 * The found tracks of a film: one chip per track (its card opens through `onSel`), Trail and Blink, "Film around this track" in a smaller
 * field, and one status line that says what chance makes. Controlled: the owner of the film holds the switches and draws with drawTracks / drawBlink.
 */
export function FilmTracks({
  tracks,
  control,
  nVisits,
  fov,
  sel,
  onSel,
  trail,
  onTrail,
  blink,
  onBlink,
  onAround,
}: {
  tracks: FilmTrack[];
  control: FilmControl | null;
  nVisits: number;
  /** the field of the film, arcminutes */
  fov: number;
  sel: number;
  onSel: (i: number) => void;
  trail: boolean;
  onTrail: (on: boolean) => void;
  blink: boolean;
  onBlink: (on: boolean) => void;
  /** open the film of the same pass again around the chosen track, in a field of `fov` arcminutes */
  onAround: (track: FilmTrack, fov: number) => void;
}) {
  const nt = tracks.length;
  const tr = tracks[sel];
  return (
    <div class="ftracks">
      <div class="ft-line" title={tracks.map(trackLine).join('; ')}>
        {nVisits} visits · {nt ? `${plural(nt, 'track')} found` : 'no track found'}
        {control ? ` · ${chanceText(control, nt)}` : ''}
      </div>
      {nt === 0 && control && <div class="mono-s">Nothing is marked: the search found no track longer than what chance makes here. Look for a spot that hops.</div>}
      <div class="ft-switches">
        <label class="ft-sw" title="The last three places of each found body, fading behind it">
          <input type="checkbox" checked={trail} disabled={!nt} onChange={(e) => onTrail((e.target as HTMLInputElement).checked)} /> Trail
        </label>
        <label class="ft-sw" title="A strip of cut-outs of the chosen track: the same piece of sky in each visit it was found in, side by side, unmarked">
          <input type="checkbox" checked={blink} disabled={!nt} onChange={(e) => onBlink((e.target as HTMLInputElement).checked)} /> Blink
        </label>
      </div>
      {nt > 0 && (
        <div class="chips">
          {tracks.map((t, i) => (
            <button
              key={t.id}
              class={`chip${sel === i ? ' on' : ''}`}
              title={`${t.rate.toFixed(1)}″/h through ${t.n} visits: the card of this track`}
              onClick={() => onSel(i)}
            >
              {trackName(t, i)}
            </button>
          ))}
        </div>
      )}
      {nt > 0 && tr && fov > 10 && (
        <div class="chips" title="Open the film of the same pass again, centred on the middle place of the chosen track, in a smaller field: the body is several pixels there. The field is read again (minutes if it is new).">
          <span class="mono-s">Film around this track</span>
          {[10, 20]
            .filter((f) => f < fov)
            .map((f) => (
              <button key={f} class="chip" onClick={() => onAround(tr, f)}>
                {f}′
              </button>
            ))}
        </div>
      )}
    </div>
  );
}
