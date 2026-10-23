import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

/** A small Markdown reader for wiki pages. It builds React elements (never HTML strings), so a note cannot inject
 * markup; links are limited to `wiki:` pages and http(s), pictures to the wiki's own files and http(s). */

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "code"; lang: string; text: string }
  | { kind: "quote"; blocks: Block[] }
  | { kind: "list"; ordered: boolean; items: Block[][] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "rule" };

const FENCE = /^\s*(```|~~~)\s*([\w+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

const cells = (line: string): string[] => {
  const inner = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return inner.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
};

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").trim().startsWith(fence[1] ?? "```")) {
        body.push(lines[i] ?? "");
        i += 1;
      }
      i += 1;
      out.push({ kind: "code", lang: fence[2] ?? "", text: body.join("\n") });
      continue;
    }
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ kind: "heading", level: (heading[1] ?? "#").length, text: heading[2] ?? "" });
      i += 1;
      continue;
    }
    if (RULE.test(line)) {
      out.push({ kind: "rule" });
      i += 1;
      continue;
    }
    if (line.trimStart().startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && (lines[i] ?? "").trimStart().startsWith(">")) {
        body.push((lines[i] ?? "").replace(/^\s*>\s?/, ""));
        i += 1;
      }
      out.push({ kind: "quote", blocks: parseBlocks(body.join("\n")) });
      continue;
    }
    if (line.includes("|") && TABLE_SEP.test(lines[i + 1] ?? "")) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && (lines[i] ?? "").includes("|") && (lines[i] ?? "").trim()) {
        rows.push(cells(lines[i] ?? ""));
        i += 1;
      }
      out.push({ kind: "table", head, rows });
      continue;
    }
    const item = ITEM.exec(line);
    if (item) {
      const base = (item[1] ?? "").length;
      const ordered = /\d/.test(item[2] ?? "");
      const items: Block[][] = [];
      while (i < lines.length) {
        const cur = ITEM.exec(lines[i] ?? "");
        if (!cur || (cur[1] ?? "").length < base || (cur[1] ?? "").length > base) break;
        const text: string[] = [cur[3] ?? ""];
        i += 1;
        const nested: string[] = [];
        while (i < lines.length) {
          const next = lines[i] ?? "";
          const nextItem = ITEM.exec(next);
          if (nextItem && (nextItem[1] ?? "").length <= base) break;
          if (!next.trim()) break;
          if (/^\s{2,}\S/.test(next) || nextItem) nested.push(next.slice(Math.min(base + 2, next.length - next.trimStart().length)));
          else text.push(next.trim());
          i += 1;
        }
        items.push([{ kind: "paragraph", text: text.join(" ") }, ...(nested.length ? parseBlocks(nested.join("\n")) : [])]);
      }
      out.push({ kind: "list", ordered, items });
      continue;
    }
    const para: string[] = [line.trim()];
    i += 1;
    while (i < lines.length) {
      const next = lines[i] ?? "";
      if (!next.trim() || FENCE.test(next) || HEADING.test(next) || RULE.test(next) || next.trimStart().startsWith(">") || ITEM.test(next)) break;
      if (next.includes("|") && TABLE_SEP.test(lines[i + 1] ?? "")) break;
      para.push(next.trim());
      i += 1;
    }
    out.push({ kind: "paragraph", text: para.join(" ") });
  }
  return out;
}

export function headingId(text: string): string {
  const plain = text.replace(/\[([^\]]*)\]\([^)]*\)|[*_`~]/g, (_m, label: string | undefined) => label ?? "");
  const id = plain.trim().toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s+/g, "-");
  return id || "section";
}

/** Unique ids for the headings of a page, in document order (a repeated title gets -2, -3 ...). */
export function headingsOf(blocks: Block[]): { id: string; level: number; text: string }[] {
  const seen = new Map<string, number>();
  const out: { id: string; level: number; text: string }[] = [];
  for (const b of blocks) {
    if (b.kind !== "heading") continue;
    const base = headingId(b.text);
    const n = (seen.get(base) ?? 0) + 1;
    seen.set(base, n);
    out.push({ id: n === 1 ? base : `${base}-${n}`, level: b.level, text: b.text.replace(/[*_`~]/g, "") });
  }
  return out;
}

