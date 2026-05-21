import type { ModelKind } from "../types";

interface Palette {
  border: string;
  fill: string;
  text: string;
}

// Each "kind" prefix gets its own border colour. Fill stays neutral so the
// node label remains readable on the dotted background. `external` is the
// "output sink" kind — distinctive warm fill so exports jump out in the DAG.
const KIND_PALETTE: Record<ModelKind, Palette> = {
  raw: { border: "#888888", fill: "#ffffff", text: "#222" },
  ref: { border: "#aa6c39", fill: "#ffffff", text: "#222" },
  stg: { border: "#7c3aed", fill: "#ffffff", text: "#222" },
  int: { border: "#4c6ef5", fill: "#ffffff", text: "#222" },
  fct: { border: "#2b8a3e", fill: "#ffffff", text: "#222" },
  dim: { border: "#0891b2", fill: "#ffffff", text: "#222" },
  mart: { border: "#d6597a", fill: "#ffffff", text: "#222" },
  external: { border: "#c2410c", fill: "#fff7ed", text: "#7c2d12" },
  model: { border: "#4c6ef5", fill: "#ffffff", text: "#222" },
};

export function paletteFor(kind: ModelKind): Palette {
  return KIND_PALETTE[kind] ?? KIND_PALETTE.model;
}
