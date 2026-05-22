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
  disabled: boolean;
  /** First non-empty line of the model description, or null when none. */
  description: string | null;
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
  /** Native (trailing comment) or inherited column description. */
  description?: string;
  /** When the description was inherited via column lineage, the source `model.column`. */
  inherited_from?: string;
}

export interface ModelDetail {
  name: string;
  language: ModelLanguage;
  source: string;
  description: string | null;
  row_count: number;
  columns: ColumnDescriptor[];
  upstream: string[];
  downstream: string[];
}

// GET /api/docs/{name} — see src/unwind/docs/ir.py
export interface ColumnStats {
  row_count: number;
  null_count: number;
  distinct_count: number;
}

export interface ColumnDoc {
  name: string;
  type: string | null;
  description: string | null;
  inherited_from: string | null;
  stats: ColumnStats | null;
}

export interface Annotation {
  line: number;
  text: string;
}

export interface ModelDoc {
  name: string;
  description: string | null;
  group: string | null;
  tags: string[];
  materialized: Materialization;
  kind: "sql" | "python";
  columns: ColumnDoc[];
  annotations: Annotation[];
  upstreams: string[];
  downstreams: string[];
  rendered_sql: string | null;
}

export interface Documentation {
  _schema: {
    purpose: string;
    fields: Record<string, string>;
  };
  project_root: string | null;
  models: ModelDoc[];
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
