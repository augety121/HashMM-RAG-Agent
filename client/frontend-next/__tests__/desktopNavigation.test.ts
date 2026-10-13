import { describe, expect, it } from "vitest";
import { desktopParentView } from "@/lib/desktopNavigation";

describe("desktop workspace navigation", () => {
  it("returns a child page to its immediate workspace", () => {
    expect(desktopParentView("advanced")).toBe("hub-operations");
    expect(desktopParentView("collab")).toBe("hub-operations");
    expect(desktopParentView("docstudio")).toBe("hub-knowledge");
    // 协作已成为与今天、项目、资料和画布并列的稳定一级入口，
    // 退出时直接回到 Chat，而不是跳回旧的内部 Agent 分类页。
    expect(desktopParentView("agents")).toBeNull();
  });

  it("leaves top-level hubs and unknown views at the conversation root", () => {
    expect(desktopParentView("hub-operations")).toBeNull();
    expect(desktopParentView("browser")).toBeNull();
    expect(desktopParentView(null)).toBeNull();
  });
});
