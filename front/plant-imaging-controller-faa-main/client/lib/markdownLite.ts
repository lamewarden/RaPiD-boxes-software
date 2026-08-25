import type { ReactNode } from "react";
import { createElement, Fragment } from "react";

/**
 * The assistant's replies sometimes use `**bold**`/`*italic*` even though
 * knowledge.md asks for plain language -- rather than fight the model
 * turn by turn, render the (tiny) subset of markdown it actually uses
 * instead of showing raw asterisks in the chat transcript. Deliberately not
 * a full markdown parser: no links, headings, code fences, or nested
 * emphasis -- just enough to stop `**word**` from reading as literal stars
 * on a screen with no room for a real markdown renderer's overhead.
 */
export function renderMarkdownLite(text: string): ReactNode {
  const parts = text.split(/(\*\*[^\n*]+?\*\*|\*[^\n*]+?\*)/g);
  return createElement(
    Fragment,
    null,
    ...parts.map((part, i) => {
      if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
        return createElement("strong", { key: i }, part.slice(2, -2));
      }
      if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) {
        return createElement("em", { key: i }, part.slice(1, -1));
      }
      return part;
    }),
  );
}
