import { useEffect, useRef } from "react";

interface Props {
  /**
   * Called every time the user drags. The argument is the new pixel width
   * the right panel should take. The caller is responsible for clamping
   * — common bounds are [240px .. window.innerWidth - 200px].
   */
  onResize: (newPanelWidth: number) => void;
}

const MIN_PANEL = 240;
const MIN_GRAPH = 200;

export function PanelResizer({ onResize }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let dragging = false;

    const onDown = (e: MouseEvent) => {
      dragging = true;
      el.classList.add("dragging");
      document.body.classList.add("resizing");
      e.preventDefault();
    };
    const onMove = (e: MouseEvent) => {
      if (!dragging) return;
      const next = Math.min(
        window.innerWidth - MIN_GRAPH,
        Math.max(MIN_PANEL, window.innerWidth - e.clientX),
      );
      onResize(next);
    };
    const onUp = () => {
      if (!dragging) return;
      dragging = false;
      el.classList.remove("dragging");
      document.body.classList.remove("resizing");
    };

    el.addEventListener("mousedown", onDown);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      el.removeEventListener("mousedown", onDown);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [onResize]);

  return <div ref={ref} className="panel-resizer" title="Drag to resize" />;
}
