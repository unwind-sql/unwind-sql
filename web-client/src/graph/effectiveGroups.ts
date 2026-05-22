import type { DagNode, DagPayload, ModelKind } from "../types";
import { paletteFor } from "./kinds";
import { paletteForGroup } from "./groups";

/**
 * A group as seen by the rendering layer. Either a "real" group declared
 * by the user via `-- @group:` (carried by `DagPayload.groups`) or an
 * auto-group derived from the model's `kind` prefix when no explicit
 * group was declared.
 *
 * Auto-groups give a meaningful structure to DAGs whose owners haven't
 * (yet) annotated their models. Without them, the collapse mechanism
 * couldn't compact the orphans, defeating the purpose of compact view.
 */
export interface EffectiveGroup {
  /**
   * Stable ID — also used as React Flow node id (`group:${id}`) and as a
   * key in the collapsed-state map. Real groups keep their declared name;
   * auto-groups use the `auto:${kind}` prefix to avoid colliding with a
   * real group that happens to share a kind name.
   */
  id: string;
  /** Display label shown in the group container (without the "(auto)" suffix). */
  label: string;
  /** Member model ids — in the order they appear in `DagPayload.nodes`. */
  members: string[];
  /** `true` when the cluster was derived from a kind prefix. */
  auto: boolean;
  /** Border/fill/label colours for the cluster container. */
  palette: { border: string; fill: string; label: string };
}

const AUTO_PREFIX = "auto:";

/** Stable id used in React Flow / collapsed-state maps for an auto group. */
export function autoGroupId(kind: ModelKind): string {
  return `${AUTO_PREFIX}${kind}`;
}

/**
 * Compute the **effective group** of every model in the DAG:
 *
 * - Models with an explicit `group` keep it (real group).
 * - Models without one are bucketed under `auto:${kind}`.
 *
 * Returns `(nodeId → groupId)` for fast lookups during edge collapsing,
 * and the deduplicated, ordered list of effective groups (real first in
 * declaration order, then auto-groups in kind-order so the visual stack
 * stays stable across renders).
 */
export function deriveEffectiveGroups(dag: DagPayload): {
  groups: EffectiveGroup[];
  groupOf: Map<string, string>;
} {
  const groupOf = new Map<string, string>();

  // Real groups first, in the order declared by the API.
  const realGroups = new Map<string, EffectiveGroup>();
  for (let i = 0; i < dag.groups.length; i++) {
    const g = dag.groups[i];
    const palette = paletteForGroup(i);
    realGroups.set(g.id, {
      id: g.id,
      label: g.id,
      members: [...g.members],
      auto: false,
      palette: { border: palette.border, fill: palette.fill, label: palette.label },
    });
    for (const m of g.members) groupOf.set(m, g.id);
  }

  // Auto-groups: anything not already in a real group, bucketed by kind.
  const autoBuckets = new Map<ModelKind, DagNode[]>();
  for (const n of dag.nodes) {
    if (groupOf.has(n.id)) continue;
    const bucket = autoBuckets.get(n.kind);
    if (bucket) bucket.push(n);
    else autoBuckets.set(n.kind, [n]);
  }
  const autoGroups: EffectiveGroup[] = [];
  // Sort kinds in a stable order matching the FilterPanel's kind list.
  const kindOrder: ModelKind[] = [
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
  for (const kind of kindOrder) {
    const bucket = autoBuckets.get(kind);
    if (!bucket || bucket.length === 0) continue;
    const id = autoGroupId(kind);
    const palette = paletteFor(kind);
    autoGroups.push({
      id,
      label: kind,
      members: bucket.map((n) => n.id),
      auto: true,
      // Auto groups borrow the kind's hue for the border and a near-white
      // fill so they read as "structural background" rather than as a
      // deliberately coloured cluster.
      palette: { border: palette.border, fill: "#fafbfc", label: palette.border },
    });
    for (const n of bucket) groupOf.set(n.id, id);
  }

  return {
    groups: [...realGroups.values(), ...autoGroups],
    groupOf,
  };
}
