import { render } from 'preact';
import { App } from './app';
import { handOver, listenTabs } from './lib/tabs';
import './style.css';
import './responsive.css';
import { installLegend, installLoupe, installPinch, installStrips } from './ui/touch';
import { loadManifest, tiles } from './lib/hips';

// one address for the site: spherex.space opens as www.spherex.space, with its place kept. Sign-in comes back to www (Google's return
// address, the e-mail link), and a guest's marks live in the storage of the host they were made on: on two hosts a person signed in
// on one and found their place and marks on the other gone
if (location.hostname === 'spherex.space') location.replace(`https://www.spherex.space${location.pathname}${location.search}${location.hash}`);

// a tab opened before a deploy asks for the old build's lazy parts: if one cannot load, the page reloads once onto the new build
const RELOADED = 'spherex-reloaded-for-new-build';
const reloadOnce = () => {
  try {
    if (sessionStorage.getItem(RELOADED)) return;
    sessionStorage.setItem(RELOADED, '1');
  } catch {
    /* no storage: reload anyway */
  }
  location.reload();
};
window.addEventListener('vite:preloadError', (e) => {
  e.preventDefault();
  reloadOnce();
});
window.addEventListener('unhandledrejection', (e) => {
  const m = String((e.reason as Error | undefined)?.message ?? e.reason ?? '');
  if (/dynamically imported module|Importing a module script failed|error loading dynamically imported/i.test(m)) reloadOnce();
});
// a page that stayed up is on the current build: a later deploy may reload it again
setTimeout(() => {
  try {
    sessionStorage.removeItem(RELOADED);
  } catch {
    /* ignore */
  }
}, 15000);

// the tile manifest (and with it the star map's full-sky base image) is asked for before anything else: the globe shows the base the moment it arrives, then refines
void loadManifest();

// two fingers zoom the globe (phones and tablets); a mouse is not touched
installPinch();
installStrips();
installLoupe();
installLegend();

const root = document.getElementById('app');
// the address the sign-in link came back to, before the lab takes its `do=` out
const startHash = location.hash;
listenTabs();
// a sign-in link's tab whose place is already open in another tab: that tab goes on (signed in, with the pending click), this one says so
if (root) render(<App />, root);
void handOver(startHash).then((taken) => {
  // the lab stops at once (its loading is undone with it): the other tab already has the field
  if (root && taken) render(<SignedElsewhere />, root);
});

/** the sign-in link's own tab when the lab goes on in the other one */
function SignedElsewhere() {
  return (
    <div class="signed-elsewhere" data-testid="signed-elsewhere">
      <b>Signed in</b>
      <span class="mono-s">your lab is open in the other tab, where it goes on</span>
      <button class="btn sm" onClick={() => render(<App />, root!)}>
        Open the lab here too
      </button>
    </div>
  );
}

// the whole-sky pictures of the surveys are fetched before the globe asks, once the page itself has loaded (the landing paints first):
// the default first, the others a moment later, the SPHEREx frames last
const preload = () => {
  tiles.preload(['dss2']);
  new Image().src = '/globe/dust_allsky.jpg';
  setTimeout(() => tiles.preload(['2mass', 'wise']), 800);
  setTimeout(() => tiles.preload(['spx8', 'spx40', 'spx88']), 4000);
};
if (document.readyState === 'complete') preload();
else window.addEventListener('load', preload, { once: true });
