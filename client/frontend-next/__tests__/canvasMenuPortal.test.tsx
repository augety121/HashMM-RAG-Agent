import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import CanvasMenuPortal from "@/components/CanvasMenuPortal";

describe("CanvasMenuPortal", () => {
  let root: Root | null = null;

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    root = null;
    document.body.innerHTML = "";
  });

  it("mounts canvas menu content into document.body", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);

    await act(async () => {
      root?.render(
        <CanvasMenuPortal>
          <div data-testid="canvas-menu">canvas</div>
        </CanvasMenuPortal>,
      );
    });

    expect(document.body.querySelector('[data-testid="canvas-menu"]')?.textContent).toBe("canvas");
  });
});
