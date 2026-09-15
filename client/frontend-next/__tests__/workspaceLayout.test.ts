import { describe, expect, it } from "vitest";
import {
  CHAT_MIN_WIDTH,
  COMPOSER_MAX_HEIGHT,
  COMPOSER_MIN_HEIGHT,
  SIDEBAR_EXPANDED_WIDTH,
  clampInspectorWidth,
  composerTextareaHeight,
} from "../lib/workspaceLayout";

describe("desktop Chat and inspector layout", () => {
  it("never lets an expanded inspector squeeze Chat below its usable width", () => {
    const viewport = 1104;
    const width = clampInspectorWidth(900, viewport, true);
    expect(width).toBe(401);
    expect(viewport - SIDEBAR_EXPANDED_WIDTH - width - 3).toBe(CHAT_MIN_WIDTH);
  });

  it("keeps a saved width when the window has enough room", () => {
    expect(clampInspectorWidth(560, 1920, true)).toBe(560);
    expect(clampInspectorWidth(1200, 1920, false)).toBe(900);
  });

  it("keeps the empty composer compact and clamps long input", () => {
    expect(composerTextareaHeight("", 180)).toBe(COMPOSER_MIN_HEIGHT);
    expect(composerTextareaHeight("hello", 12)).toBe(COMPOSER_MIN_HEIGHT);
    expect(composerTextareaHeight("long\ntext", 260)).toBe(COMPOSER_MAX_HEIGHT);
  });
});
