import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import "highlight.js/styles/atom-one-light.css";

import type { ModelLanguage } from "./types";

hljs.registerLanguage("sql", sql);
hljs.registerLanguage("python", python);

export function highlightSource(code: string, language: ModelLanguage): string {
  return hljs.highlight(code, { language }).value;
}

/** Convenience: highlight a SQL expression (used by lineage/trace trees). */
export function highlightSql(code: string): string {
  return highlightSource(code, "sql");
}
