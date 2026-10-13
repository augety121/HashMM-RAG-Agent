import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

describe("V430-V438 work assurance is wired into the user workspace", () => {
  const repo = path.resolve(process.cwd(), "..");

  test("backend exposes one connected assurance projection", () => {
    const assurance = fs.readFileSync(
      path.join(repo, "hashmm", "agent", "work_assurance.py"), "utf8",
    );
    const runtime = fs.readFileSync(
      path.join(repo, "hashmm", "agent", "work_runtime.py"), "utf8",
    );
    for (const version of ["V430", "V431", "V432", "V433", "V434", "V435", "V436", "V437", "V438"]) {
      expect(assurance).toContain(version);
    }
    expect(assurance).toContain('"model_prose_is_evidence": False');
    expect(assurance).toContain('"auto_executes": False');
    expect(runtime).toContain('"assurance": assurance');
  });

  test("desktop rejects stale cached canvases and renders plain-language readiness", () => {
    const api = fs.readFileSync(path.join(process.cwd(), "lib", "api.ts"), "utf8");
    const view = fs.readFileSync(
      path.join(process.cwd(), "components", "desktop", "WorkCanvasView.tsx"), "utf8",
    );
    expect(api).toContain('data.assurance?.schema === "hashmm.work-assurance.v1"');
    expect(view).toContain("canvas.assurance.user_summary.headline");
    expect(view).toContain("交付准备度");
    expect(view).toContain("模型能力不匹配");
  });
});
