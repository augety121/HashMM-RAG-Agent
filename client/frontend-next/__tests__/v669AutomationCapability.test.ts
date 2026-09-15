import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

const root = process.cwd();
const read = (file: string) => fs.readFileSync(path.join(root, file), "utf8");

describe("V669 automation is a truthful Chat capability", () => {
  test("plugin center exposes the real automation workspace", () => {
    const view = read("components/desktop/PluginCenterView.tsx");
    expect(view).toContain("automations:");
    expect(view).toContain('view: "scheduled"');
    expect(view).toContain("自动任务");
  });

  test("routine UI supports timezone, delivery and owner-editable schedules", () => {
    const view = read("components/desktop/ScheduledView.tsx");
    const api = read("lib/api.ts");
    expect(view).toContain("Intl.DateTimeFormat().resolvedOptions().timeZone");
    expect(view).toContain("result_destination");
    expect(view).toContain("updateUserRoutine");
    expect(view).toContain("不会无人确认地提交、发送、删除或付款");
    expect(api).toContain('result_destination: "conversation" | "work_ledger"');
    expect(api).toContain("updateUserRoutine");
    expect(api).toContain('method: "PATCH"');
  });
});
