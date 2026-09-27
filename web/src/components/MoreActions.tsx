import { useEffect, useRef, type ReactNode } from "react";
import Icon from "./Icon";

export default function MoreActions({ children, label = "Więcej" }: { children: ReactNode; label?: string }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const outside = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) ref.current.open = false; };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && ref.current?.open) {
      ref.current.open = false; ref.current.querySelector("summary")?.focus();
    } };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, []);
  return <details className="more-actions no-print" ref={ref}>
    <summary className="btn"><Icon name="more" />{label}</summary>
    <div className="more-panel">{children}</div>
  </details>;
}
