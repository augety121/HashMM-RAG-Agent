import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const repo = path.resolve(__dirname, "..", "..");
const frontend = path.join(repo, "frontend-next");
const app = path.resolve(repo, "..", "app");

describe("V395 user-facing work experience", () => {
  it("projects internal runs into user work and an action inbox", () => {
    const runtime = fs.readFileSync(path.join(repo, "hashmm", "agent", "work_runtime.py"), "utf8");
    expect(runtime).toContain('PRESENTATION_SCHEMA = "hashmm.work-presentation.v1"');
    expect(runtime).toContain('ACTION_INBOX_SCHEMA = "hashmm.action-inbox.v1"');
    expect(runtime).toContain('"needs_user"');
    expect(runtime).toContain('"review_delivery"');
    expect(runtime).toContain('"action_inbox": action_inbox(inbox_items)');
    expect(runtime).toContain("Pending decisions must remain visible");
    expect(runtime).toContain("never invent a percentage");
  });

  it("uses user destinations and progressively discloses execution options", () => {
    const sidebar = fs.readFileSync(path.join(frontend, "components", "Sidebar.tsx"), "utf8");
    const chat = fs.readFileSync(path.join(frontend, "components", "ChatArea.tsx"), "utf8");
    const panel = fs.readFileSync(path.join(frontend, "components", "DesktopPanel.tsx"), "utf8");
    expect(sidebar).toContain('label: "今天"');
    expect(sidebar).toContain('const unassignedRecent = filtered.filter(item => !String(item.project_id || "").trim())');
    expect(sidebar).toContain("const recentItems = unassignedRecent.slice(0, recentLimit)");
    expect(sidebar).not.toContain("ungroupedRecent");
    expect(sidebar).toContain("项目");
    expect(sidebar).toContain('label: "资料库"');
    expect(sidebar).toContain('label: "设备接力"');
    expect(sidebar).not.toContain('label: "复杂任务"');
    expect(sidebar).not.toContain('label: "任务与进度"');
    expect(chat).toContain("onClick={cycleRetrieval}");
    expect(chat).toContain('retrievalMode === "auto" ? "自动"');
    expect(chat).toContain("<DocFilterChips");
    expect(panel).toContain('<WorkspaceShellView section="today" />');
    expect(panel).toContain('<WorkspaceShellView section="results" />');
  });

  it("makes mobile an action inbox and hides runtime cursors from the work home", () => {
    const main = fs.readFileSync(path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "MainScaffold.kt"), "utf8");
    const work = fs.readFileSync(path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "workbench", "WorkbenchHubScreen.kt"), "utf8");
    const activity = fs.readFileSync(path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "activity", "ActivityScreen.kt"), "utf8");
    const activityVm = fs.readFileSync(path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "activity", "ActivityViewModel.kt"), "utf8");
    expect(main).toContain('TabItem("今天"');
    expect(main).toContain('TabItem("工作"');
    expect(activity).toContain('SectionHeader("需要你处理"');
    expect(activity).toContain("WorkRow");
    expect(work).not.toContain('"当前能力",');
    expect(work).not.toContain('· r${run.revision} · e${run.eventCursor}');
    expect(work).toContain("run.presentation.currentStep");
    expect(activityVm).toContain("workRuntimeRepository.fetch(force)");
    expect(activityVm).not.toContain("refreshAllInternal");
    expect(activity).toContain("work.error");
  });
});
