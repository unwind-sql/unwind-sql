import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useReactFlow } from "@xyflow/react";
import { paletteFor } from "./kinds";
import type { ModelKind } from "../types";

export interface SearchEntry {
  id: string;
  kind: ModelKind;
  group: string | null;
}

interface Props {
  /**
   * Full list of selectable models, in stable order. Comes from the raw
   * DAG payload (not React Flow's current store) so that members of a
   * collapsed group remain searchable — picking one expands the group.
   */
  searchSource: ReadonlyArray<SearchEntry>;
  /**
   * Called when the user picks a node from the dropdown. Mirrors what a
   * regular click on the node would do : opens the right-hand panel and
   * may expand the containing group if it was collapsed (the parent is
   * responsible for that bookkeeping).
   */
  onSelect: (nodeId: string) => void;
}

const MAX_RESULTS = 10;

export function NodeSearch({ searchSource, onSelect }: Props) {
  const rf = useReactFlow();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);

  // Match cmd/ctrl+K to focus the search box, regardless of which tab the
  // user is on. This is the de-facto shortcut for "search for a node" in
  // graph editors and feels native.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const isK = e.key === "k" || e.key === "K";
      if (isK && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const results = useMemo<SearchEntry[]>(() => {
    if (!query.trim()) return [];
    const needle = query.trim().toLowerCase();
    return searchSource
      .filter((n) => n.id.toLowerCase().includes(needle))
      .slice(0, MAX_RESULTS);
  }, [query, searchSource]);

  // Reset highlight when results change so the first match is always armed.
  useEffect(() => {
    setHighlight(0);
  }, [query]);

  const pick = useCallback(
    (n: SearchEntry) => {
      onSelect(n.id);
      // `onSelect` may have queued a state update (e.g. expanding the
      // collapsed group that contains `n.id`). React Flow's internal store
      // updates only after the parent re-renders, so we defer `fitView`
      // by two animation frames to give the rendered DAG time to settle.
      // Without this, `fitView` would silently no-op when the node wasn't
      // in the store yet.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          rf.fitView({ nodes: [{ id: n.id }], padding: 0.6, duration: 500 });
        });
      });
      setOpen(false);
      setQuery("");
      inputRef.current?.blur();
    },
    [onSelect, rf],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(results.length - 1, h + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const target = results[highlight];
      if (target) pick(target);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (query) {
        setQuery("");
      } else {
        setOpen(false);
        inputRef.current?.blur();
      }
    }
  };

  return (
    <div className="node-search">
      <div className="node-search-input-wrap">
        <span className="node-search-icon">⌕</span>
        <input
          ref={inputRef}
          className="node-search-input"
          placeholder="Search nodes…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <kbd className="node-search-kbd">⌘K</kbd>
      </div>
      {open && results.length > 0 && (
        <ul
          className="node-search-results"
          // mousedown fires before blur, so we can pick before the input
          // loses focus and tears down the dropdown.
          onMouseDown={(e) => e.preventDefault()}
        >
          {results.map((n, i) => {
            const palette = paletteFor(n.kind);
            return (
              <li
                key={n.id}
                className={`node-search-result ${i === highlight ? "active" : ""}`}
                onMouseEnter={() => setHighlight(i)}
                onClick={() => pick(n)}
              >
                <span
                  className="node-search-kind-dot"
                  style={{ background: palette.border }}
                  aria-hidden
                />
                <span className="node-search-result-name">{n.id}</span>
                {n.group ? (
                  <span className="node-search-result-group">{n.group}</span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {open && query.trim() && results.length === 0 && (
        <div className="node-search-empty">no match</div>
      )}
    </div>
  );
}
