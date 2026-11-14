import { useEffect, useState } from 'preact/hooks';
import { getHealth, type Health } from './lib/api';
import { parseHash, type Route, type Screen } from './lib/route';
import { Globe } from './screens/globe';
import { Lab } from './screens/lab';
import { InfoPage } from './screens/pages';
import { icon } from './ui/icons';

const NAV: { screen: Screen; name: string; icon: string; tip: string }[] = [
  { screen: 'globe', name: 'Globe', icon: 'globe', tip: 'Globe & catalog' },
  { screen: 'lab', name: 'Lab', icon: 'chart-line-up', tip: 'Spectral lab' },
  { screen: 'findings', name: 'Findings', icon: 'sparkle', tip: 'Findings: what the real data reveal' },
  { screen: 'index', name: 'Index', icon: 'database', tip: 'Exposure index: what the sky service can search' },
  { screen: 'method', name: 'Method', icon: 'sigma', tip: 'Method: how everything is computed' },
  { screen: 'about', name: 'About', icon: 'info', tip: 'About, data and credits' },
];

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}

export function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    void getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);
  // the Lab keeps its own place in the address; returning to it from another screen restores the last field
  const [lastLab, setLastLab] = useState('#/lab');
  useEffect(() => {
    if (route.screen === 'lab' && route.params['ra']) setLastLab(location.hash);
  }, [route]);
  return (
    <div class="app">
      <nav class="rail">
        <a class="brand" href="#/globe" title="SPHEREx Sky">
          <span class="logo" dangerouslySetInnerHTML={{ __html: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.2"/><path d="M12 1.5v5M12 17.5v5M1.5 12h5M17.5 12h5"/></svg>' }} />
        </a>
        <div class="tabs">
          {NAV.map((n) => (
            <a
              key={n.screen}
              class={`tab ${route.screen === n.screen ? 'on' : ''}`}
              href={n.screen === 'lab' ? lastLab : `#/${n.screen}`}
              title={n.tip}
              aria-current={route.screen === n.screen ? 'page' : undefined}
              data-testid={`nav-${n.screen}`}
            >
              <span dangerouslySetInnerHTML={{ __html: icon(n.icon, 26) }} style={{ display: 'contents' }} />
              <span>{n.name}</span>
            </a>
          ))}
        </div>
      </nav>
      <main data-screen={route.screen}>
        {route.screen === 'lab' ? <Lab route={route} /> : route.screen === 'globe' ? <Globe /> : <InfoPage screen={route.screen} health={health} />}
      </main>
    </div>
  );
}
