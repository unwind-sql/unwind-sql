import { useEffect, useState } from "react";
import { postCell } from "../api";
import { formatScalar } from "../format";
import type { CellValue, TraceResult } from "../types";
import { Investigate } from "./Investigate";
import { TraceTree } from "./TraceTree";

export interface CellRequest {
  model: string;
  column: string;
  where: Record<string, CellValue>;
  value: CellValue;
  isSource: boolean;
}

interface Props {
  request: CellRequest | null;
  onClose: () => void;
}

export function CellModal({ request, onClose }: Props) {
  const [trace, setTrace] = useState<TraceResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Close on Escape.
  useEffect(() => {
    if (!request) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [request, onClose]);

  // Fetch trace when the request changes.
  useEffect(() => {
    setTrace(null);
    setError(null);
    if (!request || request.isSource) return;
    let cancelled = false;
    setLoading(true);
    postCell(request.model, request.column, request.where)
      .then((t) => {
        if (!cancelled) setTrace(t);
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
  }, [request]);

  if (!request) return null;

  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2 className="modal-title">
            {request.model}.{request.column}
          </h2>
          <button className="close" aria-label="close" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="modal-meta">
          <span className="modal-value">= {formatScalar(request.value)}</span>
        </div>
        <div className="modal-body">
          {request.isSource ? (
            <p className="empty">
              <strong>{request.model}</strong> is a source — its columns come
              from external data.
            </p>
          ) : loading ? (
            <p className="empty">loading lineage…</p>
          ) : error ? (
            <p className="empty">{error}</p>
          ) : trace ? (
            <>
              <Investigate
                model={request.model}
                column={request.column}
                where={request.where}
              />
              <h3>Value lineage</h3>
              <div className="trace-tree">
                <TraceTree node={trace.root} />
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
