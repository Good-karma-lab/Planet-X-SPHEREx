// Hash routes hold the whole state of a screen so that any view opens by a link (docs/plan/04-ui.md, 4.1 rule 9).

// 'me' is the cabinet (flow-accounts, stream B3)
export const SCREENS = ['landing', 'globe', 'lab', 'planetx', 'findings', 'index', 'method', 'about', 'me', 'leaders'] as const; // 'leaders' is the public leaderboard (flow-accounts)
export type Screen = (typeof SCREENS)[number];

export interface Route {
  screen: Screen;
  params: Record<string, string>;
}

export const DEFAULT_SCREEN: Screen = 'landing';

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const q = raw.indexOf('?');
  // #/explore is the globe (the landing page is the front door); #/globe stays valid for old links
  const name0 = (q < 0 ? raw : raw.slice(0, q)).toLowerCase();
  const name = name0 === 'explore' ? 'globe' : name0;
  const query = q < 0 ? '' : raw.slice(q + 1);
  const screen = (SCREENS as readonly string[]).includes(name) ? (name as Screen) : DEFAULT_SCREEN;
  const params: Record<string, string> = {};
  for (const [k, v] of new URLSearchParams(query)) params[k] = v;
  return { screen, params };
}

export function formatHash(route: Route): string {
  const query = new URLSearchParams(route.params).toString();
  const name = route.screen === 'globe' ? 'explore' : route.screen;
  return `#/${name}${query ? `?${query}` : ''}`;
}
