import type { ReactNode } from "react";
import { createElement, Fragment } from "react";

const EMPHASIS_RE = /(\*\*[^\n*]+?\*\*|\*[^\n*]+?\*)/g;
// A line starting with "* " (star + whitespace) is a markdown bullet, not
// the opening of an *italic* span -- real replies mix both ("*   **Label:**
// value" bullets), and without this split the bullet's own star gets
// consumed as a stray italic delimiter, leaving a literal "*" behind
// exactly the bug this module exists to prevent. The distinguishing signal
// is the same one markdown itself uses: a space right after the star means
// bullet, no space means emphasis.
const BULLET_RE = /^(\s*)\*\s+(.*)$/;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(EMPHASIS_RE).map((part, i) => {
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      return createElement("strong", { key: `${keyPrefix}-${i}` }, part.slice(2, -2));
    }
    if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) {
      return createElement("em", { key: `${keyPrefix}-${i}` }, part.slice(1, -1));
    }
    return part;
  });
}

/**
 * The assistant's replies sometimes use light markdown (**bold**, *italic*,
 * "* " bullet lists) even though knowledge.md asks for plain language --
 * rather than fight the model turn by turn, render the (tiny) subset it
 * actually uses instead of showing raw asterisks in the chat transcript.
 * Deliberately not a full markdown parser: no links, headings, code
 * fences, or nested emphasis -- just enough to stop stray stars showing up
 * on a screen with no room for a real markdown renderer's overhead.
 */
export function renderMarkdownLite(text: string): ReactNode {
  const lines = text.split("\n");
  const nodes: ReactNode[] = [];
  lines.forEach((line, lineIndex) => {
    if (lineIndex > 0) nodes.push("\n");
    const bullet = line.match(BULLET_RE);
    if (bullet) {
      nodes.push(`${bullet[1]}• `);
      nodes.push(...renderInline(bullet[2], `${lineIndex}`));
    } else {
      nodes.push(...renderInline(line, `${lineIndex}`));
    }
  });
  return createElement(Fragment, null, ...nodes);
}
