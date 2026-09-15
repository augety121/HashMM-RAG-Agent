import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("desktop about information architecture", () => {
  const root = path.resolve(__dirname, "..");

  it("keeps one about surface inside settings", () => {
    const userMenu = fs.readFileSync(path.join(root, "components", "UserMenu.tsx"), "utf8");
    const settings = fs.readFileSync(path.join(root, "components", "SettingsModal.tsx"), "utf8");

    expect(userMenu).not.toContain("AboutModal");
    expect(userMenu).not.toContain("关于 HashMM");
    expect(settings).toContain('id: "about", label: "关于"');
    expect(settings).toContain("function AboutTab()");
    expect(settings).toContain("checkUpdate");
  });
});
