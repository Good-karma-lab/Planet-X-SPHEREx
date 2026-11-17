// Talk under a finding (spec §5e): short comments and links, so people help each other. Plain text; links become links (at most
// three in one comment); the author's public name only. GET /api/findings/{id}/comments (public), POST /api/me/findings/{id}/comments,
// DELETE /api/me/comments/{id} (mine), POST /api/me/comments/{id}/report (others': hidden after three people report it).
import { useEffect, useState } from 'preact/hooks';
import { api, useUser } from '../../lib/account';
import { SignInDialog } from '../../ui/SignInCard';

export interface Comment {
  id: number;
  name: string;
  text: string;
  at: string;
  mine: boolean;
  hidden: boolean;
}

/** the service's limits (service/accounts/people.py) */
export const TALK_MAX = 1000;
export const TALK_LINKS = 3;
const LINK = /https?:\/\/[^\s<>"']+/gi;

/** a comment's text as pieces: words, and links that open in a new tab */
export function pieces(text: string): { t: string; href?: string }[] {
  const out: { t: string; href?: string }[] = [];
  let at = 0;
  for (const m of text.matchAll(LINK)) {
    // a link's trailing full stop or bracket belongs to the sentence
    const url = m[0].replace(/[).,;:!?]+$/, '');
    if (m.index! > at) out.push({ t: text.slice(at, m.index) });
    out.push({ t: url.replace(/^https?:\/\//, ''), href: url });
    at = m.index! + url.length;
  }
  if (at < text.length) out.push({ t: text.slice(at) });
  return out;
}

const ago = (iso: string) => {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return '';
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

/** How many comments a finding has (null while asking, or when the service does not answer). */
export function useTalkCount(id: string): number | null {
  const [n, setN] = useState<number | null>(null);
  useEffect(() => {
    let stop = false;
    setN(null);
    if (!id) return;
    void fetch(`/api/findings/${encodeURIComponent(id)}/comments`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { comments?: Comment[] } | null) => !stop && setN(d?.comments ? d.comments.filter((c) => !c.hidden).length : null))
      .catch(() => {});
    return () => {
      stop = true;
    };
  }, [id]);
  return n;
}

export function Talk({ id, next }: { id: string; next: string }) {
  const { user } = useUser();
  const [list, setList] = useState<Comment[] | null | 'failed'>(null);
  const [text, setText] = useState('');
  const [line, setLine] = useState('');
  const [busy, setBusy] = useState(false);
  const [signIn, setSignIn] = useState(false);
  const load = () =>
    api<{ comments: Comment[] }>(`/api/findings/${encodeURIComponent(id)}/comments`)
      .then((d) => setList(d.comments))
      .catch(() => setList('failed'));
  useEffect(() => {
    setList(null);
    setText('');
    setLine('');
    void load();
  }, [id, user?.id]);
  const links = text.match(LINK)?.length ?? 0;
  const tooLong = text.length > TALK_MAX;
  const tooManyLinks = links > TALK_LINKS;
  const post = async () => {
    if (!text.trim() || busy || tooLong || tooManyLinks) return;
    setBusy(true);
    setLine('posting…');
    try {
      const c = await api<Comment>(`/api/me/findings/${encodeURIComponent(id)}/comments`, { method: 'POST', body: { text } });
      setList((l) => (Array.isArray(l) ? [...l, c] : [c]));
      setText('');
      setLine('');
    } catch (e) {
      // the service's own words: "wait a few seconds before the next comment", "plain text only: no HTML", "too many comments…"
      setLine(e instanceof Error ? e.message : 'The service did not take it');
    }
    setBusy(false);
  };
  const remove = async (c: Comment) => {
    try {
      await api(`/api/me/comments/${c.id}`, { method: 'DELETE' });
      setList((l) => (Array.isArray(l) ? l.filter((x) => x.id !== c.id) : l));
    } catch {
      setLine('The service did not answer');
    }
  };
  const [reported, setReported] = useState<number[]>([]);
  const report = async (c: Comment) => {
    try {
      const r = await api<{ hidden?: boolean }>(`/api/me/comments/${c.id}/report`, { method: 'POST' });
      setReported((x) => [...x, c.id]);
      if (r.hidden) setList((l) => (Array.isArray(l) ? l.filter((x) => x.id !== c.id) : l));
    } catch {
      setLine('The service did not answer');
    }
  };
  const shown = Array.isArray(list) ? list : [];
  return (
    <section class="talk" id="talk" data-testid="talk">
      <div class="talk-h mono-s">
        Talk
        {Array.isArray(list) && <span> · {shown.filter((c) => !c.hidden).length}</span>}
      </div>
      {list === null && <div class="mono-s faint">reading…</div>}
      {list === 'failed' && <div class="mono-s faint">Talk did not answer</div>}
      {Array.isArray(list) && !shown.length && <div class="mono-s faint">no comments yet</div>}
      <ol class="talk-list">
        {shown.map((c) => (
          <li key={c.id} class={c.hidden ? 'hidden' : ''} data-testid="talk-comment">
            <div class="talk-meta mono-s">
              <b>{c.name || 'someone'}</b> · <time dateTime={c.at}>{ago(c.at)}</time>
              {c.hidden && <span> · hidden after reports</span>}
              {c.mine ? (
                <button class="talk-act" onClick={() => void remove(c)} title="Remove my comment">
                  Remove
                </button>
              ) : (
                user && (
                  <button
                    class="talk-act"
                    disabled={reported.includes(c.id)}
                    onClick={() => void report(c)}
                    title="Spam or rude: hidden once three people report it"
                  >
                    {reported.includes(c.id) ? 'Reported' : 'Report'}
                  </button>
                )
              )}
            </div>
            <p class="talk-text">
              {pieces(c.text).map((p, k) =>
                p.href ? (
                  <a key={k} href={p.href} target="_blank" rel="noopener noreferrer nofollow ugc">
                    {p.t}
                  </a>
                ) : (
                  p.t
                ),
              )}
            </p>
          </li>
        ))}
      </ol>
      {user ? (
        <form
          class="talk-form"
          onSubmit={(e) => {
            e.preventDefault();
            void post();
          }}
        >
          <textarea
            class="inp talk-in"
            rows={2}
            maxLength={TALK_MAX + 200}
            value={text}
            placeholder="What do you see? Links welcome"
            aria-label="Your comment"
            onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
            onKeyDown={(e) => (e.metaKey || e.ctrlKey) && e.key === 'Enter' && void post()}
            data-testid="talk-input"
          />
          <div class="talk-foot mono-s">
            <span class={tooLong || tooManyLinks ? 'bad' : ''}>
              {line ||
                [`${text.length} / ${TALK_MAX}`, links ? `${links} of ${TALK_LINKS} links` : '', `as ${user.name || 'you'}`].filter(Boolean).join(' · ')}
            </span>
            <button class="btn sm primary" type="submit" disabled={busy || !text.trim() || tooLong || tooManyLinks} data-testid="talk-post">
              {busy ? 'Posting…' : 'Post'}
            </button>
          </div>
        </form>
      ) : (
        <button class="btn sm talk-signin" onClick={() => setSignIn(true)} data-testid="talk-signin">
          Sign in to write
        </button>
      )}
      {signIn && <SignInDialog next={next} reason="To write in Talk" onClose={() => setSignIn(false)} />}
    </section>
  );
}
