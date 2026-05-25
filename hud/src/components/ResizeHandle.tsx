import { useState } from "react";

interface Props {
  onResize: (deltaX: number) => void;
}

/**
 * 4-px-wide hairline drag handle for horizontal panel resizing.
 * Cursor turns to col-resize on hover; the handle brightens during drag.
 */
export default function ResizeHandle({ onResize }: Props) {
  const [dragging, setDragging] = useState(false);

  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setDragging(true);
    let lastX = e.clientX;

    const onMove = (mv: MouseEvent) => {
      const dx = mv.clientX - lastX;
      lastX = mv.clientX;
      if (dx !== 0) onResize(dx);
    };

    const onUp = () => {
      setDragging(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div
      onMouseDown={onMouseDown}
      className={
        "w-1 shrink-0 cursor-col-resize transition-colors " +
        (dragging ? "bg-accent/60" : "bg-line hover:bg-accent/30")
      }
      title="Drag to resize"
    />
  );
}
