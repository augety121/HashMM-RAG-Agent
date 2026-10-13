import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

describe("V580 user-facing Chat capabilities are connected", () => {
  const frontend = process.cwd();
  const repo = path.resolve(frontend, "..");

  test("plugins are discoverable and the selected set reaches the Chat API", () => {
    const sidebar = fs.readFileSync(path.join(frontend, "components", "Sidebar.tsx"), "utf8");
    const panel = fs.readFileSync(path.join(frontend, "components", "DesktopPanel.tsx"), "utf8");
    const chat = fs.readFileSync(path.join(frontend, "components", "ChatArea.tsx"), "utf8");
    const api = fs.readFileSync(path.join(frontend, "lib", "api.ts"), "utf8");
    expect(sidebar).toContain('v: "plugins"');
    expect(panel).toContain("<PluginCenterView />");
    expect(chat).toContain("readSelectedPluginIds()");
    expect(api).toContain("plugin_ids: pluginIds");
  });

  test("skills are in user settings and use authenticated personal routes", () => {
    const settings = fs.readFileSync(path.join(frontend, "components", "SettingsModal.tsx"), "utf8");
    const skills = fs.readFileSync(path.join(frontend, "components", "settings", "UserSkillsSettings.tsx"), "utf8");
    const route = fs.readFileSync(path.join(repo, "hashmm", "api", "routes", "skill_packs.py"), "utf8");
    expect(settings).toContain("skills: <UserSkillsSettings />");
    for (const source of ["压缩包", "网站", "GitHub", "从 Codex", "从 Claude Code"]) {
      expect(skills).toContain(source);
    }
    expect(route).toContain('router.post("/mine/upload")');
    expect(route).toContain("get_user_skill_pack_manager(owner)");
  });

  test("document scope stays compact and reads the current user's library", () => {
    const filter = fs.readFileSync(path.join(frontend, "components", "DocFilterChips.tsx"), "utf8");
    expect(filter).toContain("listKnowledgeDocuments");
    expect(filter).not.toContain("/api/admin/metrics");
    expect(filter).toContain("资料范围");
    expect(filter).toContain("selected.length");
  });

  test("artifacts hand off to browser verification and multi-agent review in the same Chat", () => {
    const artifact = fs.readFileSync(path.join(frontend, "components", "ArtifactPanel.tsx"), "utf8");
    expect(artifact).toContain('handoffCanvasForReview("browser"');
    expect(artifact).toContain('handoffCanvasForReview("team"');
    expect(artifact).toContain("浏览器事实核验");
    expect(artifact).toContain("多 Agent 并行评审");
    expect(artifact).toContain("未经我确认不要覆盖画布");
  });
});
