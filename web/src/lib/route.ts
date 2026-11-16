// Hash routes hold the whole state of a screen so that any view opens by a link (docs/plan/04-ui.md, 4.1 rule 9).

// 'me' is the cabinet (flow-accounts, stream B3)
export const SCREENS = ['landing', 'globe', 'lab', 'planetx', 'findings', 'index', 'method', 'about', 'me'] as const;
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
  const path = q < 0 ? raw : raw.slice(0, q);
  // #/findings/<id> is the permanent page of one finding: the id after the screen's name (case kept), as params.id
  const slash = path.indexOf('/');
  const name0 = (slash < 0 ? path : path.slice(0, slash)).toLowerCase();
  const name = name0 === 'explore' ? 'globe' : name0;
  const query = q < 0 ? '' : raw.slice(q + 1);
  const screen = (SCREENS as readonly string[]).includes(name) ? (name as Screen) : DEFAULT_SCREEN;
  const params: Record<string, string> = {};
  for (const [k, v] of new URLSearchParams(query)) params[k] = v;
  if (screen === 'findings' && slash >= 0 && path.length > slash + 1) {
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
