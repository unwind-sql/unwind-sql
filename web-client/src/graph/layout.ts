import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";

// Approximate node footprint used by dagre to compute non-overlapping ranks.
// We *also* surface this on each Node so the MiniMap knows their bounds
// before React Flow has measured the actual DOM (otherwise minimap rects
// collapse to zero and nothing is drawn).
const NODE_WIDTH = 180;
const NODE_HEIGHT = 56;

/**
 * Return the explicit `width`/`height` carried by a Node (when a caller
 * pre-sizes it — e.g. a collapsed-group representative wider than a member
 * node) or the default footprint otherwise.
 */
function sizeOf(n: Node): { width: number; height: number } {
  const width = typeof n.width === "number" ? n.width : NODE_WIDTH;
  const height = typeof n.height === "number" ? n.height : NODE_HEIGHT;
  return { width, height };
}

// dagre doesn't actually honor `paddingTop` on cluster nodes — it ignores the
// option silently and packs members flush against the top edge, where they
// overlap the group's title label. We work around it by *extending* every
// group's bounding box upward after layout (cf. GROUP_LABEL_RESERVED below),
// which gives the label a clear band above all members. The two values here
// are still passed to dagre so that, if it ever starts honoring them, the
// extra padding shows up as breathing room rather than as a layout shift.
const GROUP_PAD = 10;

// Pixels of clear space reserved above every group's first member for the
// uppercase title label. Must be at least the rendered label height + a few
// pixels of breathing room (cf. GroupNode.tsx).
const GROUP_LABEL_RESERVED = 28;

interface LayoutOpts {
  rankdir?: "LR" | "TB";
  ranksep?: number;
  nodesep?: number;
}

interface GroupSpec {
  id: string;
  members: string[];
}

export interface GroupBounds {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LayoutResult {
  nodes: Node[];
  edges: Edge[];
  groups: GroupBounds[];
}

/**
 * Compute (x, y) positions for every React Flow node using dagre.
 *
 * When `groups` is non-empty, dagre runs in compound mode : each group is a
 * cluster, members of the same cluster are kept spatially close together,
 * and the cluster bounding box is returned in `groups`. This avoids the
 * "huge group spanning the whole DAG" issue that arises when a source-rank
 * member of group A and a sink-rank member of group A end up at opposite
 * ends of the layout.
 */
export function layoutDag(
  nodes: Node[],
  edges: Edge[],
  groups: GroupSpec[] = [],
  opts: LayoutOpts = {},
): LayoutResult {
  const compound = groups.length > 0;
  const g = new dagre.graphlib.Graph({ compound });
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: opts.rankdir ?? "LR",
    ranksep: opts.ranksep ?? 50,
    // In compound mode we add `GROUP_LABEL_RESERVED` worth of headroom at
    // the top of every cluster *after* layout (cf. groupBounds below). For
    // that headroom to fit cleanly between two vertically-stacked clusters
    // — instead of overlapping the cluster above — we need dagre to leave
    // at least that much extra gap between rank-aligned members.
    nodesep:
      opts.nodesep ?? (compound ? GROUP_LABEL_RESERVED + 14 : 14),
    edgesep: 8,
    marginx: 12,
    marginy: 12,
  });

  // Cluster nodes : declared first, with no width/height so dagre sizes them
  // around their children. `padding*` values are passed for future
  // compatibility — current dagre versions ignore them, so the post-layout
  // pass below also reserves space for the title.
  const groupOf = new Map<string, string>();
  for (const grp of groups) {
    g.setNode(`group:${grp.id}`, {
      label: grp.id,
      clusterLabelPos: "top",
      paddingTop: GROUP_LABEL_RESERVED,
      paddingBottom: GROUP_PAD,
      paddingLeft: GROUP_PAD,
      paddingRight: GROUP_PAD,
    });
    for (const m of grp.members) groupOf.set(m, grp.id);
  }

  for (const n of nodes) {
    g.setNode(n.id, sizeOf(n));
    const parent = groupOf.get(n.id);
    if (parent !== undefined) g.setParent(n.id, `group:${parent}`);
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }

  dagre.layout(g);

  // Extend each cluster's bounding box upward to make room for the title
  // label, then capture the result for later use when re-emitting member
  // positions in cluster-relative coordinates.
  const extendedBounds: GroupBounds[] = groups.map((grp) => {
    const node = g.node(`group:${grp.id}`);
    return {
      id: grp.id,
      x: node.x - node.width / 2,
      y: node.y - node.height / 2 - GROUP_LABEL_RESERVED,
      width: node.width,
      height: node.height + GROUP_LABEL_RESERVED,
    };
  });
  const originalExtendedById = new Map(
    extendedBounds.map((gb) => [gb.id, { ...gb }]),
  );

  // The upward extension can push a cluster into the one directly above it
  // when dagre packed them tightly. Walk the clusters top-to-bottom and
  // shove any geometric overlap downward — but ONLY in the final bounds we
  // emit. Member coordinates stay relative to the pre-shift bounds (cf.
  // `originalExtendedById` below), so React Flow's parent translation
  // carries each cluster's members with it as the cluster moves.
  const groupBounds = resolveVerticalOverlaps(extendedBounds);

  // Member positions are emitted RELATIVE to their group (when they belong
  // to one). React Flow translates child coordinates by the parent's
  // position, so anchoring each child to the *pre-shift* extended bounds
  // (rather than the post-overlap-resolution bounds) makes the children
  // ride with their parent as the parent slides down.
  const positioned: Node[] = nodes.map((n) => {
    const pos = g.node(n.id);
    const parent = groupOf.get(n.id);
    const parentBounds = parent ? originalExtendedById.get(parent) : undefined;
    const { width, height } = sizeOf(n);
    const absX = pos.x - width / 2;
    const absY = pos.y - height / 2;
    return {
      ...n,
      position: parentBounds
        ? { x: absX - parentBounds.x, y: absY - parentBounds.y }
        : { x: absX, y: absY },
      width,
      height,
      ...(parent ? { parentId: `group:${parent}`, extent: "parent" } : {}),
      sourcePosition: "right",
      targetPosition: "left",
    } as Node;
  });

  return { nodes: positioned, edges, groups: groupBounds };
}

/** Minimum visual gap to leave between two clusters after overlap resolution. */
const OVERLAP_RESOLUTION_GAP = 12;

/**
 * Push each cluster down just enough that no two cluster bounding boxes
 * overlap. Bounding-box overlap means both X and Y ranges intersect. We
 * sort top-to-bottom and shift later clusters down — never up — so a single
 * left-to-right scan resolves chains of cascading collisions deterministically.
 */
function resolveVerticalOverlaps(input: GroupBounds[]): GroupBounds[] {
  const sorted = [...input].sort((a, b) => a.y - b.y);
  for (let i = 1; i < sorted.length; i++) {
    const cur = sorted[i];
    for (let j = 0; j < i; j++) {
      const prev = sorted[j];
      const xOverlap =
        cur.x < prev.x + prev.width && cur.x + cur.width > prev.x;
      if (!xOverlap) continue;
      const requiredTop = prev.y + prev.height + OVERLAP_RESOLUTION_GAP;
      if (cur.y < requiredTop) {
        cur.y = requiredTop;
      }
    }
  }
  // Preserve the caller's original ordering so consumers can index by it.
  const byId = new Map(sorted.map((gb) => [gb.id, gb]));
  return input.map((gb) => byId.get(gb.id) as GroupBounds);
}
