// The Score tab of the cabinet and the public leaderboard (product spec §5a): points, level, accuracy, what earned points,
// achievements by family with the progress to the next one. Everything shown is counted by the server (service/accounts/score.py).

import { useEffect, useState } from 'preact/hooks';
import { api } from '../../lib/account';
import type { Route } from '../../lib/route';

export interface Achievement {
  id: string;
  family: string;
  tier: number;
  name: string;
  line: string;
  hidden: boolean;
  earned_at: string | null;
  progress: number;
  have: number | null;
  need: number | null;
  rarity: number | null;
}
interface ScoreEvent {
  kind: string;
  ref: string;
  points: number;
  right: boolean | null;
  at: string;
  target: string | null;
  answer: string | null;
  stage: string | null;
  result?: string | null;
}
export interface MyScore {
  points: number;
  level: string;
  next_level_at: number | null;
  levels: { at: number; name: string }[];
  accuracy: number | null;
  right: number;
  answered: number;
  waiting: number;
  squares: number;
  hide_from_board: boolean;
  events: ScoreEvent[];
  achievements: Achievement[];
  rules: { points: Record<string, number>; min_look_s: number; daily_squares: number; rank_min_answers: number };
}
interface LeaderRow {
  place: number | null;
  name: string;
  points: number;
  squares: number;
  confirmed: number;
  accuracy: number | null;
  me: boolean;
  badges: { id: string; name: string }[];
  answers_to_rank: number | null;
}

const when = (iso: string | null) => (iso ? `${iso.slice(0, 16).replace('T', ' ')} UTC` : '');
const pct = (x: number | null) => (x == null ? '—' : `${Math.round(x * 100)}%`);
const num = (n: number) => n.toLocaleString('en-US');
const words = (v: string | null | undefined) => (v ?? '').replace(/_/g, ' ');
const ANSWER: Record<string, string> = { moves: 'It moves', artefact: 'Artefact', known: 'Known object' };

function eventLine(e: ScoreEvent): string {
  switch (e.kind) {
    case 'square':
      return `Square ${e.ref} looked through${e.points ? '' : ' (over the daily limit: no points)'}`;
    case 'control':
      return e.right ? `Known mover caught in a control square: ${e.target}` : `Control square ${e.ref}: the known mover was not marked`;
    case 'check':
      return `Finding ${e.ref}: answered “${ANSWER[e.answer ?? ''] ?? e.answer}”, the stage is “${e.stage}”`;
    case 'moves':
      return `Does it move? ${e.ref}: answered “${words(e.answer)}”, the stage is “${e.stage}”`;
    case 'red':
    case 'anomaly':
      return `${e.kind === 'red' ? 'Red source' : 'Odd spectrum'} ${e.ref}: answered “${words(e.answer)}”, people say “${words(e.result)}”`;
    case 'submit':
      return `Finding ${e.ref} submitted: it passed every check`;
    case 'candidate':
      return `Finding ${e.ref} became a candidate`;
    case 'confirmed':
      return `Finding ${e.ref} was confirmed`;
    default:
      return `${e.kind} ${e.ref}`;
  }
}

/** The picture of an achievement (web/public/badges/<id>.webp: one style, generated with Google Gemini). */
export const badgeSrc = (id: string) => `/badges/${id}.webp`;

/** A badge: the achievement's picture, lit when earned and greyed when not (the same file); the ring around it is cut into the
 *  tiers of its family and shows which tier this is; a hidden one that is not earned shows the "?" picture. */
export function Badge({ a, tiers = 1, size = 56 }: { a: Pick<Achievement, 'id' | 'name' | 'tier'> & Partial<Achievement>; tiers?: number; size?: number }) {
  const earned = a.earned_at !== null && a.earned_at !== undefined;
  const secret = a.name === '?';
  const r = 26.5;
  const n = Math.max(1, tiers);
  const gap = n > 1 ? 8 : 0; // degrees between the segments of the ring
  const arc = (i: number) => {
    const a0 = ((-90 + (360 / n) * i + gap / 2) * Math.PI) / 180;
    const a1 = ((-90 + (360 / n) * (i + 1) - gap / 2) * Math.PI) / 180;
    if (n === 1) return `M28 ${28 - r}A${r} ${r} 0 1 1 27.99 ${28 - r}`;
    return `M${(28 + r * Math.cos(a0)).toFixed(2)} ${(28 + r * Math.sin(a0)).toFixed(2)}A${r} ${r} 0 ${360 / n - gap > 180 ? 1 : 0} 1 ${(28 + r * Math.cos(a1)).toFixed(2)} ${(28 + r * Math.sin(a1)).toFixed(2)}`;
  };
  return (
    <span class={`cb-badge ${earned ? 'on' : ''}`} style={{ width: `${size}px`, height: `${size}px` }}>
      <img src={badgeSrc(secret ? 'hidden' : a.id)} alt="" width={size} height={size} loading="lazy" decoding="async" />
      <svg viewBox="0 0 56 56" width={size} height={size} aria-hidden="true">
        {Array.from({ length: n }, (_, i) => (
          <path key={i} d={arc(i)} class={`seg ${i < a.tier ? 'tier' : ''}`} />
        ))}
      </svg>
    </span>
  );
}

