import { useEffect, useState } from 'preact/hooks';
import { rememberLab } from './lib/place';
import { getHealth, type Health } from './lib/api';
import { parseHash, type Route, type Screen } from './lib/route';
import { Globe } from './screens/globe';
import { Lab } from './screens/lab';
import { InfoPage } from './screens/pages';
import { Landing } from './screens/landing';
import { PlanetXPage } from './screens/planetx/PlanetX';
import { icon } from './ui/icons';
import { Cabinet } from './screens/cabinet/Cabinet';
import { FindingPage } from './screens/findings/page';
import { Leaders } from './screens/cabinet/Score';
import { ProfilePage, StoryPage } from './screens/cabinet/Share'; // flow-accounts: #/u/<slug> and #/findings/<id>/story
import { useUnread } from './lib/account';
import { MoreTab } from './ui/BottomNav';

const NAV: { screen: Screen; name: string; icon: string; tip: string }[] = [
  { screen: 'globe', name: 'Globe', icon: 'globe', tip: 'The sky: globe and catalogue' },
  { screen: 'lab', name: 'Lab', icon: 'chart-line-up', tip: 'Spectral lab' },
  { screen: 'planetx', name: 'Planet X', icon: 'planet-x', tip: 'Planet X: the Solar System, the predicted orbit and what we searched' },
  { screen: 'findings', name: 'Findings', icon: 'sparkle', tip: 'Findings: what the real data reveal' },
  { screen: 'index', name: 'Index', icon: 'database', tip: 'Exposure index: what the sky service can search' },
  { screen: 'method', name: 'Method', icon: 'sigma', tip: 'Method: how everything is computed' },
  { screen: 'about', name: 'About', icon: 'info', tip: 'About, data and credits' },
  { screen: 'me', name: 'Cabinet', icon: 'user', tip: 'Cabinet: your squares, marks, findings and history' },
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
  const unread = useUnread();
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    void getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);
  // the Lab keeps its own place in the address; returning to it from another screen restores the last field
  const [lastLab, setLastLab] = useState('#/lab');
  useEffect(() => {
    if (route.screen === 'lab' && route.params['ra']) {
      setLastLab(location.hash);
      rememberLab(location.hash);
    }
  }, [route]);
  // a screen opens at its top: the page's own scroller (a desktop) and the document (a phone) both keep the place of the screen before otherwise
  useEffect(() => {
    const t = setTimeout(() => {
      window.scrollTo(0, 0);
      document.querySelectorAll('.screen').forEach((e) => ((e as HTMLElement).scrollTop = 0));
    }, 0);
    return () => clearTimeout(t);
  }, [route.screen]);
  if (route.screen === 'landing') return <Landing />;
  return (
    <div class="app">
      <nav class="rail">
        <a class="brand" href="#/" title="SPHEREx Space: the front page">
          <span
            class="logo"
            dangerouslySetInnerHTML={{
              __html:
                '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.2"/><path d="M12 1.5v5M12 17.5v5M1.5 12h5M17.5 12h5"/></svg>',
            }}
          />
        </a>
        <div class="tabs">
          {NAV.map((n) => (
            <a
              key={n.screen}
              class={`tab ${route.screen === n.screen ? 'on' : ''} ${n.screen === 'me' ? 'tab-end' : ''}`}
              href={n.screen === 'lab' ? lastLab : n.screen === 'globe' ? '#/explore' : `#/${n.screen}`}
              title={n.tip}
              aria-current={route.screen === n.screen ? 'page' : undefined}
              data-testid={`nav-${n.screen}`}
            >
              <span dangerouslySetInnerHTML={{ __html: icon(n.icon, 26) }} style={{ display: 'contents' }} />
              <span>{n.name}</span>
              {n.screen === 'me' && unread > 0 && (
                <span class="navbadge" data-testid="nav-unread">
                  {unread}
                </span>
              )}
            </a>
          ))}
          <MoreTab screen={route.screen} />
        </div>
      </nav>
      <main data-screen={route.screen}>
        {route.screen === 'lab' ? (
          <Lab route={route} />
        ) : route.screen === 'globe' ? (
          <Globe />
        ) : route.screen === 'planetx' ? (
          <PlanetXPage />
        ) : route.screen === 'me' ? (
          <Cabinet route={route} />
        ) : route.screen === 'findings' && (route.params['id'] ?? '').endsWith('/story') ? (
          <StoryPage id={(route.params['id'] ?? '').slice(0, -'/story'.length)} />
        ) : route.screen === 'findings' && route.params['id'] ? (
          <div class="screen">
            <div class="ipage">
              <FindingPage id={route.params['id']} />
            </div>
          </div>
        ) : route.screen === 'leaders' ? (
          <Leaders route={route} />
        ) : route.screen === 'u' ? (
          <ProfilePage slug={route.params['id'] ?? ''} badge={route.params['badge']} />
        ) : (
          <InfoPage screen={route.screen} health={health} />
        )}
      </main>
    </div>
  );
}
