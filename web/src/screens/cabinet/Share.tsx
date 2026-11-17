// Sharing (product spec §5c): the share buttons, the public profile `#/u/<slug>` and the story / discovery page
// `#/findings/<id>/story`. Nothing is posted from here: a button counts the click on our side (POST /api/share, no personal
// data) and opens the network's own share window, where the person posts. On a phone the first button is the system's share sheet.

import { useEffect, useState } from 'preact/hooks';
import { api, useUser } from '../../lib/account';
import { formatDec, formatRA } from '../../lib/sky';
import { Pic } from './pic';
import { Badge, badgeSrc } from './Score';

type Kind = 'profile' | 'achievement' | 'story' | 'discovery';
type Channel = 'native' | 'x' | 'linkedin' | 'facebook' | 'reddit' | 'telegram' | 'whatsapp' | 'copy';

const NETWORKS: { id: Channel; name: string; url: (u: string, text: string) => string }[] = [
  { id: 'x', name: 'X', url: (u, t) => `https://twitter.com/intent/tweet?text=${encodeURIComponent(t)}&url=${encodeURIComponent(u)}` },
  { id: 'linkedin', name: 'LinkedIn', url: (u) => `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(u)}` },
  { id: 'facebook', name: 'Facebook', url: (u) => `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(u)}` },
  { id: 'reddit', name: 'Reddit', url: (u, t) => `https://www.reddit.com/submit?url=${encodeURIComponent(u)}&title=${encodeURIComponent(t)}` },
  { id: 'telegram', name: 'Telegram', url: (u, t) => `https://t.me/share/url?url=${encodeURIComponent(u)}&text=${encodeURIComponent(t)}` },
  { id: 'whatsapp', name: 'WhatsApp', url: (u, t) => `https://wa.me/?text=${encodeURIComponent(`${t} ${u}`)}` },
];

/** The address of a share link's network window (for tests). */
export const shareUrl = (channel: Channel, link: string, text: string) => NETWORKS.find((n) => n.id === channel)?.url(link, text) ?? link;