export function ScorePanel() {
  const [s, setS] = useState<MyScore | null>(null);
  const [error, setError] = useState('');
  const [slug, setSlug] = useState('');
  useEffect(() => {
    void api<{ slug: string }>('/api/me/profile').then((p) => setSlug(p.slug), () => undefined);
  }, []);
  useEffect(() => {
    let stop = false;
    api<MyScore>('/api/me/score').then(
      (d) => !stop && setS(d),
      (e: Error) => !stop && setError(`Not loaded: ${e.message}`),
    );
    return () => {
      stop = true;
    };
  }, []);
  if (!s) return <p class={`cb-empty ${error ? 'bad' : ''}`}>{error || 'Loading…'}</p>;
  const cur = [...s.levels].reverse().find((l) => s.points >= l.at) ?? s.levels[0]!;
  const toNext = s.next_level_at == null ? 1 : (s.points - cur.at) / (s.next_level_at - cur.at);
  const nextName = s.levels.find((l) => l.at === s.next_level_at)?.name;
  const families: string[] = [];
  for (const a of s.achievements) if (!families.includes(a.family)) families.push(a.family);
  const earned = s.achievements.filter((a) => a.earned_at).length;
  return (
    <div class="cb-score" data-testid="score">
      <div class="cb-scorehead">
        <div class="cb-stat" data-testid="score-points">
          <b>{num(s.points)}</b>
          <span>points</span>
        </div>
        <img class="cb-levelbadge" src={badgeSrc(`level_${s.level.toLowerCase()}`)} alt="" width={56} height={56} data-testid="level-badge" />
        <div class="cb-stat wide" data-testid="score-level">
          <b>{s.level}</b>
          <span class="cb-bar" role="progressbar" aria-valuenow={Math.round(toNext * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Progress to the next level">
            <i style={{ width: `${Math.round(toNext * 100)}%` }} />
          </span>
          <span>{s.next_level_at == null ? 'the highest level' : `${num(s.next_level_at - s.points)} points to ${nextName}`}</span>
        </div>
        <div class="cb-stat" data-testid="score-accuracy">
          <b>{pct(s.accuracy)}</b>
          <span>{s.answered ? `right: ${s.right} of ${s.answered} answers` : 'accuracy: no settled answers yet'}</span>
        </div>
        <div class="cb-stat">
          <b>{s.squares}</b>
          <span>squares looked through</span>
        </div>
        <a class="btn sm" href="#/leaders" data-testid="to-leaders">
          Leaderboard
        </a>
        {slug && (
          <a class="btn sm" href={`#/u/${slug}`} data-testid="to-profile">
            My public page
          </a>
        )}
      </div>
      {s.waiting > 0 && (
        <p class="cb-status" data-testid="score-waiting">
          {s.waiting} {s.waiting === 1 ? 'answer waits' : 'answers wait'} for the finding’s final stage: paid then, if right
        </p>
      )}

      <h4>
        Achievements <span class="mono-s">{earned} of {s.achievements.length}</span>
      </h4>
      <div class="cb-families">
        {families.map((fam) => {
          const list = s.achievements.filter((a) => a.family === fam);
          const next = list.find((a) => !a.earned_at && !a.hidden);
          return (
            <div class="cb-family" key={fam} data-testid="family">
              <div class="cb-famname">{fam}</div>
              <div class="cb-badges">
                {list.map((a) => (
                  <a key={a.id} href={a.earned_at && slug ? `#/u/${slug}?badge=${a.id}` : undefined} class={`cb-ach ${a.earned_at ? 'on' : a === next ? 'next' : ''}`} data-testid={`ach-${a.id}`} data-earned={a.earned_at ? '1' : '0'} title={`${a.line}${a.earned_at ? ' · open to share it' : ''}${a.earned_at ? ` · earned ${when(a.earned_at)}` : ''}${a.rarity != null ? ` · ${a.rarity}% of searchers have it` : ''}`}>
                    <Badge a={a} tiers={list.length} />
                    <span class="cb-achname">{a.name}</span>
                  </a>
                ))}
              </div>
              {next && (
                <div class="cb-next" data-testid="next">
                  <span>
                    Next: <b>{next.name}</b> · {next.line}
                  </span>
                  <span class="cb-bar" role="progressbar" aria-valuenow={Math.round(next.progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={`Progress to ${next.name}`}>
                    <i style={{ width: `${Math.round(next.progress * 100)}%` }} />
                  </span>
                  <span class="mono-s">
                    {next.have} of {next.need}
                    {next.rarity != null ? ` · ${next.rarity}% of searchers have it` : ''}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <h4>What earned points</h4>
      {s.events.length === 0 && <span class="mono-s">Nothing yet</span>}
      <div class="cb-list">
        {s.events.map((e) => (
          <div key={`${e.kind}${e.ref}`} class="cb-row" data-testid="score-event">
            <span class="cb-main">
              <b>{eventLine(e)}</b>
              <span class="mono-s">{when(e.at)}</span>
            </span>
            <span class="cb-side">
              {e.right != null && <span class={`badge ${e.right ? 'st-pass' : 'st-fail'}`}>{e.right ? 'right' : 'wrong'}</span>}
              <span class="cb-pts">{e.points > 0 ? `+${num(e.points)}` : '0'}</span>
            </span>
          </div>
        ))}
      </div>
      <p class="cb-status">
        Points: square {s.rules.points['square']} (at least {s.rules.min_look_s} s and every pass shown; {s.rules.daily_squares} a day) · right answer {s.rules.points['check']} · control mover{' '}
        {s.rules.points['control']} · submit {s.rules.points['submit']} · candidate {s.rules.points['candidate']} · confirmed {num(s.rules.points['confirmed'] ?? 0)}
      </p>
    </div>
  );
}

const PERIODS = [
  ['week', 'This week'],
  ['month', 'This month'],
  ['all', 'All time'],
] as const;

/** The public leaderboard: only public names, the signed-in user's own row always shown. */
export function Leaders({ route }: { route: Route }) {
  const period = PERIODS.some(([k]) => k === route.params['period']) ? route.params['period']! : 'week';
  const [rows, setRows] = useState<LeaderRow[] | null>(null);
  const [min, setMin] = useState(10);
  const [error, setError] = useState('');
  useEffect(() => {
    let stop = false;
    setRows(null);
    api<{ rows: LeaderRow[]; rank_min_answers: number }>(`/api/leaders?period=${period}`).then(
      (d) => {
        if (stop) return;
        setRows(d.rows);
        setMin(d.rank_min_answers);
        setError('');
      },
      (e: Error) => !stop && setError(`Not loaded: ${e.message}`),
    );
    return () => {
      stop = true;
    };
  }, [period]);
  return (
    <div class="screen">
      <div class="ipage" data-testid="leaders">
        <div class="pg-h">
          <h1>Leaderboard</h1>
          <p class="hint">Points are paid for being right. A place needs {min} settled answers.</p>
        </div>
        <div class="cb-tabs" role="tablist">
          {PERIODS.map(([k, name]) => (
            <button key={k} role="tab" aria-selected={period === k} class={period === k ? 'on' : ''} onClick={() => (location.hash = k === 'week' ? '#/leaders' : `#/leaders?period=${k}`)} data-testid={`period-${k}`}>
              {name}
            </button>
          ))}
          <span class={`cb-status ${error ? 'bad' : ''}`} role="status">
            {error || (!rows ? 'Loading…' : '')}
          </span>
        </div>
        <div class="card glass cb-card">
          {rows && rows.length === 0 && <p class="cb-empty">Nobody has a place yet</p>}
          {rows && rows.length > 0 && (
            <table class="cb-board" data-testid="board">
              <thead>
                <tr>
                  <th>Place</th>
                  <th>Name</th>
                  <th class="n">Points</th>
                  <th class="n">Squares</th>
                  <th class="n">Confirmed</th>
                  <th class="n">Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} class={r.me ? 'me' : ''} data-testid={r.me ? 'board-me' : 'board-row'}>
                    <td class="pl">{r.place ?? '—'}</td>
                    <td>
                      <span class="cb-who2">
                        <b>{r.name}</b>
                        {r.me && <span class="badge">you</span>}
                        {r.badges.map((b) => (
                          <img key={b.id} class="cb-minibadge" src={badgeSrc(b.id)} alt={b.name} title={`${b.name}: one of this searcher’s three rarest achievements`} width={20} height={20} loading="lazy" />
                        ))}
                        {r.answers_to_rank != null && r.answers_to_rank > 0 && <span class="mono-s">{r.answers_to_rank} more settled answers for a place</span>}
                      </span>
                    </td>
                    <td class="n">{num(r.points)}</td>
                    <td class="n">{r.squares}</td>
                    <td class="n">{r.confirmed}</td>
                    <td class="n">{pct(r.accuracy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <p class="fmethod">
          <a href="#/me?view=badges">My badges → Cabinet</a>
        </p>
      </div>
    </div>
  );
}
