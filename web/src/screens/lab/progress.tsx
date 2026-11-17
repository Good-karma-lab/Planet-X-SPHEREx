// The state of every search and lookup of a field, in one place: the switches of the layers, the row at the top of Candidates and
// the picture's status line all read it. A search or lookup that did not answer is asked again by itself, with a growing pause,
// for as long as the page is open and seen; it never gives up and never stays silent.
import { useEffect, useRef, useState } from 'preact/hooks';

export interface SearchInfo {
  /** its name as the row and the status line say it ("asteroids") */
  name: string;
  state: 'idle' | 'running' | 'done' | 'failed';
  /** 0..1 while running, when it says how far */
  frac?: number | null;
  /** what it found, said after the switch ("3 unexplained", "1014") */
  count?: string;
  /** a failure on our own server (a 5xx, a job that failed), not an outside archive */
  ours?: boolean;
  /** while failed: seconds to the next try, and the try now */
  retry?: { nextIn: number | null; tryNow: () => void } | null;
}

/** The pause before the n-th new try (n = 0, 1, 2…): 15 s, 30 s, 60 s, then every 2 minutes. */
export function retryDelay(n: number): number {
  return n <= 0 ? 15 : n === 1 ? 30 : n === 2 ? 60 : 120;
}

/** Asks again by itself while `failed`: after retryDelay(0), retryDelay(1)… seconds; the clock stops while the page is hidden and
 *  a try that fell due meanwhile is made on return. `key` (the field) starts the schedule again. */
export function useRetry(failed: boolean, retry: () => void, key: string): { nextIn: number | null; tryNow: () => void } {
  const [due, setDue] = useState<number | null>(null);
  const tries = useRef(0);
  const [, tick] = useState(0);
  const retryRef = useRef(retry);
  retryRef.current = retry;
  useEffect(() => {
    tries.current = 0;
    setDue(null);
  }, [key]);
  useEffect(() => {
    if (!failed) {
      setDue(null);
      return;
    }
    setDue(Date.now() + retryDelay(tries.current) * 1000);
  }, [failed, key]);
  useEffect(() => {
    if (due === null) return;
    let left = due - Date.now();
    let last = Date.now();
    const t = setInterval(() => {
      const now = Date.now();
      // a hidden page does not count down
      if (typeof document === 'undefined' || document.visibilityState === 'visible') left -= now - last;
      last = now;
      tick((x) => x + 1);
      if (left <= 0) {
        clearInterval(t);
        tries.current++;
        setDue(null);
        retryRef.current();
      }
    }, 1000);
    return () => clearInterval(t);
  }, [due]);
  return {
    nextIn: due === null ? null : Math.max(0, Math.ceil((due - Date.now()) / 1000)),
    tryNow: () => {
      tries.current++;
      setDue(null);
      retryRef.current();
    },
  };
}

/** The words after a failed search: who did not answer, and when the next try is. */
export function failWord(s: SearchInfo): string {
  // a catalogue outside that is slow is asked again on its own: said as the normal wait it is, not as a failure
  if (!s.ours) return s.retry?.nextIn != null ? `slow · asking again in ${s.retry.nextIn} s` : 'no answer';
  return s.retry?.nextIn != null ? `server error · retry ${s.retry.nextIn} s` : 'server error';
}

/** One small mark after a switch or in the row: a turning ring while it runs (with its share), the count when done, the failure
 *  with its next try and a "try now". The same look everywhere. */
export function SearchMark({ s }: { s: SearchInfo | null | undefined }) {
  if (!s || s.state === 'idle') return null;
  if (s.state === 'running')
    return (
      <span class="sm-run" data-testid={`search-${s.name}`}>
        <i class="sm-ring" aria-hidden="true" />
        {s.frac != null ? `${Math.round(s.frac * 100)}%` : ''}
      </span>
    );
  if (s.state === 'failed')
    return (
      <span class={s.ours || s.retry?.nextIn == null ? 'sm-fail' : 'sm-slow'} data-testid={`search-${s.name}`} title={s.ours ? 'Our server did not answer; it is asked again by itself' : 'The archive did not answer; it is asked again by itself'}>
        {failWord(s)}
        {s.retry && (
          <>
            {' · '}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                s.retry!.tryNow();
              }}
              title="Ask again now"
            >
              now
            </a>
          </>
        )}
      </span>
    );
  return (
    <span class="sm-done" data-testid={`search-${s.name}`}>
      {s.count ?? '✓'}
    </span>
  );
}

/** The row of the searches at the top of Candidates: "movers ✓ · brown dwarfs ✓ · asteroids 21% · Planet X …". */
export function SearchRow({ list }: { list: SearchInfo[] }) {
  return (
    <div class="searchrow" data-testid="search-row">
      {list.map((s, k) => (
        <span key={s.name} class={`sr-item s-${s.state}`}>
          {k > 0 && <em aria-hidden="true">·</em>}
          {s.name}{' '}
          <b>{s.state === 'done' ? (s.count ?? '✓') : s.state === 'running' ? (s.frac != null ? `${Math.round(s.frac * 100)}%` : '…') : s.state === 'failed' ? <SearchMark s={s} /> : '–'}</b>
        </span>
      ))}
    </div>
  );
}

/** The picture's status words while anything runs: "searching: asteroids 21% · Planet X 40%" ('' when all are done). */
export function searchingLine(list: SearchInfo[]): string {
  const run = list.filter((s) => s.state === 'running');
  if (!run.length) return '';
  return `searching ${run.map((s) => `${s.name}${s.frac != null ? ` ${Math.round(s.frac * 100)}%` : ''}`).join(' · ')}`;
}
