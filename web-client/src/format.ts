import type { CellValue } from "./types";

export function formatScalar(v: CellValue): string {
  if (v === null || v === undefined) return "NULL";
  return String(v);
}

export function isNumeric(v: CellValue): boolean {
  return typeof v === "number";
}
