import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(__dirname, "..");
const read = (...parts: string[]) => fs.readFileSync(path.join(root, ...parts), "utf8");

describe("V820 API access control surface", () => {
  it("wires the self-service API tab into settings", () => {
    const settings = read("components", "SettingsModal.tsx");
    expect(settings).toContain('id: "apiaccess"');
    expect(settings).toContain("<ApiAccessSettings />");
  });

  it("keeps the one-time secret in component memory only", () => {
    const component = read("components", "settings", "ApiAccessSettings.tsx");
    expect(component).toContain("完整密钥只显示这一次");
    expect(component).toContain('setOneTimeSecret("")');
    expect(component).not.toContain("localStorage");
    expect(component).not.toContain("sessionStorage");
  });

  it("uses revisioned updates and irreversible revocation endpoints", () => {
    const client = read("lib", "api.ts");
    expect(client).toContain("updatePlatformApiKey");
    expect(client).toContain("JSON.stringify({ ...input, revision })");
    expect(client).toContain('method: "DELETE"');
    expect(client).toContain("PlatformApiKeyCreated");
  });
});

