import { useEffect, useState } from 'preact/hooks';

// What people said about finds in the classification tasks ("People say: cold brown dwarf, 3 of 3"), from GET /api/tasks/results.
// One quiet line on a Findings card and on the finding's own page; a find nobody classified has no line.

export interface PeopleSay {
  label: string | null;
  agree: number;
  total: number;
  text: string;
}

/** The line as shown: the service's text with a capital first letter. */
export function peopleLine(p: PeopleSay | undefined): string {
  if (!p?.text) return '';
  return p.text.charAt(0).toUpperCase() + p.text.slice(1);
}

const MAX_IDS = 200;

/** What people said about these finds (asked once per set of ids; nothing when the accounts service does not answer). */
export function usePeople(ids: string[]): Record<string, PeopleSay> {
  const [out, setOut] = useState<Record<string, PeopleSay>>({});
  const key = ids.slice(0, MAX_IDS).join(',');
  useEffect(() => {
    if (!key) return;
    let stop = false;
    void fetch(`/api/tasks/results?ids=${encodeURIComponent(key)}`)
      .then(async (r) => {
        if (stop || !r.ok) return;
        const body = (await r.json()) as { results?: Record<string, PeopleSay> };
        if (!stop) setOut(body.results ?? {});
      })
      .catch(() => undefined);
    return () => {
      stop = true;
    };
  }, [key]);
  return out;
}
