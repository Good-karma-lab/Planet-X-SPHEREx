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
export function render(md: string, prefix: string, leadOn = true): Rendered {
  const lines = md.split('\n');
  const out: string[] = [];
  const headings: Heading[] = [];
  let i = 0;
  let figures = 0;
  let sawLead = !leadOn;
  let sawSection = false;
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
      if (level >= 2) sawSection = true;
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
    const fig = /^\[figure:\s*([a-z0-9-]+)\s*(?:\|\s*(.*?))?\s*\]\s*$/.exec(l.trim());
    if (fig) {
      figures++;
      out.push(
        `<figure class="mf-fig" data-fig="${fig[1]}" id="${prefix}-fig-${figures}"><div class="mf-figslot" data-fig="${fig[1]}"></div>${fig[2] ? `<figcaption>${inline(fig[2])}</figcaption>` : ''}</figure>`,
      );
      i++;
      continue;
    }
    const buf: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !isTable(lines[i]!) && !/^(#{1,4} |```)/.test(lines[i]!) && !/^([-*]|\d+\.) /.test(lines[i]!) && !/^\[figure:/.test(lines[i]!.trim())) buf.push(lines[i++]!.trim());
    const text = buf.join(' ');
    const res = /^(?:\*\*Result:\*\*|Result:)\s*(.*)$/.exec(text);
    if (res) out.push(`<p class="mf-result"><span class="mf-result-k">Result</span> ${inline(res[1]!)}</p>`);
    else if (!sawSection && !sawLead) {
      // the first paragraph after the part's title, before any section, is its lead
      sawLead = true;
      out.push(`<p class="mf-lead">${inline(text)}</p>`);
    } else out.push(`<p>${inline(text)}</p>`);
  }
  return { html: out.join('\n'), headings };
}


/** A figure placed in the text: `[figure: name | caption]` on a line of its own. */
export interface FigureBlock {
  kind: 'figure';
  name: string;
  caption: string;
}
/** A callout for the margin: a paragraph that starts `**Result:**` (or `Result:`), with its source taken from the end of it. */
export interface ResultBlock {
  kind: 'result';
  text: string;
  source: string;
}
export interface HtmlBlock {
  kind: 'html';
  html: string;
}
export type Block = HtmlBlock | FigureBlock | ResultBlock;

/** One part: its title (the `# ` line), its lead (the first paragraph after it), and the rest as blocks. */
export interface PartText {
  title: string;
  lead: string;
  blocks: Block[];
  headings: Heading[];
}

const FIG = /^\[figure:\s*([a-z0-9-]+)\s*(?:\|\s*(.*?))?\s*\]\s*$/i;
const RES = /^(?:\*\*Result:\*\*|Result:)\s*(.*)$/;
/** the source at the end of a Result: "(Source: x)", "Source: x." or "; source: x" */
const SRC = /(?:\(\s*|;\s+|\.\s+|\s+)[Ss]ource:?\s*([^)]*?)\s*\)?\.?\s*$/;

/** Splits a Result paragraph into its sentence and its source. */
function result(raw: string): ResultBlock {
  let text = raw.trim();
  let source = '';
  const m = SRC.exec(text);
  if (m) {
    source = m[1]!.trim();
    text = text.slice(0, m.index).replace(/[\s;(]+$/, '').trim();
    if (text && !/[.!?]$/.test(text)) text += '.';
  }
  return { kind: 'result', text, source };
}

/** Renders a part: a run of Markdown as HTML, figures and Result paragraphs as their own blocks; the title and the lead are taken out. */
export function renderPart(md: string, prefix: string): PartText {
  const lines = md.split('\n');
  let title = '';
  let lead = '';
  let i = 0;
  while (i < lines.length && !lines[i]!.trim()) i++;
  if (i < lines.length && /^# /.test(lines[i]!)) title = lines[i++]!.slice(2).trim();
  while (i < lines.length && !lines[i]!.trim()) i++;
  const lb: string[] = [];
  while (i < lines.length && lines[i]!.trim() && !/^(#{1,4} |```|\||\[figure:|[-*] |\d+\. )/.test(lines[i]!) && !RES.test(lines[i]!.trim())) lb.push(lines[i++]!.trim());
  lead = lb.join(' ');
  const blocks: Block[] = [];
  // a Result written at the end of the lead is a callout of its own
  const inLead = /\s+Result:\s+(.*)$/.exec(lead);
  if (inLead) {
    lead = lead.slice(0, inLead.index).trim();
    blocks.push(result(inLead[1]!));
  }
  const headings: Heading[] = [];
  let buf: string[] = [];
  const flush = () => {
    if (buf.some((l) => l.trim())) {
      const r = render(buf.join('\n'), prefix, false);
      headings.push(...r.headings);
      blocks.push({ kind: 'html', html: r.html });
    }
    buf = [];
  };
  let inCode = false;
  for (; i < lines.length; i++) {
    const line = lines[i]!;
    if (line.startsWith('```')) inCode = !inCode;
    if (!inCode) {
      const f = FIG.exec(line.trim());
      if (f) {
        flush();
        blocks.push({ kind: 'figure', name: f[1]!.toLowerCase(), caption: (f[2] ?? '').trim() });
        continue;
      }
      if (RES.test(line.trim())) {
        flush();
        const para = [line.trim()];
        while (i + 1 < lines.length && lines[i + 1]!.trim() && !FIG.test(lines[i + 1]!.trim()) && !/^(#{1,4} |```|\|)/.test(lines[i + 1]!)) para.push(lines[++i]!.trim());
        blocks.push(result(RES.exec(para.join(' '))![1]!));
        continue;
      }
    }
    buf.push(line);
  }
  flush();
  return { title, lead, blocks, headings };
}
