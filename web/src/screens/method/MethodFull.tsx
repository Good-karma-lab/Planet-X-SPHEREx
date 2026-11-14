import { useEffect, useRef } from 'preact/hooks';
import data from './sections.json';
import './method.css';

interface Section {
  part?: string;
  id?: string;
  title?: string;
  wide?: boolean;
  html?: string;
}

const sections = data.sections as Section[];
const log = data.log as string[][];

/** The section `sec` of the address (#/method?sec=pipeline), if any. */
const wanted = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('sec');

/**
 * The full account of the method: every step with its parameters, the reason for the choice and the check on real data, with the videos and pictures the checks
 * produced. The text is the account as developed and validated on the research version; the table above it says what the live site does today.
 */
export function MethodFull() {
  const root = useRef<HTMLDivElement>(null);
  const go = (id: string, smooth: boolean) => {
    const el = root.current?.querySelector(`#m-${CSS.escape(id)}`) as HTMLElement | null;
    if (!el) return false;
    el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
    return true;
  };
  useEffect(() => {
    const r = root.current;
    if (!r) return;
    // links between sections scroll inside the page; the videos play only while they are on screen
    const onClick = (e: Event) => {
      const a = (e.target as HTMLElement).closest('a[data-sec]') as HTMLAnchorElement | null;
      if (a && go(a.dataset.sec!, true)) e.preventDefault();
    };
    r.addEventListener('click', onClick);
    let io: IntersectionObserver | null = null;
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches && 'IntersectionObserver' in window) {
      io = new IntersectionObserver(
        (es) =>
          es.forEach((e) => {
            const v = e.target as HTMLVideoElement;
            if (e.isIntersecting && e.intersectionRatio > 0.45) void v.play().catch(() => {});
            else v.pause();
          }),
        { threshold: [0, 0.45, 1] },
      );
      r.querySelectorAll('video').forEach((v) => io!.observe(v));
    }
    const sec = wanted();
    if (sec) {
      go(sec, false);
      setTimeout(() => go(sec, false), 500); // again once the pictures above have their size
    }
    const onHash = () => {
      const w = wanted();
      if (w) go(w, true);
    };
    window.addEventListener('hashchange', onHash);
    return () => {
      window.removeEventListener('hashchange', onHash);
      r.removeEventListener('click', onClick);
      io?.disconnect();
    };
  }, []);
  let n = 0;
  return (
    <div class="mfull" ref={root}>
      <div class="mf-note warn-note">
        The account below is the method as developed and checked on real data (version {data.version.id}, {data.version.date}). Where it says “the lab”, the table
        “In production today” above is what the live site does; the numbers are those of the test fields named in each section.
      </div>
      <div class="mf-cols">
        <nav class="mf-toc card glass">
          {sections.map((s) => {
            if (s.part) return <div class="toc-sep" key={s.part}>{s.part}</div>;
            n++;
            return (
              <a key={s.id} href="#/method" onClick={(e) => { e.preventDefault(); go(s.id!, true); }}>
                {n}. <span dangerouslySetInnerHTML={{ __html: s.title! }} />
              </a>
            );
          })}
          <div class="toc-sep">History</div>
          <a href="#/method" onClick={(e) => { e.preventDefault(); go('log', true); }}>Change log</a>
        </nav>
        <div class="mf-body">
          {(() => {
            let k = 0;
            return sections.map((s) => {
              if (s.part) return null;
              k++;
              return (
                <section key={s.id} class={`card glass${s.wide ? ' motion' : ' motion'}`} id={`m-${s.id}`}>
                  <h3>
                    {k}. <span dangerouslySetInnerHTML={{ __html: s.title! }} />
                  </h3>
                  <div dangerouslySetInnerHTML={{ __html: s.html! }} />
                </section>
              );
            });
          })()}
          <section class="card glass motion" id="m-log">
            <h3>Change log of the methods</h3>
            <table class="tbl">
              <thead>
                <tr><th>Version</th><th>Date</th><th>Change</th></tr>
              </thead>
              <tbody>
                {log.map((r) => (
                  <tr key={r[0]}>
                    <td>{r[0]}</td>
                    <td>{r[1]}</td>
                    <td dangerouslySetInnerHTML={{ __html: r[2]! }} />
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  );
}
