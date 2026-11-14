import { useEffect, useState } from 'preact/hooks';
import { getHealth, type Health } from './lib/api';
import { parseHash, SCREENS, type Route, type Screen } from './lib/route';
import { Lab } from './screens/lab';

const TITLES: Record<Screen, string> = { globe: 'Globe', lab: 'Lab', findings: 'Findings', method: 'Method' };

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}

type ServiceState = { kind: 'asking' } | { kind: 'ok'; health: Health } | { kind: 'down'; error: string };

function useService(): ServiceState {
  const [state, setState] = useState<ServiceState>({ kind: 'asking' });
  useEffect(() => {
    const ctl = new AbortController();
    getHealth(ctl.signal)
      .then((health) => setState({ kind: 'ok', health }))
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setState({ kind: 'down', error: e instanceof Error ? e.message : String(e) });
      });
    return () => ctl.abort();
  }, []);
  return state;
}

function statusLine(s: ServiceState): string {
  if (s.kind === 'asking') return 'asking the sky service…';
  if (s.kind === 'down') return `sky service did not answer · ${s.error}`;
  return `sky service ${s.health.version}`;
}

export function App() {
  const route = useRoute();
  const service = useService();
  return (
    <div class="shell">
      <header class="top">
        <a class="brand" href="#/globe">SPHEREx Sky</a>
        <nav>
          {SCREENS.map((s) => (
            <a key={s} href={`#/${s}`} class={s === route.screen ? 'on' : ''} aria-current={s === route.screen ? 'page' : undefined}>
              {TITLES[s]}
            </a>
          ))}
        </nav>
      </header>
      <main class="view" data-screen={route.screen}>
        {route.screen === 'lab' ? <Lab route={route} /> : <h1>{TITLES[route.screen]}</h1>}
      </main>
      <footer class="status" data-testid="status">{statusLine(service)}</footer>
    </div>
  );
}
