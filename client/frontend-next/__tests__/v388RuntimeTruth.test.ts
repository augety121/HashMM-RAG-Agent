import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V388 runtime truth and partial-data UX", () => {
  const root = path.resolve(__dirname, "..");

  it("does not turn failed dashboard reads into convincing zero values", () => {
    const dashboard = fs.readFileSync(path.join(root, "components", "AdminDashboard.tsx"), "utf8");
    expect(dashboard).toContain("部分数据暂时无法验证");
    expect(dashboard).toContain("数值不会用 0 代替读取失败");
    expect(dashboard).toContain("value == null ? \"—\"");
    expect(dashboard).toContain("readSource<MetricsData>");
    expect(dashboard).toContain("评测服务暂时无法连接");
    expect(dashboard).toContain("基准服务暂时无法连接");
    expect(dashboard).not.toContain('value={rt?.total_llm_calls || 0}');
  });

  it("requires document receipts instead of inventing background success", () => {
    const docs = fs.readFileSync(path.join(root, "components", "admin", "DocsTab.tsx"), "utf8");
    expect(docs).toContain("内容回执");
    expect(docs).toContain("未取得批量任务回执");
    expect(docs).toContain("无法确认是否已执行");
    expect(docs).toContain("已有列表为最近一次成功结果，不会用空列表覆盖");
    expect(docs).not.toContain("服务器会继续处理");
    expect(docs).not.toContain("d.source_path");
  });
});
