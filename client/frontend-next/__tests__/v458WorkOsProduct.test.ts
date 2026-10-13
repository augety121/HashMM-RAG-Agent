import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

describe("V439-V458 Work OS is connected to the desktop product", () => {
  const repo = path.resolve(process.cwd(), "..");

  test("push transport keeps bearer credentials out of URLs and polling as fallback", () => {
    const api = fs.readFileSync(path.join(process.cwd(), "lib", "api.ts"), "utf8");
    const overview = fs.readFileSync(
      path.join(process.cwd(), "components", "desktop", "WorkOverviewView.tsx"), "utf8",
    );
    expect(api).toContain('Accept: "text/event-stream"');
    expect(api).toContain("...authHeaders()");
    expect(api).not.toMatch(/work-runs\/stream[^`]*access_token/);
    expect(overview).toContain("subscribeWorkFeed");
    expect(overview).toContain("60_000");
  });

  test("one user-facing projection powers placement, capability, recovery and work map", () => {
    const backend = fs.readFileSync(
      path.join(repo, "hashmm", "agent", "work_os.py"), "utf8",
    );
    const view = fs.readFileSync(
      path.join(process.cwd(), "components", "desktop", "WorkCanvasView.tsx"), "utf8",
    );
    expect(backend).toContain('WORK_PROJECTION_SCHEMA = "hashmm.work-projection.v2"');
    expect(backend).toContain('"model_prose_is_execution_evidence": False');
    expect(backend).toContain('"model_prose_is_verification_evidence": False');
    for (const label of ["工作位置", "执行方式", "工作地图", "恢复中心", "协作与接管"]) {
      expect(view).toContain(label);
    }
  });

  test("artifact feedback uses an owner-checked revision API instead of prompt prose", () => {
    const api = fs.readFileSync(path.join(process.cwd(), "lib", "api.ts"), "utf8");
    const view = fs.readFileSync(
      path.join(process.cwd(), "components", "desktop", "WorkCanvasView.tsx"), "utf8",
    );
    expect(api).toContain("/annotations");
    expect(view).toContain("artifact_revision: annotationResult.version");
    expect(view).toContain("保存批注并标记待重验");
  });

  test("ordinary composer keeps real work modes directly reachable", () => {
    const composer = fs.readFileSync(
      path.join(process.cwd(), "components", "ChatArea.tsx"), "utf8",
    );
    expect(composer).toContain('retrievalMode === "auto" ? "自动"');
    expect(composer).toContain("深度检索");
    expect(composer).toContain("多智能体");
    expect(composer).toContain("<DocFilterChips");
    expect(composer).not.toContain("composer-work-options");
  });
});
