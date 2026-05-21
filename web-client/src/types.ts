// Shape of the payload returned by GET /api/dag.
// Mirrors src/unwind/web/routes/dag.py::_dag_payload.

export type ModelKind =
  | "raw"
  | "ref"
  | "stg"
  | "int"
  | "fct"
  | "dim"
  | "mart"
  | "external"
  | "model";

export type Materialization = "table" | "view" | "external";

export type ModelLanguage = "sql" | "python";

export interface DagNode {
  id: string;
  kind: ModelKind;
  language: ModelLanguage;
  group: string | null;
  tags: string[];
  row_count: number | null;
  materialized: Materialization;
  location: string | null;
}

export interface DagEdge {
  from: string;
  to: string;
}

export interface DagGroup {
  id: string;
  members: string[];
}

export interface DagPayload {
  nodes: DagNode[];
  edges: DagEdge[];
  groups: DagGroup[];
}

// GET /api/model/{name}
export interface ColumnDescriptor {
  name: string;
  type: string;
}

export interface ModelDetail {
  name: string;
  language: ModelLanguage;
  source: string;
  row_count: number;
  columns: ColumnDescriptor[];
  upstream: string[];
  downstream: string[];
}

// GET /api/model/{name}/data
export type CellValue = string | number | boolean | null;

export interface ModelData {
  columns: ColumnDescriptor[];
  rows: CellValue[][];
  total: number;
  limit: number;
  offset: number;
}

// GET /api/column/{model}/{column}
export interface ColumnLineage {
  name: string;
  expression: string | null;
  upstream: ColumnLineage[];
}

// GET /api/column/{model}/{column}/impact
export type ImpactUsage = "projection" | "join" | "filter" | "group" | "order";

export interface ImpactedColumn {
  model: string;
  column: string;
  type: string;
  expression: string;
}

export interface ImpactEdge {
  parent_model: string;
  parent_column: string;
  child_model: string;
  child_column: string | null;
  usage: ImpactUsage;
}

export interface ColumnImpact {
  source: { model: string; column: string; type: string };
  affected: ImpactedColumn[];
  edges: ImpactEdge[];
  opaque_consumers: string[];
}

// POST /api/cell
export interface TraceNode {
  model: string;
  column: string;
  expression: string;
  substituted: string;
  values: CellValue[];
  value_count: number;
  predicate: Record<string, CellValue>;
  upstream: TraceNode[];
}

export interface TraceResult {
  model: string;
  column: string;
  where: Record<string, CellValue>;
  root: TraceNode;
}

// SSE /api/investigate
export interface Finding {
  model: string;
  column: string;
  value: CellValue;
  reason: string;
}

export interface Explanation {
  summary: string;
  findings: Finding[];
}

export type InvestigateEvent =
  | { event: "status"; data: { phase: "tracing" | "cached" | "llm" } }
  | { event: "done"; data: Explanation }
  | { event: "error"; data: { error: string } };
