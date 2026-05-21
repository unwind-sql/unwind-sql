// Palette assigned to a group by index of first appearance. Mirrors the
// previous Cytoscape `GROUP_PALETTE` so groups keep recognisable colours.

interface GroupPalette {
  border: string;
  fill: string;
  label: string;
}

const PALETTE: GroupPalette[] = [
  { fill: "#eef3ff", border: "#4c6ef5", label: "#4c6ef5" },
  { fill: "#fef6ee", border: "#e8a657", label: "#a55b13" },
  { fill: "#eef9f0", border: "#3aa766", label: "#1f6f43" },
  { fill: "#f7eefb", border: "#9b51e0", label: "#6e2bb0" },
  { fill: "#fdeef0", border: "#d6597a", label: "#9c2c4f" },
  { fill: "#eef9fa", border: "#3da5b3", label: "#1f6e7a" },
];

export function paletteForGroup(index: number): GroupPalette {
  return PALETTE[index % PALETTE.length];
}