/** The share buttons of one thing. `text` is what the person is offered to say; they change it in the network's own window. */
export function ShareButtons({ kind, shareRef, text, note }: { kind: Kind; shareRef: string; text: string; note?: string }) {
  const [status, setStatus] = useState('');
  const native = typeof navigator !== 'undefined' && typeof navigator.share === 'function';
  const link = (channel: Channel) => api<{ url: string; title: string; text: string }>('/api/share', { method: 'POST', body: { kind, ref: shareRef, channel } });
  const fail = (e: Error) => setStatus(e.message);
  const open = (n: (typeof NETWORKS)[number]) => {
    // the window is opened by the click itself (a window opened after the answer is blocked as a pop-up), then pointed at the network
    const w = window.open('', '_blank');
    link(n.id).then((r) => {
      if (w) {
        w.opener = null;
        w.location.href = n.url(r.url, text);
      } else location.href = n.url(r.url, text);
      setStatus('');
    }, (e: Error) => {
      w?.close();
      fail(e);
    });
  };
  const copy = () =>
    link('copy').then(async (r) => {
      try {
        await navigator.clipboard.writeText(r.url);
        setStatus('Link copied');
      } catch {
        setStatus(r.url); // no clipboard here: the link is shown to copy by hand
      }
    }, fail);
  const system = () =>
    link('native').then((r) => navigator.share({ title: r.title, text, url: r.url }).then(() => setStatus(''), () => setStatus('')), fail);
  return (
    <div class="cb-share" data-testid="share">
      <div class="cb-sharetext" data-testid="share-text">
        {text}
      </div>
      <div class="cb-sharebtns">
        {native && (
          <button class="btn sm primary" onClick={system} data-testid="share-native">
            Share…
          </button>
        )}
        {NETWORKS.map((n) => (
          <button key={n.id} class="btn sm" onClick={() => open(n)} data-testid={`share-${n.id}`}>
            {n.name}
          </button>
        ))}
        <button class="btn sm" onClick={copy} data-testid="share-copy">
          Copy link
        </button>
      </div>
      <span class={`cb-status ${status && status !== 'Link copied' && !status.startsWith('http') ? 'bad' : ''}`} role="status" data-testid="share-status">
        {status || note || ''}
      </span>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------- the profile

interface Profile {
  slug: string;
  name: string;
  points: number;
  level: string;
  accuracy: number | null;
  answered: number;
  squares: number;
  since: string;
  public: boolean;
  mine: boolean;
  badges: { id: string; family: string; tier: number; name: string; line: string; earned_at: string }[];
  findings: { finding_id: string; title: string; status: string; created: string; story: boolean }[];
}

const day = (iso: string | null) => (iso ? iso.slice(0, 10) : '');
const words = (s: string) => s.replace(/_/g, ' ');

/** The public page of a searcher: name, points, level, badges, findings. Never an e-mail. */
export function ProfilePage({ slug, badge }: { slug: string; badge?: string }) {
  const [p, setP] = useState<Profile | null>(null);
  const [error, setError] = useState('');
  const [n, setN] = useState(0);
  const { user } = useUser();
  useEffect(() => {
    let stop = false;
    setError('');
    api<Profile>(`/api/u/${encodeURIComponent(slug)}`).then(
      (d) => !stop && setP(d),
      (e: Error) => !stop && (setP(null), setError(e.message === 'no such public profile' ? 'This profile is not public' : `Not loaded: ${e.message}`)),
    );
    return () => {
      stop = true;
    };
  }, [slug, n, user?.id]);
  const front = p?.badges.find((b) => b.id === badge) ?? null;
  const makePublic = (v: boolean) => api('/api/me', { method: 'PATCH', body: { public_profile: v } }).then(() => setN((x) => x + 1), (e: Error) => setError(e.message));
  return (
    <div class="screen">
      <div class="ipage" data-testid="profile">
        {!p ? (
          <p class={`cb-empty ${error ? 'bad' : ''}`} data-testid="profile-status">
            {error || 'Loading…'}
          </p>
        ) : (
          <>
            <div class="pg-h cb-prohead">
              <img class="cb-levelbadge" src={badgeSrc(`level_${p.level.toLowerCase()}`)} alt="" width={72} height={72} />
              <div>
                <h1 data-testid="profile-name">{p.name}</h1>
                <p class="hint">
                  {p.level} · searching NASA’s infrared sky on SPHEREx Space since {day(p.since)}
                </p>
              </div>
            </div>
            {p.mine && (
              <div class="card glass cb-card cb-mine" data-testid="profile-mine">
                <span class={`badge ${p.public ? 'st-pass' : 'st-open'}`}>{p.public ? 'public' : 'private: only you see this page'}</span>
                <button class="btn sm" onClick={() => void makePublic(!p.public)} data-testid="profile-toggle">
                  {p.public ? 'Make it private' : 'Make it public'}
                </button>
                {p.public && <ShareButtons kind="profile" shareRef={p.slug} text={`I search NASA’s infrared sky for things that move, on SPHEREx Space: ${p.points} points, ${p.badges.length} badges.`} />}
              </div>
            )}
            {front && (
              <div class="card glass cb-card cb-front" data-testid="profile-front">
                <Badge a={front} size={120} />
                <div>
                  <b>{front.name}</b>
                  <p>{front.line}</p>
                  <span class="mono-s">earned {day(front.earned_at)}</span>
                  {p.mine && p.public && <ShareButtons kind="achievement" shareRef={`${p.slug}.${front.id}`} text={`I earned “${front.name}” on SPHEREx Space: ${front.line.charAt(0).toLowerCase()}${front.line.slice(1)} in NASA’s infrared sky.`} />}
                </div>
              </div>
            )}
            <div class="cb-scorehead">
              <div class="cb-stat">
                <b>{p.points.toLocaleString('en-US')}</b>
                <span>points</span>
              </div>
              <div class="cb-stat">
                <b>{p.accuracy == null ? '—' : `${Math.round(p.accuracy * 100)}%`}</b>
                <span>{p.answered ? `right, of ${p.answered} answers` : 'no settled answers yet'}</span>
              </div>
              <div class="cb-stat">
                <b>{p.squares}</b>
                <span>squares looked through</span>
              </div>
              <div class="cb-stat">
                <b>{p.findings.length}</b>
                <span>findings submitted</span>
              </div>
            </div>
            <div class="card glass cb-card">
              <h3>Badges</h3>
              {p.badges.length === 0 && <p class="cb-empty">No badges yet</p>}
              <div class="cb-badges">
                {p.badges.map((b) => (
                  <a key={b.id} class="cb-ach on" href={`#/u/${p.slug}?badge=${b.id}`} title={b.line} data-testid="profile-badge">
                    <Badge a={b} />
                    <span class="cb-achname">{b.name}</span>
                  </a>
                ))}
              </div>
            </div>
            <div class="card glass cb-card">
              <h3>Findings</h3>
              {p.findings.length === 0 && <p class="cb-empty">No findings submitted yet</p>}
              <div class="cb-list">
                {p.findings.map((f) => (
                  <a key={f.finding_id} class="cb-row" href={f.story ? `#/findings/${f.finding_id}/story` : `#/findings/${f.finding_id}`} data-testid="profile-finding">
                    <span class="cb-main">
                      <b>{f.title || `Finding ${f.finding_id}`}</b>
                      <span class="mono-s">
                        submitted {day(f.created)}
                        {f.story ? ' · story' : ''}
                      </span>
                    </span>
                    <span class="cb-side">
                      <span class={`badge st-${f.status}`}>{words(f.status)}</span>
                    </span>
                  </a>
                ))}
              </div>
            </div>
            <p class="fmethod">
              <a href="#/leaders">Leaderboard</a>
            </p>
          </>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------------------------- the story

interface Story {
  finding_id: string;
  title: string;
  intro: string;
  notes: string;
  public: boolean;
  mine: boolean;
  published: string | null;
  status: string;
  status_text: string;
  discovery: boolean;
  finder: string;
  finder_slug: string | null;
  checkers: string[];
  ra: number | null;
  dec: number | null;
  submitted: string;
  checks: { id: string; state: string; detail?: string }[];
  marks: { ra: number; dec: number; pass: number | null; label: string }[];
  machine: boolean;
  summary: string | null;
  next: string;
}

/** The story of a submitted finding, made from its investigation; "Discovery" once it is confirmed. Its owner edits the text
 *  and decides when it is public. */
export function StoryPage({ id }: { id: string }) {
  const { user, ready } = useUser();
  const [s, setS] = useState<Story | null>(null);
  const [error, setError] = useState('');
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState({ title: '', intro: '', notes: '' });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!ready) return;
    let stop = false;
    setError('');
    const take = (d: Story) => {
      if (stop) return;
      setS(d);
      setDraft({ title: d.title, intro: d.intro, notes: d.notes });
    };
    // the owner gets their own story (a draft is made at the first ask); everyone else the public one
    const pub = () => api<Story>(`/api/stories/${encodeURIComponent(id)}`).then(take, (e: Error) => !stop && setError(e.message === 'no such public story' ? 'This story is not public' : `Not loaded: ${e.message}`));
    if (user) api<Story>(`/api/me/stories/${encodeURIComponent(id)}`).then(take, pub);
    else void pub();
    return () => {
      stop = true;
    };
  }, [id, user?.id, ready]);
  const save = (changes: Partial<{ title: string; intro: string; notes: string; public: boolean }>) => {
    setBusy(true);
    return api<Story>(`/api/me/stories/${encodeURIComponent(id)}`, { method: 'PUT', body: changes })
      .then(
        (d) => {
          setS(d);
          setDraft({ title: d.title, intro: d.intro, notes: d.notes });
          setEdit(false);
          setError('');
        },
        (e: Error) => setError(e.message),
      )
      .finally(() => setBusy(false));
  };
  if (!s)
    return (
      <div class="screen">
        <div class="ipage" data-testid="story">
          <p class={`cb-empty ${error ? 'bad' : ''}`} data-testid="story-status">
            {error || 'Loading…'}
          </p>
        </div>
      </div>
    );
  const kind: Kind = s.discovery ? 'discovery' : 'story';
  const text = s.discovery
    ? `Confirmed: a new object in NASA’s SPHEREx infrared sky, found on SPHEREx Space by ${s.finder}.`
    : s.mine
      ? 'I just submitted a new moving object to SPHEREx Space: it passed every check, now waiting for the verdict.'
      : `${s.finder} submitted a new moving object to SPHEREx Space: it passed every check.`;
  return (
    <div class="screen">
      <div class={`ipage cb-story ${s.discovery ? 'discovery' : ''}`} data-testid="story">
        {s.discovery && (
          <div class="cb-celebrate" data-testid="celebration">
            <img src={badgeSrc('confirmed')} alt="" width={132} height={132} />
            <div>
              <span class="cb-kicker">Discovery</span>
              <h1>A new object, confirmed</h1>
              <p>
                Found by <b>{s.finder}</b>
                {s.checkers.length > 0 && (
                  <>
                    {' '}
                    · checked by <b>{s.checkers.join(', ')}</b>
                  </>
                )}
              </p>
            </div>
          </div>
        )}
        {s.mine && (
          <div class="card glass cb-card cb-mine" data-testid="story-mine">
            <span class={`badge ${s.public ? 'st-pass' : 'st-open'}`}>{s.public ? 'public' : 'draft: only you see this page'}</span>
            {!edit && (
              <button class="btn sm" onClick={() => setEdit(true)} data-testid="story-edit">
                Edit the text
              </button>
            )}
            <button class={`btn sm ${s.public ? '' : 'primary'}`} disabled={busy || edit} onClick={() => void save({ public: !s.public })} data-testid="story-publish">
              {s.public ? 'Unpublish' : 'Publish'}
            </button>
            <span class="cb-status bad">{error}</span>
          </div>
        )}
        <div class="pg-h">
          {edit ? (
            <input class="inp cb-title-in" aria-label="Title" maxLength={140} value={draft.title} onInput={(e) => setDraft({ ...draft, title: (e.target as HTMLInputElement).value })} data-testid="story-title-in" />
          ) : (
            <h1 data-testid="story-title">{s.title}</h1>
          )}
          <p class="hint" data-testid="story-byline">
            {s.finder_slug ? <a href={`#/u/${s.finder_slug}`}>{s.finder}</a> : s.finder} · submitted {day(s.submitted)} · <span class={`badge st-${s.status}`}>{words(s.status)}</span>
          </p>
        </div>
        {s.summary && (
          <p class="cb-press" data-testid="story-summary">
            {s.summary}
          </p>
        )}
        <div class="cb-storygrid">
          <div class="cb-storypic">
            <Pic findingId={s.machine ? s.finding_id : null} ra={s.ra} dec={s.dec} size={320} label={s.title} />
            <span class="mono-s">The time-lapse: one frame per SPHEREx pass</span>
          </div>
          <div class="cb-storytext">
            {edit ? (
              <textarea class="inp" rows={7} aria-label="The story" maxLength={6000} value={draft.intro} onInput={(e) => setDraft({ ...draft, intro: (e.target as HTMLTextAreaElement).value })} data-testid="story-intro-in" />
            ) : (
              s.intro.split(/\n{2,}/).map((para, i) => <p key={i}>{para}</p>)
            )}
            <p class="cb-statusline" data-testid="story-verdict">
              {s.status_text}
            </p>
          </div>
        </div>

        <div class="card glass cb-card">
          <h3>Where it is</h3>
          {s.ra != null && s.dec != null ? (
            <p class="cb-where">
              <b>
                {formatRA(s.ra)} {formatDec(s.dec)}
              </b>{' '}
              <span class="mono-s">
                RA {s.ra.toFixed(4)}° Dec {s.dec >= 0 ? '+' : ''}
                {s.dec.toFixed(4)}°
              </span>{' '}
              <a class="btn sm" href={`#/lab?ra=${s.ra.toFixed(5)}&dec=${s.dec.toFixed(5)}&fov=5`}>
                Open in the lab
              </a>
            </p>
          ) : (
            <p class="cb-empty">The place was not kept</p>
          )}
        </div>

        <div class="card glass cb-card">
          <h3>How it was found</h3>
          {s.marks.length > 0 && (
            <p class="cb-where">
              Marked on {s.marks.length} {s.marks.length === 1 ? 'pass' : 'passes'}:{' '}
              {s.marks.map((m, i) => (
                <span key={i} class="mono-s">
                  {m.pass != null ? `pass ${m.pass + 1} ` : ''}
                  {formatRA(m.ra, true)} {formatDec(m.dec, true)}
                  {i < s.marks.length - 1 ? ' · ' : ''}
                </span>
              ))}
            </p>
          )}
          <div class="cb-checks">
            {s.checks.map((c) => (
              <span key={c.id} class={`badge st-${c.state}`}>
                {words(c.id)}: {words(c.state)}
              </span>
            ))}
          </div>
          {s.checks
            .filter((c) => c.detail)
            .map((c) => (
              <div key={c.id} class="cb-note">
                <b>{words(c.id)}</b>: {c.detail}
              </div>
            ))}
        </div>

        {(edit || s.notes) && (
          <div class="card glass cb-card">
            <h3>Notes of the finder</h3>
            {edit ? (
              <textarea class="inp" rows={6} aria-label="Notes" maxLength={12000} value={draft.notes} onInput={(e) => setDraft({ ...draft, notes: (e.target as HTMLTextAreaElement).value })} data-testid="story-notes-in" />
            ) : (
              s.notes.split(/\n{2,}/).map((para, i) => (
                <p key={i} class="cb-note">
                  {para}
                </p>
              ))
            )}
          </div>
        )}
        {edit && (
          <div class="cb-submit">
            <button class="btn primary" disabled={busy || !draft.title.trim()} onClick={() => void save(draft)} data-testid="story-save">
              Save
            </button>
            <button class="btn" disabled={busy} onClick={() => (setEdit(false), setDraft({ title: s.title, intro: s.intro, notes: s.notes }))}>
              Cancel
            </button>
          </div>
        )}

        <div class="card glass cb-card">
          <h3>What happens next</h3>
          <p class="cb-where">{s.next}</p>
        </div>

        {s.public ? (
          <div class="card glass cb-card">
            <h3>Share</h3>
            <ShareButtons kind={kind} shareRef={s.finding_id} text={text} />
          </div>
        ) : (
          s.mine && <p class="cb-status">Publish the story to share it. Until then only you see this page.</p>
        )}
      </div>
    </div>
  );
}
