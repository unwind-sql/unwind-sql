import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ModelKind, ModelLanguage } from "../types";
import { paletteFor } from "./kinds";

export type NodeRelation = "upstream" | "downstream" | null;

export type ImpactRole = "source" | "projection" | "structural" | "opaque";

export interface ModelNodeData extends Record<string, unknown> {
  label: string;
  kind: ModelKind;
  language: ModelLanguage;
  group: string | null;
  tags: string[];
  rowCount: number | null;
  relation?: NodeRelation;
  impactRole?: ImpactRole | null;
  disabled?: boolean;
  description?: string | null;
}

const LANGUAGE_BADGE: Record<ModelLanguage, string> = {
  sql: "SQL",
  python: "PY",
};

// Relation halos use the same hues as the animated edges (cf. Graph.tsx) so
// the eye can immediately tie a tinted node to the edge that connects it
// to the focused node.
const UPSTREAM_COLOR = "#0ea5e9";
const DOWNSTREAM_COLOR = "#f59e0b";

// Impact halos use colour to communicate the kind of impact at a glance:
// red for value-flow, amber for structural use, purple for opaque sinks,
// blue for the analysis anchor.
const IMPACT_PALETTE: Record<
  ImpactRole,
  { border: string; haloOuter: string; haloInner: string; label: string }
> = {
  source: {
    border: "#1d4ed8",
    haloOuter: "rgba(29, 78, 216, 0.30)",
    haloInner: "rgba(29, 78, 216, 0.25)",
    label: "source",
  },
  projection: {
    border: "#dc2626",
    haloOuter: "rgba(220, 38, 38, 0.30)",
    haloInner: "rgba(220, 38, 38, 0.25)",
    label: "value flow",
  },
  structural: {
    border: "#d97706",
    haloOuter: "rgba(217, 119, 6, 0.30)",
    haloInner: "rgba(217, 119, 6, 0.25)",
    label: "structural",
  },
  opaque: {
    border: "#a855f7",
    haloOuter: "rgba(168, 85, 247, 0.30)",
    haloInner: "rgba(168, 85, 247, 0.25)",
    label: "opaque",
  },
};

function relationStyle(relation: NodeRelation): {
  border: string;
  shadow: string;
} | null {
  if (relation === "upstream") {
    return {
      border: `2px solid ${UPSTREAM_COLOR}`,
      shadow: "0 0 0 3px rgba(14, 165, 233, 0.30), 0 0 12px 2px rgba(14, 165, 233, 0.25)",
    };
  }
  if (relation === "downstream") {
    return {
      border: `2px solid ${DOWNSTREAM_COLOR}`,
      shadow: "0 0 0 3px rgba(245, 158, 11, 0.30), 0 0 12px 2px rgba(245, 158, 11, 0.25)",
    };
  }
  return null;
}

function impactStyle(role: ImpactRole | null | undefined): {
  border: string;
  shadow: string;
  badge: { color: string; text: string };
} | null {
  if (!role) return null;
  const p = IMPACT_PALETTE[role];
  return {
    border: `2px solid ${p.border}`,
    shadow: `0 0 0 3px ${p.haloOuter}, 0 0 12px 2px ${p.haloInner}`,
    badge: { color: p.border, text: p.label },
  };
}

/**
 * Custom node : kind-coloured left bar + label + meta line (kind/group)
 * + footer with row count and tags. Plenty of room to add exec status etc.
 */
