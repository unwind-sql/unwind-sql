import type { NodeProps } from "@xyflow/react";

export interface GroupNodeData extends Record<string, unknown> {
  label: string;
  border: string;
  fill: string;
  labelColor: string;
  /**
   * `true` when the cluster was synthesized client-side from kind prefixes
   * (members lack an explicit `@group:` directive). We draw a dashed border
   * and an italic / muted label so users understand the grouping is heuristic
   * — not a deliberate choice in their project.
   */
  auto?: boolean;
}

/**
 * Compound-node container that visually wraps every member of a `group`.
 * Draws a soft rounded rectangle with the group name in the top-left corner.
 * No handles : it's purely decorative and not selectable as a model.
 */
export function GroupNode({ data }: NodeProps) {
  const d = data as GroupNodeData;
  const auto = d.auto === true;
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        background: d.fill,
        border: auto ? `1.5px dashed ${d.border}` : `1.5px solid ${d.border}`,
        borderRadius: 8,
        position: "relative",
        pointerEvents: "none",
        opacity: auto ? 0.85 : 1,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: 8,
          left: 12,
          // The layout step (cf. layout.ts::GROUP_LABEL_RESERVED) leaves a
          // clear band at the top of every group, so this label is never
          // overlapped by the first member node.
          fontSize: 12,
          fontWeight: 700,
          color: d.labelColor,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          fontStyle: auto ? "italic" : "normal",
          pointerEvents: "none",
        }}
      >
        {d.label}
        {auto ? (
          <span
            style={{
              marginLeft: 6,
              fontSize: 9,
              fontWeight: 600,
              color: "#94a3b8",
              letterSpacing: "0.04em",
            }}
          >
            (auto)
          </span>
        ) : null}
      </div>
    </div>
  );
}
