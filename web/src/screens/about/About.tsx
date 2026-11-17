import { REFERENCES } from './references';

const REPO = 'https://github.com/Good-karma-lab/Planet-X-SPHEREx';

const TEAM = [
  { name: 'Aleksandr Ostapenko', role: 'Team owner', url: 'https://www.linkedin.com/in/alexanderostapenko/' },
  { name: 'Katherine Karmatskikh', role: 'Team member', url: 'https://www.linkedin.com/in/katherinekarmatskikh/' },
];

/** The About page: the references the site is built on, with a working link each, and the team. Nothing else. */
export function AboutBody() {
  return (
    <>
      <div class="pg-h">
        <h1>About</h1>
        <p class="hint">
          SPHEREx Space shows what NASA's SPHEREx telescope really saw. Here are the sources it is built on and the people who built it. Not
          affiliated with or endorsed by NASA.
        </p>
      </div>
      <h2 class="about-h" id="references">
        References
      </h2>
      <div class="pg-grid about-grid">
        {REFERENCES.map((g) => (
          <section class="card glass" key={g.title}>
            <h3>{g.title}</h3>
            <ul class="about-refs">
              {g.refs.map((r) => (
                <li key={r.url + r.name}>
                  <a href={r.url} target="_blank" rel="noopener noreferrer">
                    {r.name}
                  </a>
                  <span class="faint"> · {r.note}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <h2 class="about-h" id="team">
        The team
      </h2>
      <div class="pg-grid about-grid">
        <section class="card glass">
          <h3>Good Karma Lab</h3>
          <p>NASA Space Apps Challenge 2026, Barcelona Local Event.</p>
          <ul class="about-team">
            {TEAM.map((m) => (
              <li key={m.url}>
                <b>{m.name}</b> <span class="faint">· {m.role} ·</span>{' '}
                <a href={m.url} target="_blank" rel="noopener noreferrer">
                  LinkedIn ↗
                </a>
              </li>
            ))}
          </ul>
          <p class="mono-s">
            Code:{' '}
            <a href={REPO} target="_blank" rel="noopener noreferrer">
              github.com/Good-karma-lab/Planet-X-SPHEREx
            </a>{' '}
            (Apache-2.0).
          </p>
          <p class="mono-s">Achievement badges generated with Google Gemini.</p>
        </section>
      </div>
    </>
  );
}
