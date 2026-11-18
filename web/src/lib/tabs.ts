// A sign-in link opens a new tab, while the lab is already open (and loaded) in the tab the person asked from. The new tab asks the
// site's other tabs whether one shows the same place as a guest; if so, that tab takes over (it reads the account again and does the
// click the link carries: the mark, the answer) and the new tab says so instead of loading the whole field a second time.
import { currentUser, loadUser } from './account';

const CH = 'spherex-tabs';
/** a lab address without what the sign-in round trip adds (`do`, `signin`): the place itself */
const placeOf = (hash: string) => {
  const [name, q = ''] = hash.replace(/^#\/?/, '').split('?');
  const p = new URLSearchParams(q);
  p.delete('do');
  p.delete('signin');
  p.sort();
  return `${name}?${p.toString()}`;
};

/** Every tab answers who it is; a guest tab on the same place takes over a sign-in that came back elsewhere. */
export function listenTabs(): void {
  if (typeof BroadcastChannel === 'undefined') return;
  const ch = new BroadcastChannel(CH);
  ch.onmessage = (e: MessageEvent<{ type: string; place?: string; hash?: string; id?: string }>) => {
    const m = e.data;
    if (m?.type !== 'signed-in' || !m.place || !m.hash) return;
    // only a guest tab on that very place: it now reads the account (the cookie is shared) and does what the link carries
    if (currentUser() || placeOf(location.hash) !== m.place) return;
    ch.postMessage({ type: 'taken', id: m.id });
    void loadUser().then(() => {
      if (m.hash !== location.hash) location.hash = m.hash!.replace(/[?&]signin=[^&]*/, '');
    });
  };
}

/** In a tab opened by the sign-in link: true when another tab took over (this one then shows a short note, not the lab). */
export async function handOver(hash: string): Promise<boolean> {
  if (typeof BroadcastChannel === 'undefined' || !/^#\/lab\?/.test(hash)) return false;
  // a fresh tab with no history of its own (a mail client's new tab; a blank tab and the link: two entries)
  if (history.length > 2 || document.referrer) return false;
  const u = await loadUser().catch(() => null);
  if (!u) return false;
  const ch = new BroadcastChannel(CH);
  const id = Math.random().toString(36).slice(2);
  const taken = await new Promise<boolean>((done) => {
    const t = setTimeout(() => done(false), 400);
    ch.onmessage = (e: MessageEvent<{ type: string; id?: string }>) => {
      if (e.data?.type === 'taken' && e.data.id === id) {
        clearTimeout(t);
        done(true);
      }
    };
    ch.postMessage({ type: 'signed-in', place: placeOf(hash), hash, id });
  });
  ch.close();
  return taken;
}
