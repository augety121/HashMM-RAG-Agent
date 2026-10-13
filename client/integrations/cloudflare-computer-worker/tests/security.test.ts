import { describe, expect, it } from "vitest";
import {
  isWorkspaceHandle,
  limitText,
  shellQuote,
  validateArgv,
  validateCwd,
  workspacePath,
} from "../src/security";

describe("Cloudflare Computer gateway boundary", () => {
  it("accepts only opaque workspace handles", () => {
    expect(isWorkspaceHandle(`ws_${"a".repeat(32)}`)).toBe(true);
    expect(isWorkspaceHandle("user@example.com")).toBe(false);
    expect(isWorkspaceHandle("personal")).toBe(false);
  });

  it("confines files and cwd to /workspace", () => {
    expect(workspacePath("project/readme.md")).toBe("/workspace/project/readme.md");
    expect(workspacePath("../secret")).toBeNull();
    expect(workspacePath("%2e%2e/secret")).toBeNull();
    expect(workspacePath("/etc/passwd")).toBeNull();
    expect(validateCwd("/workspace/project")).toBe("/workspace/project");
    expect(validateCwd("/tmp")).toBeNull();
  });

  it("uses argv and rejects unreviewed command groups", () => {
    expect(validateArgv(["ls", "-la"])).toEqual(["ls", "-la"]);
    expect(validateArgv(["curl", "https://example.com"])).toBeNull();
    expect(validateArgv(["env"])).toBeNull();
    expect(validateArgv(["sh", "-c", "rm -rf /"])).toBeNull();
    expect(shellQuote("a b")).toBe("'a b'");
  });

  it("clips output by bytes", () => {
    expect(limitText("abcdef", 3)).toEqual({ text: "abc", truncated: true });
  });
});
