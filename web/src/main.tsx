import { render } from 'preact';
import { App } from './app';
import './style.css';
import './responsive.css';
import { installLoupe, installPinch, installStrips } from './ui/touch';
import { loadManifest, tiles } from './lib/hips';

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

const root = document.getElementById('app');
if (root) render(<App />, root);

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
