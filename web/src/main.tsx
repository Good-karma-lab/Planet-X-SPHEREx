import { render } from 'preact';
import { App } from './app';
import './style.css';
import { tiles } from './lib/hips';

const root = document.getElementById('app');
if (root) render(<App />, root);

// the whole-sky pictures of the surveys are fetched at once, before the globe asks: the default first, the others a moment later
tiles.preload(['dss2']);
new Image().src = '/globe/dust_allsky.jpg';
setTimeout(() => tiles.preload(['2mass', 'wise']), 2500);
