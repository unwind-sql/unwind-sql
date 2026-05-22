import { Handle, Position, type NodeProps } from "@xyflow/react";

export interface GroupCollapsedNodeData extends Record<string, unknown> {
  /** Effective-group id (real name or `auto:${kind}`). */
  groupId: string;
  /** Display label (without the "(auto)" suffix, which we render separately). */
  label: string;
  /** Members currently visible (after filter). */
  memberCount: number;
  /** Total members declared in the group. */
  totalCount: number;
  /** Synthesised cluster (from kind prefix)? */
  auto: boolean;
  border: string;
  fill: string;
  labelColor: string;
}

/**
 * Single-node representative drawn in place of an entire group when the
 * group is collapsed. Clicking it (handled by Graph's onNodeClick) expands
 * the group back to its member nodes.
 */
export function GroupCollapsedNode({ data, selected }: NodeProps) {
  const d = data as GroupCollapsedNodeData;
  const filterHidden = d.memberCount < d.totalCount;
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        background: d.fill,
        border: selected
          ? `2.5px solid #1a3a99`
          : d.auto
            ? `1.5px dashed ${d.border}`
            : `2px solid ${d.border}`,
        borderRadius: 8,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        padding: "8px 14px",
        boxShadow: selected
          ? "0 0 0 4px rgba(76, 110, 245, 0.35)"
          : "0 1px 3px rgba(0, 0, 0, 0.06)",
        cursor: "pointer",
        userSelect: "none",
        transition: "box-shadow 120ms ease, border-color 120ms ease",
      }}
      title={`Click to expand · ${d.memberCount} model${d.memberCount === 1 ? "" : "s"}${
        filterHidden ? ` (${d.totalCount - d.memberCount} hidden by filter)` : ""
      }`}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 700,
          color: d.labelColor,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          fontStyle: d.auto ? "italic" : "normal",
          display: "flex",
          alignItems: "baseline",
          gap: 6,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
          }}
        >
          {d.label}
        </span>
        {d.auto ? (
          <span
            style={{
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
      <div
        style={{
          fontSize: 11,
          color: "#6b7280",
          marginTop: 2,
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <span aria-hidden>▸</span>
        <span>
          {d.memberCount} model{d.memberCount === 1 ? "" : "s"}
          {filterHidden ? (
            <span style={{ color: "#94a3b8" }}>
              {" "}
              · {d.totalCount - d.memberCount} hidden
            </span>
          ) : null}
        </span>
      </div>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}
