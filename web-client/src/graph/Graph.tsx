import { useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel as RfPanel,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { fetchDag } from "../api";
import type { ColumnImpact, DagPayload } from "../types";
import { paletteFor } from "./kinds";
import { paletteForGroup } from "./groups";
import { layoutDag } from "./layout";
import { ModelNode, type ImpactRole } from "./ModelNode";
import { GroupNode } from "./GroupNode";
import { NodeSearch } from "./NodeSearch";

const nodeTypes = { model: ModelNode, groupBox: GroupNode } as const;

const EDGE_STYLES = ["smoothstep", "step", "bezier", "straight"] as const;
type EdgeStyle = (typeof EDGE_STYLES)[number];

interface Props {
  selected: string | null;
  onSelect: (modelName: string) => void;
  impact: ColumnImpact | null;
}

// Edge tints for the impact-highlight mode. Picked to match ModelNode's
// per-role border colours so a tinted edge and the nodes it joins read as
// belonging to the same impact "layer".
const IMPACT_EDGE_PROJECTION = "#dc2626";
const IMPACT_EDGE_STRUCTURAL = "#f59e0b";

function payloadToFlow(
  dag: DagPayload,
  edgeStyle: EdgeStyle,
): { nodes: Node[]; edges: Edge[] } {
  const memberNodes: Node[] = dag.nodes.map((n) => ({
    id: n.id,
    type: "model",
    position: { x: 0, y: 0 },
    data: {
      label: n.id,
      kind: n.kind,
      language: n.language,
      group: n.group,
      tags: n.tags,
      rowCount: n.row_count,
    },
  }));
  const edges: Edge[] = dag.edges.map((e) => ({
    id: `${e.from}->${e.to}`,
    source: e.from,
    target: e.to,
    type: edgeStyle,
    animated: false,
    style: { stroke: "#94a3b8", strokeWidth: 1.4 },
  }));

  // dagre runs in compound mode when there are groups, which keeps each
  // cluster spatially tight regardless of source/sink rank distance.
  const laid = layoutDag(memberNodes, edges, dag.groups ?? []);

  // Synthesize one container node per cluster from the bounds returned by
  // dagre. Render them behind everything else and make them non-interactive.
  const groupNodes: Node[] = laid.groups.map((gb, idx) => {
    const palette = paletteForGroup(idx);
    return {
      id: `group:${gb.id}`,
      type: "groupBox",
      position: { x: gb.x, y: gb.y },
      width: gb.width,
      height: gb.height,
      data: {
        label: gb.id,
        border: palette.border,
        fill: palette.fill,
        labelColor: palette.label,
      },
      selectable: false,
      draggable: false,
      focusable: false,
      zIndex: -1,
    };
  });

  // React Flow requires parents to appear before their children.
  return { nodes: [...groupNodes, ...laid.nodes], edges };
}

/**
 * Classify each model in the DAG by its role in the current impact analysis:
 *
 * - `source` — the column's origin model (anchor of the analysis).
 * - `projection` — a model where one or more output columns rebuild their
 *   value from the source. A rename breaks code paths; a type change asks
 *   for re-validation of casts and downstream comparisons.
 * - `structural` — a model that references the source in JOIN / WHERE /
 *   GROUP / ORDER but doesn't re-emit it as a named column. A rename still
 *   breaks the SQL.
 * - `opaque` — a Python sink whose body we can't inspect. Surface it so the
 *   user audits it by hand.
 */
function computeImpactRoles(impact: ColumnImpact | null): Map<string, ImpactRole> {
  const roles = new Map<string, ImpactRole>();
  if (!impact) return roles;
  roles.set(impact.source.model, "source");
  for (const c of impact.affected) {
    if (!roles.has(c.model)) roles.set(c.model, "projection");
  }
  for (const e of impact.edges) {
    if (e.usage === "projection") continue;
    if (!roles.has(e.child_model)) roles.set(e.child_model, "structural");
  }
  for (const m of impact.opaque_consumers) {
    if (!roles.has(m)) roles.set(m, "opaque");
  }
  return roles;
}

export function Graph({ selected, onSelect, impact }: Props) {
  const [data, setData] = useState<DagPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edgeStyle, setEdgeStyle] = useState<EdgeStyle>("smoothstep");

  useEffect(() => {
    let cancelled = false;
    fetchDag()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const flow = useMemo(() => {
    if (!data) return null;
    const f = payloadToFlow(data, edgeStyle);

    // Direct neighbours of the focused node : edges arriving = upstream
    // (its dependencies), edges leaving = downstream (its dependents).
    const upstream = new Set<string>();
    const downstream = new Set<string>();
    if (selected) {
      for (const e of f.edges) {
        if (e.target === selected) upstream.add(e.source);
        if (e.source === selected) downstream.add(e.target);
      }
    }

    const impactRoles = computeImpactRoles(impact);

    return {
      ...f,
      nodes: f.nodes.map((n) => {
        if (n.type !== "model") return n;
        // Impact overrides the regular upstream/downstream halo because the
        // user is asking a different question.
        const impactRole = impactRoles.get(n.id) ?? null;
        const relation = impactRole
          ? null
          : upstream.has(n.id)
            ? "upstream"
            : downstream.has(n.id)
              ? "downstream"
              : null;
        return {
          ...n,
          selected: n.id === selected,
          data: { ...n.data, relation, impactRole },
        };
      }),
      edges: f.edges.map((e) => {
        // Tint edges between two impacted nodes when impact mode is on.
        if (impact) {
          const sourceRole = impactRoles.get(e.source);
          const targetRole = impactRoles.get(e.target);
          if (sourceRole && targetRole) {
            const tint =
              targetRole === "projection"
                ? IMPACT_EDGE_PROJECTION
                : IMPACT_EDGE_STRUCTURAL;
            return {
              ...e,
              animated: true,
              style: { ...e.style, stroke: tint, strokeWidth: 2 },
            };
          }
          // Mute everything else when an impact is active so the highlight pops.
          return {
            ...e,
            animated: false,
            style: { ...e.style, stroke: "#e2e8f0", strokeWidth: 1 },
          };
        }
        const isUp = e.target === selected;
        const isDown = e.source === selected;
        if (!isUp && !isDown) return e;
        return {
          ...e,
          animated: true,
          style: {
            ...e.style,
            stroke: isUp ? "#0ea5e9" : "#f59e0b",
            strokeWidth: 2,
          },
        };
      }),
    };
  }, [data, selected, edgeStyle, impact]);

  const onNodeClick: NodeMouseHandler = (_evt, n) => {
    if (n.type !== "model") return;
    onSelect(n.id);
  };

  if (error) {
    return (
      <div style={{ padding: "1rem", color: "#b91c1c" }}>
        Failed to load DAG: {error}
      </div>
    );
  }
  if (!flow) {
    return <div style={{ padding: "1rem", color: "#666" }}>Loading DAG…</div>;
  }

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={flow.nodes}
        edges={flow.edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        fitView
        proOptions={{ hideAttribution: true }}
        minZoom={0.15}
        maxZoom={2}
        defaultEdgeOptions={{ type: edgeStyle }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls position="bottom-left" showInteractive={true} />
        <MiniMap
          position="bottom-right"
          pannable
          zoomable
          nodeColor={(n) => {
            if (n.type === "groupBox") {
              return (n.data as { fill?: string } | undefined)?.fill ?? "#eef3ff";
            }
            const kind = (n.data as { kind?: string } | undefined)?.kind ?? "model";
            return paletteFor(kind as never).border;
          }}
          maskColor="rgba(241, 245, 249, 0.6)"
          style={{ background: "#fff", border: "1px solid #e5e7eb" }}
        />
        <RfPanel position="top-left">
          <NodeSearch onSelect={onSelect} />
        </RfPanel>
        <RfPanel position="top-right" className="edge-style-panel">
          <label>
            edges
            <select
              value={edgeStyle}
              onChange={(e) => setEdgeStyle(e.target.value as EdgeStyle)}
            >
              {EDGE_STYLES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        </RfPanel>
      </ReactFlow>
    </ReactFlowProvider>
  );
}
