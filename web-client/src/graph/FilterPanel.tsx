import { useMemo, useState } from "react";
import type { ModelKind } from "../types";
import { paletteFor } from "./kinds";

interface Props {
  /** Kinds present in the current DAG, in stable order. */
  availableKinds: ModelKind[];
  /** Real groups present in the current DAG (does not include auto-groups). */
  availableGroups: string[];
  hiddenKinds: Set<ModelKind>;
  hiddenGroups: Set<string>;
  onToggleKind: (kind: ModelKind) => void;
  onToggleGroup: (group: string) => void;
  onClear: () => void;
}

/**
 * Collapsible panel exposing two filter dimensions :
 *
 * - **Kind** : hide every model of a given prefix (`raw_`, `ref_`, ...).
 *   Useful to suppress the bulk of source nodes that dominate large DAGs.
 * - **Group** : hide every model belonging to a declared `@group:` cluster.
 *
 * Hiding a node makes its incident real edges disappear; bypass edges
 * (computed in Graph.tsx) reroute the data flow around the gap, so the
 * remaining DAG keeps showing the relationship between the still-visible
 * upstream and downstream nodes.
 */
export function FilterPanel({
  availableKinds,
  availableGroups,
  hiddenKinds,
  hiddenGroups,
  onToggleKind,
  onToggleGroup,
  onClear,
}: Props) {
  const [open, setOpen] = useState(false);
  const activeCount = hiddenKinds.size + hiddenGroups.size;
  const summary = useMemo(() => {
    if (activeCount === 0) return "all visible";
    return `${activeCount} hidden`;
  }, [activeCount]);

  return (
    <div className="filter-panel">
      <button
        type="button"
        className="filter-panel-toggle"
        onClick={() => setOpen((o) => !o)}
        title={open ? "Hide filters" : "Show filters"}
      >
        <span className="filter-panel-icon" aria-hidden>
          ⌗
        </span>
        <span>filter</span>
        <span className="filter-panel-summary">{summary}</span>
        <span className="filter-panel-chevron" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <div className="filter-panel-body">
          {availableKinds.length > 0 && (
            <section className="filter-panel-section">
              <header>kinds</header>
              <ul>
                {availableKinds.map((k) => {
                  const palette = paletteFor(k);
                  const hidden = hiddenKinds.has(k);
                  return (
                    <li key={k}>
                      <label>
                        <input
                          type="checkbox"
                          checked={!hidden}
                          onChange={() => onToggleKind(k)}
                        />
                        <span
                          className="filter-panel-dot"
                          style={{ background: palette.border }}
                          aria-hidden
                        />
                        <span className={hidden ? "filter-panel-off" : ""}>
                          {k}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
          {availableGroups.length > 0 && (
            <section className="filter-panel-section">
              <header>groups</header>
              <ul>
                {availableGroups.map((g) => {
                  const hidden = hiddenGroups.has(g);
                  return (
                    <li key={g}>
                      <label>
                        <input
                          type="checkbox"
                          checked={!hidden}
                          onChange={() => onToggleGroup(g)}
                        />
                        <span className={hidden ? "filter-panel-off" : ""}>
                          {g}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
          {activeCount > 0 && (
            <button
              type="button"
              className="filter-panel-clear"
              onClick={onClear}
            >
              reset
            </button>
          )}
        </div>
      )}
    </div>
  );
}
