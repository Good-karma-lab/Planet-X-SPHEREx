import { Validation } from './Validation';

const lab = (q: string) => `#/lab?${q}`;

/** The About page, written for people who have never seen SPHEREx: what this is, what to try, what is real, where it is weak. */
export function AboutBody() {
  return (
    <>
      <div class="pg-h">
        <h1>About</h1>
        <p class="hint">
          SPHEREx Sky shows what NASA's SPHEREx telescope really saw: pick any patch of the sky and watch it change over the seasons, in colour, and find the things that
          move. Nothing here is simulated. Not affiliated with or endorsed by NASA.
        </p>
      </div>
      <div class="pg-grid">
        <section class="card glass">
          <h3>What is SPHEREx?</h3>
          <p>
            SPHEREx is a NASA space telescope that photographs the whole sky again and again, each time through a filter that splits the light into 102 colours between
            0.75 and 5 µm (infrared, invisible to the eye). So every point of the sky gets a spectrum: a fingerprint that says what it is made of and how hot it is.
          </p>
          <p>
            The sky is covered about every six months. That makes it a movie with a frame every season: a star that drifts, a comet, an asteroid, or a faraway world
            moving slowly around the Sun shows up as something that is not where it was.
          </p>
        </section>
        <section class="card glass">
          <h3>What you can do here</h3>
          <ol>
            <li>
              <b>Globe:</b> pick a place (or a famous object: Barnard's Star, a brown dwarf, comet 3I/ATLAS) and open it in the lab.
            </li>
            <li>
              <b>Lab:</b> see the field as a picture, a wall of bands, or the difference between seasons; click any source for its spectrum. Try{' '}
              <a href={lab('ra=133.75999&dec=-7.2422&fov=5')}>WISE 0855−0714</a>, the coldest known brown dwarf.
            </li>
            <li>
              <b>Searches:</b> the site looks for movers between seasons, inside a season (days), pairs of stars whose brightness changes, odd spectra, and, in{' '}
              <b>Find Planet X</b>, bodies far beyond Neptune.
            </li>
            <li>
              <b>Findings:</b> everything the searches found, with its checks.
            </li>
          </ol>
        </section>
        <section class="card glass">
          <h3>What is real</h3>
          <ul>
            <li>All pictures are real SPHEREx exposures from the public archive; none is drawn or illustrative.</li>
            <li>Stars (Gaia DR3), names (SIMBAD), asteroids and comets (JPL Small-Body Database and Horizons) and the older infrared pictures (DSS2, 2MASS, WISE) come from their own archives.</li>
            <li>Colours are false colours: each wavelength has its hue; nothing is “what the eye would see”.</li>
            <li>A track that no catalogue explains is a <b>lead</b>, not a discovery. We say how often chance alone would make one.</li>
          </ul>
        </section>
        <section class="card glass">
          <h3>Where it is weak</h3>
          <ul>
            <li>The search for very distant bodies reaches about magnitude 20–21; a Planet Nine of the predicted brightness would be fainter.</li>
            <li>Bands are broad: a molecule is never claimed from a band picture.</li>
            <li>Large fields take minutes the first time; the five test targets are ready in seconds.</li>
          </ul>
        </section>
        <section class="card glass">
          <h3>Words used here</h3>
          <dl class="kv">
            <div class="kvr"><dt>Exposure</dt><dd>one detector's picture of one pointing</dd></div>
            <div class="kvr"><dt>Visit</dt><dd>a few exposures taken minutes apart</dd></div>
            <div class="kvr"><dt>Pass (season)</dt><dd>all visits of a place within one stretch of months</dd></div>
            <div class="kvr"><dt>Band</dt><dd>a wavelength window with a plain name (Starlight, Methane, Water ice…)</dd></div>
            <div class="kvr"><dt>Mover / track</dt><dd>a source that is somewhere else in each pass, on a straight line</dd></div>
            <div class="kvr"><dt>Lead</dt><dd>a track or pair a search found; checked, not proven</dd></div>
            <div class="kvr"><dt>Chance</dt><dd>how many false tracks the same search makes on shuffled data</dd></div>
          </dl>
        </section>
        <section class="card glass">
          <h3>Data and credits</h3>
          <ul>
            <li>SPHEREx Level-2 spectral images: NASA / JPL-Caltech, served by NASA/IPAC Infrared Science Archive (IRSA).</li>
            <li>Object catalogue: SIMBAD, CDS Strasbourg. Stars: Gaia DR3. Small bodies: JPL SBDB and Horizons; Moving Object Search Tool: IRSA.</li>
            <li>Globe backgrounds: DSS2, 2MASS, AllWISE and IRIS all-sky surveys (HiPS), CDS and IRSA.</li>
            <li>Comet 3I/ATLAS frame: Canadian Astronomy Data Centre (CSA's NEOSSat).</li>
          </ul>
        </section>
        <section class="card glass">
          <h3>Team</h3>
          <ul>
            <li>Good Karma Lab · NASA Space Apps Challenge 2026, Barcelona.</li>
            <li>Licence: Apache-2.0. Icons: Phosphor (MIT).</li>
          </ul>
        </section>
      </div>
      <Validation />
    </>
  );
}