const INLINE = /(`[^`\n]+`)|(!\[([^\]]*)\]\(([^)\s]+)\))|(\[([^\]]+)\]\(([^)\s]+)\))|(\*\*([^*]+)\*\*)|(~~([^~]+)~~)|(\*([^*\s][^*]*)\*)|(\b_([^_\s][^_]*)_\b)/g;

function wikiTarget(url: string): string | null {
  if (!url.startsWith("wiki:")) return null;
  try {
    return `/wiki/${decodeURIComponent(url.slice(5))}`;
  } catch {
    return null;
  }
}

const safeImage = (url: string) => url.startsWith("/api/wiki/assets/") || /^https?:\/\//i.test(url);

export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) nodes.push(text.slice(last, at));
    key += 1;
    if (m[1]) nodes.push(<code key={key}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) {
      const url = m[4] ?? "";
      nodes.push(safeImage(url) ? <img key={key} src={url} alt={m[3] ?? ""} loading="lazy" /> : (m[3] ?? ""));
    } else if (m[5]) {
      const label = renderInline(m[6] ?? "");
      const url = m[7] ?? "";
      const inner = wikiTarget(url);
      if (inner) nodes.push(<Link key={key} to={inner}>{label}</Link>);
      else if (/^https?:\/\//i.test(url)) nodes.push(<a key={key} href={url} target="_blank" rel="noreferrer noopener">{label}</a>);
      else nodes.push(<Fragment key={key}>{label}</Fragment>);
    } else if (m[8]) nodes.push(<strong key={key}>{renderInline(m[9] ?? "")}</strong>);
    else if (m[10]) nodes.push(<del key={key}>{renderInline(m[11] ?? "")}</del>);
    else if (m[12]) nodes.push(<em key={key}>{renderInline(m[13] ?? "")}</em>);
    else if (m[14]) nodes.push(<em key={key}>{renderInline(m[15] ?? "")}</em>);
    last = at + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function RenderBlocks({ blocks, ids }: { blocks: Block[]; ids?: string[] }) {
  let heading = 0;
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "heading": {
            const Tag = `h${Math.min(b.level + 1, 6)}` as "h2";
            const id = ids?.[heading];
            heading += 1;
            return <Tag key={i} id={id}>{renderInline(b.text)}</Tag>;
          }
          case "paragraph":
            return <p key={i}>{renderInline(b.text)}</p>;
          case "code":
            return <pre key={i}><code>{b.text}</code></pre>;
          case "quote":
            return <blockquote key={i}><RenderBlocks blocks={b.blocks} /></blockquote>;
          case "rule":
            return <hr key={i} />;
          case "list": {
            const Tag = b.ordered ? "ol" : "ul";
            return (
              <Tag key={i}>
                {b.items.map((item, j) => (
                  <li key={j}><RenderBlocks blocks={item} /></li>
                ))}
              </Tag>
            );
          }
          case "table":
            return (
              <div key={i} className="table-wrap">
                <table>
                  <thead>
                    <tr>{b.head.map((c, j) => <th key={j}>{renderInline(c)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>{r.map((c, k) => <td key={k}>{renderInline(c)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
        }
      })}
    </>
  );
}

/** The blocks of a page; `dropTitle` leaves out a first `# heading` that only repeats the page title shown above. */
export function readBlocks(source: string, dropTitle?: string): Block[] {
  const blocks = parseBlocks(source);
  const first = blocks[0];
  return dropTitle && first?.kind === "heading" && first.level === 1 && first.text.trim() === dropTitle.trim() ? blocks.slice(1) : blocks;
}

export function Markdown({ source, dropTitle }: { source: string; dropTitle?: string }) {
  const blocks = readBlocks(source, dropTitle);
  const ids = headingsOf(blocks).map((h) => h.id);
  return (
    <div className="markdown">
      <RenderBlocks blocks={blocks} ids={ids} />
    </div>
  );
}
