import { useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel as RfPanel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { fetchDag } from "../api";
import type { ColumnImpact, DagPayload, ModelKind } from "../types";
import { paletteFor } from "./kinds";
import { layoutDag } from "./layout";
import { ModelNode, type ImpactRole } from "./ModelNode";
import { GroupNode } from "./GroupNode";
import { GroupCollapsedNode } from "./GroupCollapsedNode";
import { NodeSearch } from "./NodeSearch";
import { FilterPanel } from "./FilterPanel";
import {
  deriveEffectiveGroups,
  type EffectiveGroup,
} from "./effectiveGroups";

const nodeTypes = {
  model: ModelNode,
  groupBox: GroupNode,
  groupCollapsed: GroupCollapsedNode,
} as const;

/**
 * Width/height of a collapsed-group representative node. Wider than a
 * member node so it can fit a label like "consolidation · 14 models".
 */
const REP_NODE_WIDTH = 220;
const REP_NODE_HEIGHT = 64;

/**
 * Threshold beyond which the DAG opens with every group auto-collapsed.
 * Picked empirically — a DAG with 30+ nodes is usually too dense for the
 * member view to read at a glance.
 */
const AUTO_COLLAPSE_THRESHOLD = 30;

/**
 * Inner helper that lives inside `<ReactFlowProvider>` so it can use
 * `useReactFlow`. Re-fits the viewport whenever its `trigger` changes —
 * driven by `collapsedGroups` from the parent so every expand/collapse
 * action (including the initial auto-collapse decision) ends with the
 * graph nicely centered.
 *
 * Two RAFs of delay so that the parent's state change has been committed
 * AND React Flow's internal store has registered the new nodes before
 * `fitView` resolves the bounding box.
 */
function ViewportSync({ trigger }: { trigger: unknown }) {
  const rf = useReactFlow();
  useEffect(() => {
    let cancelled = false;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (cancelled) return;
        rf.fitView({ padding: 0.15, duration: 400 });
      });
    });
    return () => {
      cancelled = true;
    };
  }, [trigger, rf]);
  return null;
}

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

interface FlowOpts {
  hiddenKinds: Set<ModelKind>;
  hiddenGroups: Set<string>;
  collapsedGroups: Set<string>;
  effectiveGroups: EffectiveGroup[];
  groupOf: Map<string, string>;
}