export function ModelNode({ data, selected }: NodeProps) {
  const d = data as ModelNodeData;
  const palette = paletteFor(d.kind);
  // Priority: explicit selection > impact role > relation halo > default.
  const impact = !selected ? impactStyle(d.impactRole ?? null) : null;
  const relStyle = !selected && !impact ? relationStyle(d.relation ?? null) : null;
  const disabled = d.disabled === true;
  const tooltip = buildTooltip(d, disabled);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "stretch",
        background: disabled ? "#f1f5f9" : palette.fill,
        color: disabled ? "#94a3b8" : palette.text,
        border: selected
          ? `3px solid #1a3a99`
          : disabled
            ? "1.5px dashed #94a3b8"
            : (impact?.border ?? relStyle?.border ?? `1.5px solid ${palette.border}`),
        borderRadius: 6,
        boxShadow: selected
          ? "0 0 0 4px rgba(76, 110, 245, 0.45), 0 0 18px 4px rgba(76, 110, 245, 0.35)"
          : disabled
            ? "none"
            : (impact?.shadow ?? relStyle?.shadow ?? "0 1px 2px rgba(0, 0, 0, 0.04)"),
        fontSize: 12,
        width: "100%",
        height: "100%",
        overflow: "hidden",
        opacity: disabled ? 0.7 : 1,
        transition: "box-shadow 120ms ease, border-color 120ms ease",
      }}
      title={tooltip}
    >
      <div
        style={{
          width: 4,
          background: disabled ? "#cbd5e1" : palette.border,
          flex: "0 0 auto",
        }}
      />
      <div
        style={{
          padding: "6px 10px",
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            minWidth: 0,
          }}
        >
          <div
            style={{
              fontWeight: 600,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              flex: 1,
              minWidth: 0,
              textDecoration: disabled ? "line-through" : "none",
            }}
          >
            {d.label}
          </div>
          {disabled ? (
            <span
              title="disabled (bypassed)"
              style={{
                flex: "0 0 auto",
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: "0.04em",
                color: "#64748b",
                background: "#ffffff",
                border: "1px dashed #94a3b8",
                borderRadius: 3,
                padding: "0 4px",
                lineHeight: "14px",
              }}
            >
              ⏸ MUTE
            </span>
          ) : null}
          <span
            title={d.language === "python" ? "Python model" : "SQL model"}
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: "0.04em",
              color: palette.border,
              background: "#ffffff",
              border: `1px solid ${palette.border}`,
              borderRadius: 3,
              padding: "0 4px",
              lineHeight: "14px",
            }}
          >
            {LANGUAGE_BADGE[d.language]}
          </span>
        </div>
        <div
          style={{
            fontSize: 10,
            color: palette.border,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {d.kind}
          {d.group ? ` · ${d.group}` : ""}
          {impact ? (
            <span
              style={{
                marginLeft: 6,
                color: impact.badge.color,
                fontWeight: 700,
              }}
            >
              · {impact.badge.text}
            </span>
          ) : null}
        </div>
        {(d.rowCount !== null || d.tags.length > 0) && (
          <div
            style={{
              fontSize: 10,
              color: "#6b7280",
              display: "flex",
              alignItems: "center",
              gap: 6,
              marginTop: 1,
            }}
          >
            {d.rowCount !== null && (
              <span style={{ fontFamily: "ui-monospace, SF Mono, Menlo, monospace" }}>
                {formatCount(d.rowCount)}
              </span>
            )}
            {d.tags.map((t) => (
              <span
                key={t}
                style={{
                  background: "#f1f5f9",
                  border: "1px solid #e2e8f0",
                  borderRadius: 999,
                  padding: "0 6px",
                  fontSize: 9,
                }}
              >
                {t}
              </span>
            ))}
          </div>
        )}
      </div>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

/**
 * Build the native HTML `title` tooltip for a node: shows the description
 * (when present), tags, and disabled state. Falls back to the existing
 * "disabled" hint when nothing else is informative.
 */
function buildTooltip(d: ModelNodeData, disabled: boolean): string | undefined {
  const lines: string[] = [];
  if (d.description) lines.push(d.description);
  if (d.tags.length > 0) lines.push(`tags: ${d.tags.join(", ")}`);
  if (disabled) lines.push("disabled (bypassed)");
  return lines.length > 0 ? lines.join("\n") : undefined;
}

function formatCount(n: number): string {
  if (n < 1000) return `${n} rows`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K rows`;
  return `${(n / 1_000_000).toFixed(1)}M rows`;
}
