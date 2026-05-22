import { describe, expect, it } from "vitest";
import {
  autoGroupId,
  deriveEffectiveGroups,
} from "../src/graph/effectiveGroups";
import type { DagNode, DagPayload } from "../src/types";

function node(id: string, kind: DagNode["kind"], group: string | null = null): DagNode {
  return {
    id,
    kind,
    language: "sql",
    group,
    tags: [],
    row_count: null,
    materialized: "table",
    location: null,
    disabled: false,
    description: null,
  };
}

function payload(
  nodes: DagNode[],
  groups: Array<{ id: string; members: string[] }> = [],
): DagPayload {
  return { nodes, edges: [], groups };
}

describe("deriveEffectiveGroups", () => {
  it("keeps every declared real group and assigns its members", () => {
    const p = payload(
      [
        node("a", "raw", "src"),
        node("b", "int", "core"),
        node("c", "fct", "core"),
      ],
      [
        { id: "src", members: ["a"] },
        { id: "core", members: ["b", "c"] },
      ],
    );
    const { groups, groupOf } = deriveEffectiveGroups(p);
    expect(groups.map((g) => g.id)).toEqual(["src", "core"]);
    expect(groups.every((g) => !g.auto)).toBe(true);
    expect(groupOf.get("a")).toBe("src");
    expect(groupOf.get("b")).toBe("core");
    expect(groupOf.get("c")).toBe("core");
  });

  it("buckets orphan nodes by kind into auto-groups", () => {
    const p = payload([
      node("r1", "raw"),
      node("r2", "raw"),
      node("i1", "int"),
      node("f1", "fct", "outputs"),
    ], [{ id: "outputs", members: ["f1"] }]);
    const { groups, groupOf } = deriveEffectiveGroups(p);
    expect(groups.map((g) => g.id)).toEqual([
      "outputs",
      autoGroupId("raw"),
      autoGroupId("int"),
    ]);
    const auto = groups.filter((g) => g.auto);
    expect(auto.map((g) => g.label)).toEqual(["raw", "int"]);
    expect(groupOf.get("r1")).toBe(autoGroupId("raw"));
    expect(groupOf.get("r2")).toBe(autoGroupId("raw"));
    expect(groupOf.get("i1")).toBe(autoGroupId("int"));
    expect(groupOf.get("f1")).toBe("outputs");
  });

  it("returns no auto-groups when every node already has a real group", () => {
    const p = payload(
      [node("a", "raw", "src"), node("b", "fct", "out")],
      [
        { id: "src", members: ["a"] },
        { id: "out", members: ["b"] },
      ],
    );
    const { groups } = deriveEffectiveGroups(p);
    expect(groups.every((g) => !g.auto)).toBe(true);
    expect(groups.map((g) => g.id)).toEqual(["src", "out"]);
  });

  it("returns auto-groups in the canonical kind order", () => {
    // Declared in scrambled order in the payload; the result must follow
    // the raw → ref → stg → int → fct → ... canonical order.
    const p = payload([
      node("f", "fct"),
      node("i", "int"),
      node("r", "raw"),
      node("d", "dim"),
    ]);
    const { groups } = deriveEffectiveGroups(p);
    expect(groups.map((g) => g.id)).toEqual([
      autoGroupId("raw"),
      autoGroupId("int"),
      autoGroupId("fct"),
      autoGroupId("dim"),
    ]);
  });
});
