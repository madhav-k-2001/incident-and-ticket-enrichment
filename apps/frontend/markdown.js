// Small, safe Markdown renderer for assistant replies.
//
// Everything is HTML-escaped first, then a limited subset is turned into
// markup: headings, paragraphs, lists (nested by indentation), fenced code,
// block quotes, rules, GFM tables, and inline code / bold / italic / strike /
// http(s) and mailto links. Unclosed syntax while a reply is still streaming
// simply renders as text until it closes.

const FENCE = /^\s{0,3}(```|~~~)/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const INDENTED = /^\s+\S/;

export function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function renderMarkdown(source) {
  const lines = String(source).replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  return renderBlocks(lines);
}

function renderBlocks(lines) {
  let html = "";
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;

    if (!line.trim()) {
      i++;
    } else if (FENCE.test(line)) {
      const fence = line.trim().slice(0, 3);
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) body.push(lines[i++]);
      i++;
      html += `<pre class="code"><code>${escapeHtml(body.join("\n"))}</code></pre>`;
    } else if ((m = line.match(HEADING))) {
      const tag = `h${Math.min(m[1].length + 2, 6)}`;
      html += `<${tag}>${inline(m[2])}</${tag}>`;
      i++;
    } else if (RULE.test(line)) {
      html += "<hr>";
      i++;
    } else if (QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && (m = lines[i].match(QUOTE))) {
        body.push(m[1]);
        i++;
      }
      html += `<blockquote>${renderBlocks(body)}</blockquote>`;
    } else if (isTableStart(lines, i)) {
      i = renderTable(lines, i, (out) => (html += out));
    } else if (LIST.test(line)) {
      const body = [lines[i++]];
      while (i < lines.length) {
        const next = lines[i];
        const continues = LIST.test(next) || INDENTED.test(next);
        const blankInside =
          !next.trim() && i + 1 < lines.length && (LIST.test(lines[i + 1]) || INDENTED.test(lines[i + 1]));
        if (!continues && !blankInside) break;
        body.push(next);
        i++;
      }
      html += renderList(body);
    } else {
      const para = [line.trim()];
      i++;
      while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) para.push(lines[i++].trim());
      html += `<p>${para.map(inline).join("<br>")}</p>`;
    }
  }
  return html;
}

function startsBlock(lines, i) {
  const line = lines[i];
  return (
    FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) ||
    LIST.test(line) || isTableStart(lines, i)
  );
}

function renderList(lines) {
  const first = lines[0].match(LIST);
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  for (const line of lines) {
    const m = line.match(LIST);
    if (m && m[1].length <= indent + 1) items.push({ head: m[3], rest: [] });
    else items[items.length - 1].rest.push(line);
  }
  const tag = ordered ? "ol" : "ul";
  const start = ordered ? parseInt(first[2], 10) : 1;
  const open = start !== 1 ? `<ol start="${start}">` : `<${tag}>`;
  const body = items
    .map((item) => {
      const rest = item.rest.some((l) => l.trim()) ? renderBlocks(dedent(item.rest)) : "";
      return `<li>${inline(item.head)}${rest}</li>`;
    })
    .join("");
  return `${open}${body}</${tag}>`;
}

function dedent(lines) {
  const indents = lines.filter((l) => l.trim()).map((l) => l.match(/^\s*/)[0].length);
  const n = Math.min(...indents);
  return lines.map((l) => l.slice(n));
}

function isTableStart(lines, i) {
  return (
    lines[i].includes("|") &&
    i + 1 < lines.length &&
    lines[i + 1].includes("-") &&
    TABLE_SEP.test(lines[i + 1])
  );
}

function splitRow(row) {
  return row
    .trim()
    .replace(/\\\|/g, "\u0001")
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim().replace(/\u0001/g, "|"));
}

function renderTable(lines, i, emit) {
  const head = splitRow(lines[i]);
  const aligns = splitRow(lines[i + 1]).map((c) =>
    c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : ""
  );
  i += 2;
  const rows = [];
  while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(splitRow(lines[i++]));

  const cell = (tag, text, col) => {
    const align = aligns[col] ? ` style="text-align:${aligns[col]}"` : "";
    return `<${tag}${align}>${inline(text ?? "")}</${tag}>`;
  };
  const thead = `<tr>${head.map((c, col) => cell("th", c, col)).join("")}</tr>`;
  const tbody = rows
    .map((row) => `<tr>${head.map((_, col) => cell("td", row[col], col)).join("")}</tr>`)
    .join("");
  emit(`<div class="table-wrap"><table><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`);
  return i;
}

function inline(text) {
  const codes = [];
  let s = text.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  s = escapeHtml(s);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (all, label, url) =>
    /^(https?:\/\/|mailto:)/i.test(url)
      ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`
      : all
  );
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?![\w])/g, "$1<strong>$2</strong>");
  s = s.replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w])_(?=\S)([^_]*?\S)_(?![\w])/g, "$1<em>$2</em>");
  s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, n) => `<code>${escapeHtml(codes[Number(n)])}</code>`);
}
