// Hash routes hold the whole state of a screen so that any view opens by a link (docs/plan/04-ui.md, 4.1 rule 9).

export const SCREENS = ['globe', 'lab', 'findings', 'method'] as const;
export type Screen = (typeof SCREENS)[number];

export interface Route {
  screen: Screen;
  params: Record<string, string>;
}

export const DEFAULT_SCREEN: Screen = 'globe';

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const q = raw.indexOf('?');
  const name = (q < 0 ? raw : raw.slice(0, q)).toLowerCase();
  const query = q < 0 ? '' : raw.slice(q + 1);
  const screen = (SCREENS as readonly string[]).includes(name) ? (name as Screen) : DEFAULT_SCREEN;
  const params: Record<string, string> = {};
  for (const [k, v] of new URLSearchParams(query)) params[k] = v;
  return { screen, params };
}

export function formatHash(route: Route): string {
  const query = new URLSearchParams(route.params).toString();
  return `#/${route.screen}${query ? `?${query}` : ''}`;
}
