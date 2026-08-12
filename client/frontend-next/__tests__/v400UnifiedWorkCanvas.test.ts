import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const repo = path.resolve(__dirname, "..", "..");
const frontend = path.join(repo, "frontend-next");
const app = path.resolve(repo, "..", "app");

describe("V400 unified work canvas", () => {
  it("keeps the server canvas deterministic, owner-bound and non-executing", () => {
    const runtime = fs.readFileSync(path.join(repo, "hashmm", "agent", "work_runtime.py"), "utf8");
    const routes = fs.readFileSync(path.join(repo, "hashmm", "api", "routes", "work_runtime.py"), "utf8");
    expect(runtime).toContain('WORK_CANVAS_SCHEMA = "hashmm.work-canvas.v1"');
    expect(runtime).toContain('COMPLETION_RECEIPT_SCHEMA = "hashmm.completion-receipt.v1"');
    expect(runtime).toContain('"model_prose_is_completion_evidence": False');
    expect(runtime).toContain('"auto_execution_enabled": False');
    expect(runtime).toContain('"automatic_promotion_allowed": False');
    expect(runtime).toContain('"paired_replay_required_before_promotion": True');
    expect(runtime).toContain("current_status != \"delivered\"");
    expect(runtime).toContain("WHERE id=? AND user_id=?");
    expect(routes).toContain('@router.get("/{run_id}/workspace"');
    expect(routes).toContain('@router.post("/{run_id}/decisions"');
    expect(routes).toContain('"Vary": "Authorization"');
  });

  it("uses one desktop surface for goal, process, evidence, results and review", () => {
    const view = fs.readFileSync(
      path.join(frontend, "components", "desktop", "WorkCanvasView.tsx"), "utf8",
    );
    const api = fs.readFileSync(path.join(frontend, "lib", "api.ts"), "utf8");
    expect(view).toContain('id: "overview", label: "概览"');
    expect(view).toContain('id: "process", label: "过程"');
    expect(view).toContain('id: "evidence", label: "依据"');
    expect(view).toContain('id: "results", label: "成果"');
    expect(view).toContain("workRunDecision(");
    expect(view).toContain('inspectorTab: "artifact"');
    expect(view).not.toContain("Math.random");
    expect(api).toContain("workRunWorkspace");
    expect(api).toContain('"If-None-Match"');
    expect(api).toContain("workCanvasCacheGet");
    expect(api).toContain("workCanvasCachePut");
    expect(api).toContain("/workspace");
  });

  it("gives mobile a native work surface and account-scoped conditional cache", () => {
    const repository = fs.readFileSync(
      path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "data", "remote", "WorkRuntimeRepository.kt"),
      "utf8",
    );
    const screen = fs.readFileSync(
      path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "workbench", "WorkCanvasScreen.kt"),
      "utf8",
    );
    const activity = fs.readFileSync(
      path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "activity", "ActivityScreen.kt"),
      "utf8",
    );
    const workbench = fs.readFileSync(
      path.join(app, "app", "src", "main", "java", "com", "hashmm", "app", "ui", "workbench", "WorkbenchHubScreen.kt"),
      "utf8",
    );
    expect(repository).toContain("canvasesByRun");
    expect(repository).toContain("/workspace");
    expect(repository).toContain('"If-None-Match"');
    expect(repository).toContain("/decisions");
    expect(repository).toContain("hashmm.mobile-work-outbox.v1");
    expect(repository).toContain("reconcileOutbox");
    expect(repository).toContain("same durable id");
    expect(screen).toContain('"概览", "过程", "依据", "成果"');
    expect(screen).toContain("InAppFileViewer");
    expect(screen).toContain("workRunDecision");
    expect(activity).toContain("onOpenWork(run.id)");
    expect(workbench).toContain("onOpenWork(run.id)");
  });
});
