import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");
const sidebar = fs.readFileSync(path.join(root, "components", "Sidebar.tsx"), "utf8");
const admin = fs.readFileSync(path.join(root, "components", "AdminPanel.tsx"), "utf8");

describe("V1700 stable navigation contract", () => {
  it("keeps project conversations out of Recent", () => {
    expect(sidebar).toContain("const unassignedRecent = filtered.filter");
    expect(sidebar).toContain("!String(item.project_id || \"\").trim()")
  });

  it("reserves a trailing action slot instead of replacing row geometry", () => {
    expect(sidebar).toContain('w-[82px] flex-shrink-0');
    expect(sidebar).toContain("group-hover:opacity-0");
    expect(sidebar).toContain("group-hover:opacity-100");
    expect(sidebar).not.toContain("transition-all");
  });

  it("uses readable section labels and no decorative admin shield block", () => {
    expect(sidebar).toContain('className="text-[12px] font-semibold hover:opacity-75"');
    expect(sidebar).toContain('text-[12px] font-semibold');
    expect(admin).not.toContain('className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"');
  });
});
