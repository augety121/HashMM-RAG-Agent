import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V389 admin mutations require receipts", () => {
  const root = path.resolve(__dirname, "..");

  it("updates templates in place and preserves the last verified list on failure", () => {
    const source = fs.readFileSync(path.join(root, "components", "admin", "TemplatesTab.tsx"), "utf8");
    expect(source).toContain('method: "PATCH"');
    expect(source).toContain("服务器未确认原模板已更新");
    expect(source).toContain("已有列表为最近一次成功结果，不会用空列表覆盖");
    expect(source).not.toContain("Delete and recreate");
    expect(source).not.toContain('setTemplates([])');
  });

  it("only changes tools whose server mutations succeeded", () => {
    const source = fs.readFileSync(path.join(root, "components", "admin", "ToolsTab.tsx"), "utf8");
    expect(source).toContain("Promise.allSettled");
    expect(source).toContain("succeeded.has(t.name)");
    expect(source).toContain("失败项保持原状态");
    expect(source).not.toContain("api.toggleTool(n, enable).catch(() => {})");
  });

  it("does not turn skill or MCP read failures into verified empty states", () => {
    const skills = fs.readFileSync(path.join(root, "components", "admin", "SkillsTab.tsx"), "utf8");
    const mcp = fs.readFileSync(path.join(root, "components", "admin", "MCPServersPanel.tsx"), "utf8");
    expect(skills).toContain("当前没有取得可验证的技能列表");
    expect(skills).not.toContain("catch {}");
    expect(mcp).toContain("当前没有取得可验证的连接列表");
    expect(mcp).toContain("服务器已确认断开并删除");
  });

  it("requires save and delete receipts for custom API tools", () => {
    const source = fs.readFileSync(path.join(root, "components", "admin", "CustomToolsPanel.tsx"), "utf8");
    expect(source).toContain("服务器已确认保存自定义工具");
    expect(source).toContain("服务器已确认删除自定义工具");
    expect(source).toContain("当前保留最近一次成功读取的工具");
    expect(source).not.toContain("try { await api.deleteCustomTool(id); load(); } catch {}");
  });
});
