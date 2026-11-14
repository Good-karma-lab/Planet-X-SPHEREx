/** A small Markdown renderer for the Method text: headings, paragraphs, lists, tables, code blocks, bold, italics-free inline code and links. HTML in the text is escaped. */

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Inline: `code`, **bold**, [text](url). */
function inline(s: string): string {
  const parts = s.split(/(`[^`]+`)/g);
  return parts
    .map((p) => {
      if (p.startsWith('`') && p.endsWith('`') && p.length > 1) return `<code>${esc(p.slice(1, -1))}</code>`;
      let t = esc(p);
      t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
      t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
      return t;
    })
    .join('');
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

export interface Heading {
  id: string;
  title: string;
  level: number;
}

export interface Rendered {
  html: string;
  headings: Heading[];
}

/** Renders one Markdown text; `prefix` keeps the ids of several texts apart. */
export function render(md: string, prefix: string): Rendered {
  const lines = md.split('\n');
  const out: string[] = [];
  const headings: Heading[] = [];
  let i = 0;
  const isTable = (l: string) => l.trim().startsWith('|');
  while (i < lines.length) {
    const l = lines[i]!;
    if (!l.trim()) {
      i++;
      continue;
    }
    if (l.startsWith('```')) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.startsWith('```')) buf.push(lines[i++]!);
      i++;
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
      continue;
    }
    const h = /^(#{1,4}) (.*)$/.exec(l);
    if (h) {
      const level = h[1]!.length;
      const id = `${prefix}-${slug(h[2]!)}`;
      headings.push({ id, title: h[2]!, level });
      out.push(`<h${level + 1} id="${id}">${inline(h[2]!)}</h${level + 1}>`);
      i++;
      continue;
    }
    if (isTable(l)) {
      const rows: string[][] = [];
      while (i < lines.length && isTable(lines[i]!)) {
        const cells = lines[i]!.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...body] = rows;
      out.push(
        `<div class="mf-scroll"><table class="tbl"><thead><tr>${head!.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${body
          .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
          .join('')}</tbody></table></div>`,
      );
      continue;
    }
    const list = /^(\s*)([-*]|\d+\.) (.*)$/.exec(l);
    if (list && list[1] === '') {
      const ordered = /\d/.test(list[2]!);
      const items: string[] = [];
      while (i < lines.length) {
        const m = /^([-*]|\d+\.) (.*)$/.exec(lines[i]!);
        if (!m) break;
        let t = m[2]!;
        i++;
        while (i < lines.length && /^\s+\S/.test(lines[i]!) && !/^\s*([-*]|\d+\.) /.test(lines[i]!)) t += ' ' + lines[i++]!.trim();
        items.push(`<li>${inline(t)}</li>`);
      }
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    const buf: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !isTable(lines[i]!) && !/^(#{1,4} |```)/.test(lines[i]!) && !/^([-*]|\d+\.) /.test(lines[i]!)) buf.push(lines[i++]!.trim());
    out.push(`<p>${inline(buf.join(' '))}</p>`);
  }
  return { html: out.join('\n'), headings };
}


/** A figure placed in the text: `[figure: name | caption]` on a line of its own. */
export interface FigureBlock {
  kind: 'figure';
  name: string;
  caption: string;
}
/** A callout for the margin: `Result: headline | text | source` (an optional tone in brackets: `Result [amber]:` or `Result [red]:`). */
export interface ResultBlock {
  kind: 'result';
  headline: string;
  text: string;
  source: string;
  tone: '' | 'am' | 'rd';
}
export interface HtmlBlock {
  kind: 'html';
  html: string;
}
export type Block = HtmlBlock | FigureBlock | ResultBlock;

export interface Blocks {
  blocks: Block[];
  headings: Heading[];
}

const FIG = /^\[figure:\s*([a-z0-9_-]+)\s*(?:\|\s*(.*?))?\s*\]\s*$/i;
const RES = /^Result\s*(?:\[(amber|red|am|rd)\])?\s*:\s*(.+)$/i;

/** Renders a text into blocks: runs of Markdown as HTML, figures and Result callouts as their own blocks. */
export function renderBlocks(md: string, prefix: string): Blocks {
  const blocks: Block[] = [];
  const headings: Heading[] = [];
  let buf: string[] = [];
  let inCode = false;
  const flush = () => {
    if (!buf.some((l) => l.trim())) {
      buf = [];
      return;
    }
    const r = render(buf.join('\n'), prefix);
    headings.push(...r.headings);
    blocks.push({ kind: 'html', html: r.html });
    buf = [];
  };
  for (const line of md.split('\n')) {
    if (line.startsWith('```')) inCode = !inCode;
    if (!inCode) {
      const f = FIG.exec(line.trim());
      if (f) {
        flush();
        blocks.push({ kind: 'figure', name: f[1]!.toLowerCase(), caption: (f[2] ?? '').trim() });
        continue;
      }
      const r = RES.exec(line.trim());
      if (r) {
        flush();
        const [headline = '', text = '', source = ''] = r[2]!.split(/\s+\|\s+/).map((x) => x.trim());
        const t = (r[1] ?? '').toLowerCase();
        blocks.push({ kind: 'result', headline, text, source, tone: t.startsWith('a') ? 'am' : t.startsWith('r') ? 'rd' : '' });
        continue;
      }
    }
    buf.push(line);
  }
  flush();
  return { blocks, headings };
}
