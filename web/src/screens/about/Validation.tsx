import { useLive } from '../findings/live';

/**
 * How we know it works: every check with its number. A number marked "live" is read from the service now; the others were measured once, on the date and field named
 * beside them (the Method page has the procedure), and are not recomputed here.
 */
export function Validation() {
  const live = useLive();
  const built = live.filter((l) => l.movers?.result);
  const tracks = built.flatMap((l) => l.movers!.result!.tracks);
  const same = tracks.filter((t) => t.check === 'same').length;
  const chance = built.map((l) => l.movers!.result!.chance).filter((c): c is { trials: number; mean: number; any: number } => !!c);
  const meanChance = chance.length ? chance.reduce((a, c) => a + c.mean, 0) / chance.length : null;
  const trials = chance.length ? Math.min(...chance.map((c) => c.trials)) : null;
  const Row = ({ k, v, live: isLive }: { k: string; v: string; live?: boolean }) => (
    <div class="orow">
      <span class="faint">
        {k}
        {isLive ? <span class="badge green" style="margin-left:6px">live</span> : null}
      </span>
      <span>{v}</span>
    </div>
  );
  return (
    <section class="card glass" id="works">
      <h3>How we know it works</h3>
      <p class="hint" style="margin-top:0">
        Every claim on this site is checked against something we did not make: Gaia, JPL, 2MASS, WISE, IRSA. Here are the checks and their numbers.
      </p>
      <div class="okv">
        <div class="search-h">A moving star moves the way Gaia says</div>
        <Row
          k="Tracks with a Gaia star of the same motion"
          v={built.length ? `${same} of ${tracks.length} tracks found on the ${built.length} test fields searched` : 'the test fields are not searched yet'}
          live
        />
        <Row k="Barnard's Star" v="10.08 ″/yr toward 355° measured on the stacks; SIMBAD 10.39 ″/yr toward 356° (3 passes, 386 exposures)" />
        <Row k="ε Indi B" v="4.58 ″/yr toward 122° measured; SIMBAD 4.68 ″/yr toward 122° (4 passes, 494 exposures)" />
        <Row k="Registration of the passes" v="0.14–0.26″ scatter on field stars; star width 8.8″ (FWHM, isolated unsaturated stars)" />
        <div class="search-h">Known asteroids are found, and counted the way IRSA counts them</div>
        <Row k="SPHEREx images of a known body" v="119, 107, 118 and 121 detector images for four asteroids on 14–20 Dec 2025: the same count as IRSA's Moving Object Search Tool" />
        <Row k="Blind search on the ecliptic, Dec 2025" v="4 of 4 known asteroids found without being told where to look, 0 false tracks" />
        <div class="search-h">What chance alone makes</div>
        <Row
          k="Tracks made by chance per field"
          v={meanChance != null ? `${meanChance.toFixed(2)} per field (the same search on ${trials} trials with every moving source put at a random place)` : 'no control run read yet'}
          live
        />
        <Row k="Pairs changing in place" v="the search on moved sources gives the rate of false pairs; the RW Aur pair, the one pair known to be real, changes by 4.5σ" />
        <div class="search-h">How many movers the search finds</div>
        <Row
          k="Injected movers (three 20′ fields)"
          v="a mover with a peak at 40× the stack noise is found in about 85 % of cases at 5–100″/yr; at 20× in 62–88 %; below 12× most are missed"
        />
        <div class="search-h">Spectra agree with the catalogues</div>
        <Row k="An ordinary star" v="the spectrum averaged under the 2MASS J, H, Ks and WISE W1 filters gives 0.99, 1.02, 1.03 and 0.985 of the catalogue flux" />
        <Row k="2MASS J17572542+0446559" v="42 mJy in D2 against J 11.53 (39 mJy)" />
        <Row k="Every spectrum on this site" v="the check against catalogues sits under it; a ratio far from 1 is marked, never hidden" />
        <div class="search-h">Where we are not sure</div>
        <Row k="Depth" v="one blind search of the Find Planet X kind reaches about V 20–21; a Planet Nine of the predicted brightness is fainter, and the margin over WISE is 1.4–1.7× only in D4 (3 µm)" />
        <Row k="A lead is not a discovery" v="a track no catalogue explains is a lead; nothing is called a discovery on this site" />
      </div>
      <p class="mono-s">
        Numbers without “live” were measured once and are not recomputed here; <a href="#/method">the Method page</a> has the procedure and the fields. Injection recall by magnitude will be added when the injection test is run on the service.
      </p>
    </section>
  );
}
