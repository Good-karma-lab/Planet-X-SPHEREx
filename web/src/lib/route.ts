// Hash routes hold the whole state of a screen so that any view opens by a link (docs/plan/04-ui.md, 4.1 rule 9).

// 'me' is the cabinet, 'leaders' the public leaderboard (#/leaders)
// 'missing' is an address that names no screen: it says so, with the ways back (an unknown address used to open the landing page silently)
export const SCREENS = ['landing', 'globe', 'lab', 'planetx', 'findings', 'index', 'method', 'about', 'me', 'leaders', 'u', 'missing'] as const; // 'u' is a public profile, #/u/<slug> (flow-accounts)
export type Screen = (typeof SCREENS)[number];

export interface Route {
  screen: Screen;
  params: Record<string, string>;
}

export const DEFAULT_SCREEN: Screen = 'landing';

// other names people type or links carry for a screen: #/explore is the globe (the landing page is the front door), #/globe stays valid for old links
const ALIASES: Record<string, Screen> = {
  explore: 'globe',
  cabinet: 'me',
  account: 'me',
  profile: 'me',
  'planet-x': 'planetx',
  'planet-nine': 'planetx',
  planet9: 'planetx',
  leaderboard: 'leaders',
  finds: 'findings',
  home: 'landing',
};

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const q = raw.indexOf('?');
  const path = q < 0 ? raw : raw.slice(0, q);
  // #/findings/<id> is the permanent page of one finding: the id after the screen's name (case kept), as params.id
  const slash = path.indexOf('/');
  const name0 = (slash < 0 ? path : path.slice(0, slash)).toLowerCase();
  const name = ALIASES[name0] ?? name0;
  const query = q < 0 ? '' : raw.slice(q + 1);
  const known = (SCREENS as readonly string[]).includes(name) && name !== 'missing';
  const screen: Screen = !name ? DEFAULT_SCREEN : known ? (name as Screen) : 'missing';
  const params: Record<string, string> = {};
  if (screen === 'missing') params['path'] = path;
  for (const [k, v] of new URLSearchParams(query)) params[k] = v;
  if ((screen === 'findings' || screen === 'u') && slash >= 0 && path.length > slash + 1) {
    try {
      params['id'] = decodeURIComponent(path.slice(slash + 1));
    } catch {
      params['id'] = path.slice(slash + 1);
    }
  }
  return { screen, params };
}

export function formatHash(route: Route): string {
  const { id, ...rest } = route.params;
  const one = route.screen === 'findings' && id;
  const query = new URLSearchParams(one ? rest : route.params).toString();
  const name = route.screen === 'globe' ? 'explore' : route.screen;
  return `#/${name}${one ? `/${encodeURIComponent(id)}` : ''}${query ? `?${query}` : ''}`;
}
