import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const repo = path.resolve(__dirname, "..", "..");
const frontend = path.join(repo, "frontend-next");
const app = path.resolve(repo, "..", "app");

describe("V550 unified user workspace", () => {
  it("uses one owner-scoped workspace API instead of a second executor", () => {
    const api = fs.readFileSync(path.join(frontend, "lib", "api.ts"), "utf8");
    const shell = fs.readFileSync(
      path.join(frontend, "components", "desktop", "WorkspaceShellView.tsx"),
      "utf8",
    );
    expect(api).toContain('schema: "hashmm.workspace.v2"');
    expect(api).toContain("/api/v2/workspaces/");
    expect(api).toContain("If-None-Match");
    expect(api).toContain("workCanvasCachePut");
    expect(api).toContain("completion_requires_evidence");
    expect(shell).toContain('section: WorkspaceSection');
    expect(shell).toContain("needs_user");
    expect(shell).not.toContain("演示数据");
  });

  it("keeps primary navigation about user destinations", () => {
    const sidebar = fs.readFileSync(path.join(frontend, "components", "Sidebar.tsx"), "utf8");
    const panel = fs.readFileSync(path.join(frontend, "components", "DesktopPanel.tsx"), "utf8");
    expect(sidebar).toContain('label: "今天"');
    expect(sidebar).toContain('const unassignedRecent = filtered.filter(item => !String(item.project_id || "").trim())');
    expect(sidebar).toContain("const recentItems = unassignedRecent.slice(0, recentLimit)");
    expect(sidebar).not.toContain("ungroupedRecent");
    expect(sidebar).toContain("项目");
    expect(sidebar).toContain('label: "资料库"');
    expect(sidebar).toContain('label: "设备接力"');
    expect(panel).toContain('<WorkspaceShellView section="today" />');
    expect(panel).toContain('<WorkspaceShellView section="projects" />');
    expect(panel).toContain('<WorkspaceShellView section="library" />');
  });

  it("makes App a mobile control surface over the same V2 runtime", () => {
    const main = fs.readFileSync(
      path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "MainScaffold.kt"),
      "utf8",
    );
    const repository = fs.readFileSync(
      path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "data", "remote", "WorkRuntimeRepository.kt"),
      "utf8",
    );
    expect(main).toContain('TabItem("今天"');
    expect(repository).toContain("/api/v2/workspaces/personal/snapshot");
    expect(repository).toContain("ownerIsolation");
    expect(repository).toContain("modelProseIsExecutionEvidence");
    expect(repository).toContain("WORKSPACE_ETAG_KEY");
    expect(repository).toContain("A 404 is the sole compatibility fallback");
  });
});
