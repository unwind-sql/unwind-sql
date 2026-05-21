import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import { layoutDag } from "../src/graph/layout";

function n(id: string): Node {
  return {
    id,
    type: "model",
    position: { x: 0, y: 0 },
    data: { label: id },
  };
}

function e(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target };
}

describe("layoutDag", () => {
  it("returns the same number of nodes and edges, all positioned", () => {
    const nodes = [n("a"), n("b"), n("c")];
    const edges = [e("a", "b"), e("b", "c")];
    const out = layoutDag(nodes, edges, []);
    expect(out.nodes).toHaveLength(3);
    expect(out.edges).toEqual(edges);
    for (const node of out.nodes) {
      expect(node.position.x).toBeTypeOf("number");
      expect(node.position.y).toBeTypeOf("number");
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
    }
  });

  it("orders nodes left-to-right along the dependency chain", () => {
    const nodes = [n("c"), n("a"), n("b")];
    const edges = [e("a", "b"), e("b", "c")];
    const out = layoutDag(nodes, edges, [], { rankdir: "LR" });
    const xs = Object.fromEntries(out.nodes.map((node) => [node.id, node.position.x]));
    expect(xs.a).toBeLessThan(xs.b);
    expect(xs.b).toBeLessThan(xs.c);
  });

  it("attaches LR-friendly handle positions", () => {
    const out = layoutDag([n("a")], [], []);
    expect(out.nodes[0].sourcePosition).toBe("right");
    expect(out.nodes[0].targetPosition).toBe("left");
  });

  it("handles a single isolated node", () => {
    const out = layoutDag([n("a")], [], []);
    expect(out.nodes).toHaveLength(1);
    expect(out.edges).toHaveLength(0);
    expect(out.groups).toEqual([]);
  });

  it("returns cluster bounds and re-parents members in compound mode", () => {
    const nodes = [n("a"), n("b"), n("c")];
    const edges = [e("a", "b"), e("b", "c")];
    const out = layoutDag(nodes, edges, [
      { id: "g1", members: ["a", "b"] },
      { id: "g2", members: ["c"] },
    ]);
    expect(out.groups).toHaveLength(2);
    const byId = Object.fromEntries(out.nodes.map((n) => [n.id, n]));
    // Members reference their parent group and use relative coordinates.
    expect((byId.a as { parentId?: string }).parentId).toBe("group:g1");
    expect((byId.b as { parentId?: string }).parentId).toBe("group:g1");
    expect((byId.c as { parentId?: string }).parentId).toBe("group:g2");
    for (const gb of out.groups) {
      expect(gb.width).toBeGreaterThan(0);
      expect(gb.height).toBeGreaterThan(0);
    }
  });
});
