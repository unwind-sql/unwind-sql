import { useState } from "react";
import { docsExportUrl, fetchColumnImpact } from "./api";
import { CellModal, type CellRequest } from "./cell/CellModal";
import { Graph } from "./graph/Graph";
import { Panel } from "./panel/Panel";
import { PanelResizer } from "./PanelResizer";
import type { CellValue, ColumnImpact } from "./types";

export function App() {
  const [selected, setSelected] = useState<string | null>(null);
  const [panelWidth, setPanelWidth] = useState(460);
  const [cell, setCell] = useState<CellRequest | null>(null);
  const [impact, setImpact] = useState<ColumnImpact | null>(null);
  const [impactError, setImpactError] = useState<string | null>(null);

  function openCell(
    model: string,
    column: string,
    where: Record<string, CellValue>,
    value: CellValue,
    isSource: boolean,
  ) {
    setCell({ model, column, where, value, isSource });
  }

  function openImpact(modelName: string, column: string) {
    setImpactError(null);
    fetchColumnImpact(modelName, column)
      .then((result) => {
        setImpact(result);
        // Pivot the DAG selection to the source so it's visually centered
        // amid the highlighted downstream.
        setSelected(modelName);
      })
      .catch((e: unknown) => {
        setImpactError(e instanceof Error ? e.message : String(e));
      });
  }

  function closeImpact() {
    setImpact(null);
    setImpactError(null);
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>unwind — DAG explorer</h1>
        {impactError ? (
          <span className="impact-error" role="alert">
            Impact analysis failed: {impactError}
          </span>
        ) : null}
        <nav className="app-export" aria-label="Documentation export">
          <a
            href={docsExportUrl("markdown")}
            className="export-btn"
            download="unwind-docs.md"
            title="Download a Markdown documentation file for the whole project"
          >
            ⬇ Docs (.md)
          </a>
          <a
            href={docsExportUrl("json")}
            className="export-btn"
            download="unwind-docs.json"
            title="Download the JSON manifest (LLM-ready semantic layer)"
          >
            ⬇ Docs (.json)
          </a>
        </nav>
      </header>
      <div className="app-main">
        <main className="app-graph">
          <Graph selected={selected} onSelect={setSelected} impact={impact} />
        </main>
        <PanelResizer onResize={setPanelWidth} />
        <aside
          className="app-panel"
          style={{ width: panelWidth, flex: `0 0 ${panelWidth}px` }}
        >
          <Panel
            modelName={selected}
            impact={impact}
            onImpactClick={openImpact}
            onImpactClose={closeImpact}
            onSelectModel={setSelected}
            onCellClick={openCell}
          />
        </aside>
      </div>
      <CellModal request={cell} onClose={() => setCell(null)} />
    </div>
  );
}
