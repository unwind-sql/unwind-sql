import { useEffect, useState } from "react";
import { fetchModel } from "../api";
import { highlightSource } from "../sql";
import type { CellValue, ColumnImpact, ModelDetail, ModelLanguage } from "../types";
import { ColumnList } from "./ColumnList";
import { DataTable } from "./DataTable";
import { DocView } from "./DocView";
import { ImpactPanel } from "./ImpactPanel";

interface Props {
  modelName: string | null;
  impact: ColumnImpact | null;
  onImpactClick: (modelName: string, column: string) => void;
  onImpactClose: () => void;
  onSelectModel: (modelName: string) => void;
  onCellClick: (
    model: string,
    column: string,
    where: Record<string, CellValue>,
    value: CellValue,
    isSource: boolean,
  ) => void;
}

type Tab = "columns" | "source" | "data" | "doc";

const LANG_LABEL: Record<ModelLanguage, string> = {
  sql: "SQL",
  python: "Python",
};

export function Panel({
  modelName,
  impact,
  onImpactClick,
  onImpactClose,
  onSelectModel,
  onCellClick,
}: Props) {
  const [model, setModel] = useState<ModelDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("columns");

  useEffect(() => {
    setModel(null);
    setError(null);
    setTab("columns");
    if (!modelName) return;
    let cancelled = false;
    fetchModel(modelName)
      .then((m) => {
        if (!cancelled) setModel(m);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [modelName]);

  // The impact view takes precedence over the regular model panel: when an
  // impact is active, the user is studying a downstream blast radius, not a
  // single model. Selecting a model in the impact list updates `modelName`
  // (so the DAG highlights it) but keeps the impact view on screen.
  if (impact) {
    return (
      <ImpactPanel
        impact={impact}
        onClose={onImpactClose}
        onSelectModel={onSelectModel}
      />
    );
  }

  if (!modelName) {
    return (
      <div className="panel-body">
        <h2>Pick a model</h2>
        <p className="empty">
          Click any node in the graph to see its columns and source. Click a
          column to trace its lineage, or use the ↓ Impact button on a column
          to see what would break if you renamed or retyped it.
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel-body">
        <h2>{modelName}</h2>
        <p className="empty">{error}</p>
      </div>
    );
  }

  if (!model) {
    return (
      <div className="panel-body">
        <h2>{modelName}</h2>
        <p className="empty">loading…</p>
      </div>
    );
  }

  const isSource = model.upstream.length === 0;
  const sourceLabel = LANG_LABEL[model.language];

  return (
    <div className="panel-body">
      <h2>
        {model.name}
        <span
          className="lang-pill"
          title={`This model is defined in ${sourceLabel}`}
        >
          {sourceLabel}
        </span>
      </h2>
      {model.description ? (
        <p className="model-description">{model.description}</p>
      ) : null}
      <p className="meta">
        {model.row_count.toLocaleString()} rows · {model.upstream.length}{" "}
        upstream · {model.downstream.length} downstream
      </p>
      <div className="tabs">
        <Btn label="Columns" active={tab === "columns"} on={() => setTab("columns")} />
        <Btn label={sourceLabel} active={tab === "source"} on={() => setTab("source")} />
        <Btn label="Data" active={tab === "data"} on={() => setTab("data")} />
        <Btn label="📖 Doc" active={tab === "doc"} on={() => setTab("doc")} />
      </div>
      {tab === "columns" ? (
        <ColumnList
          modelName={model.name}
          columns={model.columns}
          isSource={isSource}
          onImpactClick={onImpactClick}
        />
      ) : tab === "source" ? (
        <pre className="sql">
          <code
            className={`hljs language-${model.language}`}
            dangerouslySetInnerHTML={{
              __html: highlightSource(model.source.trim(), model.language),
            }}
          />
        </pre>
      ) : tab === "data" ? (
        <DataTable
          modelName={model.name}
          isSource={isSource}
          onCellClick={(column, where, value) =>
            onCellClick(model.name, column, where, value, isSource)
          }
        />
      ) : (
        <DocView modelName={model.name} />
      )}
    </div>
  );
}

function Btn({
  label,
  active,
  on,
}: {
  label: string;
  active: boolean;
  on: () => void;
}) {
  return (
    <button className={active ? "active" : ""} onClick={on}>
      {label}
    </button>
  );
}
