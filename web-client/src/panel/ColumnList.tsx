import { useEffect, useState } from "react";
import { fetchColumnLineage } from "../api";
import type { ColumnDescriptor, ColumnLineage } from "../types";
import { LineageTree } from "./LineageTree";

interface Props {
  modelName: string;
  columns: ColumnDescriptor[];
  isSource: boolean;
  onImpactClick: (modelName: string, column: string) => void;
}

export function ColumnList({ modelName, columns, isSource, onImpactClick }: Props) {
  const [active, setActive] = useState<string | null>(null);
  const [lineage, setLineage] = useState<ColumnLineage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Reset selection when the model changes.
  useEffect(() => {
    setActive(null);
    setLineage(null);
    setError(null);
  }, [modelName]);

  useEffect(() => {
    if (!active || isSource) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setLineage(null);
    fetchColumnLineage(modelName, active)
      .then((tree) => {
        if (!cancelled) setLineage(tree);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active, isSource, modelName]);

  return (
    <>
      <ul className="cols">
        {columns.map((c) => (
          <li
            key={c.name}
            className={`col ${active === c.name ? "active" : ""}`}
            onClick={() => setActive(c.name)}
          >
            <span>{c.name}</span>
            <span className="ty">{c.type}</span>
            <button
              type="button"
              className="col-impact"
              title={`Downstream impact of ${modelName}.${c.name}`}
              onClick={(e) => {
                // Don't bubble: opening the impact view shouldn't also
                // switch the column lineage selection underneath.
                e.stopPropagation();
                onImpactClick(modelName, c.name);
              }}
            >
              ↓ Impact
            </button>
          </li>
        ))}
      </ul>
      {active ? (
        <div className="lineage-section">
          <h3>Lineage of {active}</h3>
          {isSource ? (
            <p className="empty">
              <strong>{modelName}</strong> is a source — its columns come from
              external data.
            </p>
          ) : loading ? (
            <p className="empty">loading…</p>
          ) : error ? (
            <p className="empty">{error}</p>
          ) : lineage ? (
            <div className="lineage">
              <LineageTree node={lineage} />
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
