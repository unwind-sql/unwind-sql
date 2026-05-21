import { useEffect, useState } from "react";
import { fetchModelData } from "../api";
import { formatScalar, isNumeric } from "../format";
import type { CellValue, ModelData } from "../types";

interface Props {
  modelName: string;
  isSource: boolean;
  onCellClick: (
    column: string,
    where: Record<string, CellValue>,
    value: CellValue,
  ) => void;
}

const PAGE_SIZE = 100;

export function DataTable({ modelName, isSource, onCellClick }: Props) {
  const [data, setData] = useState<ModelData | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset on model change.
  useEffect(() => {
    setOffset(0);
    setData(null);
    setError(null);
  }, [modelName]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchModelData(modelName, offset, PAGE_SIZE)
      .then((d) => {
        if (!cancelled) setData(d);
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
  }, [modelName, offset]);

  if (loading && !data) return <p className="empty">loading…</p>;
  if (error) return <p className="empty">{error}</p>;
  if (!data) return null;

  const start = data.total === 0 ? 0 : data.offset + 1;
  const end = Math.min(data.offset + data.rows.length, data.total);

  function handleClick(rowIdx: number, colIdx: number) {
    if (!data) return;
    const row = data.rows[rowIdx];
    const col = data.columns[colIdx];
    const where: Record<string, CellValue> = {};
    for (let i = 0; i < data.columns.length; i++) {
      where[data.columns[i].name] = row[i];
    }
    onCellClick(col.name, where, row[colIdx]);
  }

  return (
    <>
      <div className="data-pager">
        <button
          className="prev"
          disabled={data.offset === 0}
          onClick={() => setOffset(Math.max(0, data.offset - data.limit))}
        >
          ‹ prev
        </button>
        <span>
          {start.toLocaleString()}–{end.toLocaleString()} /{" "}
          {data.total.toLocaleString()}
        </span>
        <button
          className="next"
          disabled={data.offset + data.rows.length >= data.total}
          onClick={() => setOffset(data.offset + data.limit)}
        >
          next ›
        </button>
        <span className="pager-meta">page size {data.limit}</span>
      </div>
      <div className="data-table-wrap">
        <table className="data">
          <thead>
            <tr>
              {data.columns.map((c) => (
                <th key={c.name}>
                  <div className="col-name">{c.name}</div>
                  <div className="ty">{c.type}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, i) => (
              <tr key={i}>
                {row.map((v, j) => {
                  const cls = v === null ? "null" : isNumeric(v) ? "num" : "";
                  return (
                    <td
                      key={j}
                      className={cls}
                      onClick={() => handleClick(i, j)}
                      title={isSource ? "(source — no upstream lineage)" : ""}
                    >
                      {formatScalar(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
