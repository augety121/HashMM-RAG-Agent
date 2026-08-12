"use client";

import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Mount transient canvas UI outside composer overflow containers. Keeping the
 * target lookup here prevents callers from accidentally invoking createPortal
 * without its required DOM container and remains safe during server rendering.
 */
export default function CanvasMenuPortal({ children }: { children: ReactNode }) {
  const target = typeof document !== "undefined" ? document.body : null;
  return target ? createPortal(children, target) : null;
}
