// The "More" item of the phone's bottom bar. A phone's bar holds five destinations (Globe, Lab, Findings, More, Cabinet); the
// pages that are not steps of the search (Planet X, Index, Method, About, the front page) open from here. On tablets and desktops
// the rail shows every page and this item is not displayed (responsive.css).
import { useEffect, useState } from 'preact/hooks';
import type { Screen } from '../lib/route';
import { icon } from './icons';

const MORE: { screen: Screen; name: string; icon: string; href: string }[] = [
  { screen: 'planetx', name: 'Planet X', icon: 'planet-x', href: '#/planetx' },
  { screen: 'index', name: 'Index', icon: 'database', href: '#/index' },
  { screen: 'method', name: 'Method', icon: 'sigma', href: '#/method' },
  { screen: 'about', name: 'About', icon: 'info', href: '#/about' },
];

export const inMore = (screen: Screen): boolean => MORE.some((m) => m.screen === screen);

export function MoreTab({ screen }: { screen: Screen }) {
  const [open, setOpen] = useState(false);
  // the sheet closes when a page opens, and by Escape
  useEffect(() => setOpen(false), [screen]);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [open]);
  return (
    <>
      <button
        type="button"
        class={`tab tab-more ${inMore(screen) || open ? 'on' : ''}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        data-testid="nav-more"
      >
        <span
          style={{ display: 'contents' }}
          dangerouslySetInnerHTML={{
            __html:
              '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>',
          }}
        />
        <span>More</span>
      </button>
      {open && (
        <div class="more-layer" onClick={() => setOpen(false)}>
          <div class="more-sheet" role="dialog" aria-label="More pages" data-testid="more-sheet" onClick={(e) => e.stopPropagation()}>
            <div class="more-head">
              <b>More</b>
              <button type="button" class="more-x" aria-label="Close" onClick={() => setOpen(false)}>
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
            {MORE.map((m) => (
              <a key={m.screen} class={`more-row ${screen === m.screen ? 'on' : ''}`} href={m.href} aria-current={screen === m.screen ? 'page' : undefined} data-testid={`more-${m.screen}`}>
                <span style={{ display: 'contents' }} dangerouslySetInnerHTML={{ __html: icon(m.icon, 24) }} />
                <span>{m.name}</span>
              </a>
            ))}
            <a class="more-row" href="#/" data-testid="more-front">
              <span
                style={{ display: 'contents' }}
                dangerouslySetInnerHTML={{
                  __html:
                    '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.2"/><path d="M12 1.5v5M12 17.5v5M1.5 12h5M17.5 12h5"/></svg>',
                }}
              />
              <span>Front page</span>
            </a>
          </div>
        </div>
      )}
    </>
  );
}
