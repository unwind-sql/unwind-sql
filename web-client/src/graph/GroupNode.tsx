import type { NodeProps } from "@xyflow/react";

export interface GroupNodeData extends Record<string, unknown> {
  label: string;
  border: string;
  fill: string;
  labelColor: string;
}

/**
 * Compound-node container that visually wraps every member of a `group`.
 * Draws a soft rounded rectangle with the group name in the top-left corner.
 * No handles : it's purely decorative and not selectable as a model.
 */
export function GroupNode({ data }: NodeProps) {
  const d = data as GroupNodeData;
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        background: d.fill,
        border: `1.5px solid ${d.border}`,
        borderRadius: 8,
        position: "relative",
        pointerEvents: "none",
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
          pointerEvents: "none",
        }}
      >
        {d.label}
      </div>
    </div>
  );
}