function payloadToFlow(
  dag: DagPayload,
  edgeStyle: EdgeStyle,
  opts: FlowOpts,
): { nodes: Node[]; edges: Edge[] } {
  const { effectiveGroups, groupOf, collapsedGroups } = opts;

  // --- 1. Determine which nodes are hidden by the filter panel ---
  // Their `kind` is unchecked, or their declared group is unchecked. They
  // are removed from the rendered DAG, but bypass edges are computed
  // around them so the surviving upstream and downstream nodes keep
  // showing the data flow that *did* exist.
  const filterHiddenIds = new Set(
    dag.nodes
      .filter(
        (n) =>
          opts.hiddenKinds.has(n.kind) ||
          (n.group !== null && opts.hiddenGroups.has(n.group)),
      )
      .map((n) => n.id),
  );

  // --- 2. Representative resolution ---
  // The "rep" of a node is what's drawn in its place: itself when visible,
  // a `groupRep:${gid}` synthetic node when its effective group is
  // collapsed, or `null` when the node is filter-hidden (no anchor —
  // bypass handles edge continuity).
  function repOf(id: string): string | null {
    if (filterHiddenIds.has(id)) return null;
    const g = groupOf.get(id);
    if (g !== undefined && collapsedGroups.has(g)) return `groupRep:${g}`;
    return id;
  }
  function isVisibleMember(id: string): boolean {
    if (filterHiddenIds.has(id)) return false;
    const g = groupOf.get(id);
    return !(g !== undefined && collapsedGroups.has(g));
  }

  // --- 3. Build member nodes (visible-as-themselves only) ---
  const memberNodes: Node[] = dag.nodes
    .filter((n) => isVisibleMember(n.id))
    .map((n) => ({
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
        disabled: n.disabled,
        description: n.description,
      },
    }));

  // --- 4. Build representative nodes for collapsed groups with content ---
  // A collapsed group with every member filter-hidden contributes nothing
  // to the rendered DAG — skip its rep entirely.
  const repNodes: Node[] = [];
  for (const g of effectiveGroups) {
    if (!collapsedGroups.has(g.id)) continue;
    const visibleMembers = g.members.filter((m) => !filterHiddenIds.has(m));
    if (visibleMembers.length === 0) continue;
    repNodes.push({
      id: `groupRep:${g.id}`,
      type: "groupCollapsed",
      position: { x: 0, y: 0 },
      width: REP_NODE_WIDTH,
      height: REP_NODE_HEIGHT,
      data: {
        groupId: g.id,
        label: g.label,
        memberCount: visibleMembers.length,
        totalCount: g.members.length,
        auto: g.auto,
        border: g.palette.border,
        fill: g.palette.fill,
        labelColor: g.palette.label,
      },
    });
  }

  // --- 5. Bypass scaffolding ---
  const disabledIds = new Set(dag.nodes.filter((n) => n.disabled).map((n) => n.id));
  // Disabled and filter-hidden nodes both want the same bypass treatment:
  // walk through them and connect their visible upstream to visible
  // downstream. The visual difference (muted real edge vs nothing)
  // lives upstream of this set; the bypass logic itself is unified.
  const bypassIds = new Set<string>([...disabledIds, ...filterHiddenIds]);

  const parentsOf = new Map<string, string[]>();
  const childrenOf = new Map<string, string[]>();
  for (const e of dag.edges) {
    if (!parentsOf.has(e.to)) parentsOf.set(e.to, []);
    parentsOf.get(e.to)!.push(e.from);
    if (!childrenOf.has(e.from)) childrenOf.set(e.from, []);
    childrenOf.get(e.from)!.push(e.to);
  }
  function resolveUpstream(name: string, seen: Set<string>): string[] {
    const out: string[] = [];
    for (const p of parentsOf.get(name) ?? []) {
      if (seen.has(p)) continue;
      seen.add(p);
      if (bypassIds.has(p)) out.push(...resolveUpstream(p, seen));
      else out.push(p);
    }
    return out;
  }
  function resolveDownstream(name: string, seen: Set<string>): string[] {
    const out: string[] = [];
    for (const c of childrenOf.get(name) ?? []) {
      if (seen.has(c)) continue;
      seen.add(c);
      if (bypassIds.has(c)) out.push(...resolveDownstream(c, seen));
      else out.push(c);
    }
    return out;
  }

  // --- 6. Real edges, after collapse-to-representative ---
  // For every dag edge whose endpoints are not filter-hidden, map both
  // endpoints to their representative. Same-rep edges (intra-collapsed-
  // group) disappear; cross-rep edges are deduplicated. When multiple
  // member→member edges collapse to the same rep pair, prefer the
  // non-muted variant so the rendered group→group connection looks live.
  type EdgeAcc = { source: string; target: string; muted: boolean };
  const realByKey = new Map<string, EdgeAcc>();
  for (const e of dag.edges) {
    if (filterHiddenIds.has(e.from) || filterHiddenIds.has(e.to)) continue;
    const u = repOf(e.from);
    const v = repOf(e.to);
    if (u === null || v === null) continue;
    if (u === v) continue;
    const key = `${u}->${v}`;
    const muted = disabledIds.has(e.from) || disabledIds.has(e.to);
    const existing = realByKey.get(key);
    if (!existing) realByKey.set(key, { source: u, target: v, muted });
    else if (existing.muted && !muted)
      realByKey.set(key, { source: u, target: v, muted });
  }
  const realEdges: Edge[] = [...realByKey.values()].map((acc) => ({
    id: `${acc.source}->${acc.target}`,
    source: acc.source,
    target: acc.target,
    type: edgeStyle,
    animated: false,
    style: acc.muted
      ? { stroke: "#cbd5e1", strokeWidth: 1, strokeDasharray: "4 4" }
      : { stroke: "#94a3b8", strokeWidth: 1.4 },
  }));

  // --- 7. Bypass edges, after collapse-to-representative ---
  // For every bypassed node, gather the visible upstream and downstream
  // walking through bypass chains. Then map endpoints to representatives.
  // Pre-seed dedupe with the keys of real edges so a bypass coinciding
  // with an existing direct edge isn't drawn on top of it.
  const coveredKeys = new Set<string>(realByKey.keys());
  const bypassEdges: Edge[] = [];
  for (const id of bypassIds) {
    const ups = resolveUpstream(id, new Set([id]));
    const downs = resolveDownstream(id, new Set([id]));
    for (const u of ups) {
      for (const d of downs) {
        const ru = repOf(u);
        const rd = repOf(d);
        if (ru === null || rd === null) continue;
        if (ru === rd) continue;
        const key = `${ru}->${rd}`;
        if (coveredKeys.has(key)) continue;
        coveredKeys.add(key);
        bypassEdges.push({
          id: `bypass:${key}`,
          source: ru,
          target: rd,
          type: edgeStyle,
          animated: false,
          style: {
            stroke: "#64748b",
            strokeWidth: 1.4,
            strokeDasharray: "2 6",
          },
          // Sit visually above the muted real edges so the bypass reads
          // as the actual data path.
          zIndex: 1,
        });
      }
    }
  }

  // --- 8. Layout ---
  // Expanded groups (not collapsed, with at least one visible member)
  // become compound clusters. Reps live alongside as regular nodes.
  const groupsForLayout = effectiveGroups
    .filter((g) => !collapsedGroups.has(g.id))
    .map((g) => ({
      id: g.id,
      members: g.members.filter((m) => isVisibleMember(m)),
    }))
    .filter((g) => g.members.length > 0);

  const laid = layoutDag(
    [...memberNodes, ...repNodes],
    realEdges,
    groupsForLayout,
  );
  const edges = [...realEdges, ...bypassEdges];

  // --- 9. Group containers for expanded groups ---
  // Each `laid.groups` entry is one expanded effective-group; look it
  // back up to know its label, palette, and whether it's auto-derived.
  const effectiveById = new Map(effectiveGroups.map((g) => [g.id, g]));
  const groupNodes: Node[] = laid.groups.map((gb) => {
    const eff = effectiveById.get(gb.id);
    const palette = eff?.palette ?? {
      border: "#94a3b8",
      fill: "#f8fafc",
      label: "#64748b",
    };
    return {
      id: `group:${gb.id}`,
      type: "groupBox",
      position: { x: gb.x, y: gb.y },
      width: gb.width,
      height: gb.height,
      data: {
        label: eff?.label ?? gb.id,
        border: palette.border,
        fill: palette.fill,
        labelColor: palette.label,
        auto: eff?.auto ?? false,
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
  const [hiddenKinds, setHiddenKinds] = useState<Set<ModelKind>>(() => new Set());
  const [hiddenGroups, setHiddenGroups] = useState<Set<string>>(() => new Set());
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  /**
   * `true` until the first `data` arrives and we've decided the initial
   * collapsed state. Without this guard, the auto-collapse effect would
   * fire on every data refresh and override the user's choices.
   */
  const [collapseInitialised, setCollapseInitialised] = useState(false);

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

  // Effective groups (real + auto-from-kind) — keyed off `data` only, so
  // the derivation is stable across filter / collapse interactions.
  const { effectiveGroups, groupOf } = useMemo<{
    effectiveGroups: EffectiveGroup[];
    groupOf: Map<string, string>;
  }>(() => {
    if (!data) {
      return {
        effectiveGroups: [],
        groupOf: new Map<string, string>(),
      };
    }
    const derived = deriveEffectiveGroups(data);
    return { effectiveGroups: derived.groups, groupOf: derived.groupOf };
  }, [data]);

  // First time data arrives: open every group collapsed when the DAG is
  // large enough to be visually overwhelming. Otherwise leave them all
  // expanded so small DAGs render exactly as before.
  useEffect(() => {
    if (!data || collapseInitialised) return;
    if (data.nodes.length > AUTO_COLLAPSE_THRESHOLD) {
      setCollapsedGroups(new Set(effectiveGroups.map((g) => g.id)));
    }
    setCollapseInitialised(true);
  }, [data, collapseInitialised, effectiveGroups]);

  // Filter-panel inputs derived from the current DAG.
  const availableKinds = useMemo<ModelKind[]>(() => {
    if (!data) return [];
    const seen = new Set<ModelKind>();
    for (const n of data.nodes) seen.add(n.kind);
    // Stable, kind-prefix ordering so the checkboxes don't jiggle between
    // reloads. Kinds not present in the DAG are filtered out.
    const order: ModelKind[] = [
      "raw",
      "ref",
      "stg",
      "int",
      "fct",
      "dim",
      "mart",
      "external",
      "model",
    ];
    return order.filter((k) => seen.has(k));
  }, [data]);

  const availableGroups = useMemo<string[]>(() => {
    if (!data) return [];
    return [...data.groups.map((g) => g.id)].sort();
  }, [data]);

  // If a group disappears from the DAG (e.g. removed in the project), drop it
  // from the hidden set so stale ids don't linger.
  useEffect(() => {
    if (!data) return;
    const known = new Set(availableGroups);
    setHiddenGroups((prev) => {
      const next = new Set<string>();
      for (const g of prev) if (known.has(g)) next.add(g);
      return next.size === prev.size ? prev : next;
    });
  }, [data, availableGroups]);

  const toggleKind = (k: ModelKind) =>
    setHiddenKinds((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  const toggleGroup = (g: string) =>
    setHiddenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });
  const clearFilters = () => {
    setHiddenKinds(new Set());
    setHiddenGroups(new Set());
  };

  // Group toggle actions used by the collapsed-rep click handler and the
  // toolbar buttons.
  const toggleGroupCollapsed = (gid: string) =>
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(gid)) next.delete(gid);
      else next.add(gid);
      return next;
    });
  const collapseAll = () =>
    setCollapsedGroups(new Set(effectiveGroups.map((g) => g.id)));
  const expandAll = () => setCollapsedGroups(new Set());

  const collapsedCount = collapsedGroups.size;
  const totalGroups = effectiveGroups.length;

  // Search source: full DAG so members of collapsed groups stay
  // searchable. Picking such a member expands its containing group before
  // the search dropdown fits-view to it.
  const searchSource = useMemo(
    () =>
      data
        ? data.nodes.map((n) => ({ id: n.id, kind: n.kind, group: n.group }))
        : [],
    [data],
  );
  const onSelectFromSearch = (id: string) => {
    const g = groupOf.get(id);
    if (g !== undefined && collapsedGroups.has(g)) {
      setCollapsedGroups((prev) => {
        const next = new Set(prev);
        next.delete(g);
        return next;
      });
    }
    onSelect(id);
  };

  const flow = useMemo(() => {
    if (!data) return null;
    const f = payloadToFlow(data, edgeStyle, {
      hiddenKinds,
      hiddenGroups,
      collapsedGroups,
      effectiveGroups,
      groupOf,
    });

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
  }, [
    data,
    selected,
    edgeStyle,
    impact,
    hiddenKinds,
    hiddenGroups,
    collapsedGroups,
    effectiveGroups,
    groupOf,
  ]);

  const onNodeClick: NodeMouseHandler = (_evt, n) => {
    if (n.type === "model") {
      onSelect(n.id);
      return;
    }
    if (n.type === "groupCollapsed") {
      const gid = (n.data as { groupId?: string } | undefined)?.groupId;
      if (gid) toggleGroupCollapsed(gid);
    }
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
        <ViewportSync trigger={collapsedGroups} />
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls position="bottom-left" showInteractive={true} />
        <MiniMap
          position="bottom-right"
          pannable
          zoomable
          nodeColor={(n) => {
            if (n.type === "groupBox" || n.type === "groupCollapsed") {
              return (n.data as { fill?: string } | undefined)?.fill ?? "#eef3ff";
            }
            const kind = (n.data as { kind?: string } | undefined)?.kind ?? "model";
            return paletteFor(kind as never).border;
          }}
          maskColor="rgba(241, 245, 249, 0.6)"
          style={{ background: "#fff", border: "1px solid #e5e7eb" }}
        />
        <RfPanel position="top-left">
          <div className="graph-top-left-stack">
            <NodeSearch
              searchSource={searchSource}
              onSelect={onSelectFromSearch}
            />
            <FilterPanel
              availableKinds={availableKinds}
              availableGroups={availableGroups}
              hiddenKinds={hiddenKinds}
              hiddenGroups={hiddenGroups}
              onToggleKind={toggleKind}
              onToggleGroup={toggleGroup}
              onClear={clearFilters}
            />
            {totalGroups > 0 && (
              <div className="group-toolbar">
                <span className="group-toolbar-summary">
                  {collapsedCount} / {totalGroups} collapsed
                </span>
                <button
                  type="button"
                  onClick={collapseAll}
                  disabled={collapsedCount === totalGroups}
                  title="Collapse every group"
                >
                  collapse all
                </button>
                <button
                  type="button"
                  onClick={expandAll}
                  disabled={collapsedCount === 0}
                  title="Expand every group"
                >
                  expand all
                </button>
              </div>
            )}
          </div>
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
