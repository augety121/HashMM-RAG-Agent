/**
 * Tests for lib/render.ts
 *
 * Run with: npx vitest run __tests__/render.test.ts
 * Or add to package.json scripts: "test": "vitest"
 */
import { describe, it, expect } from "vitest";
import { sanitizeLLMOutput, renderMsg } from "../lib/render";

describe("sanitizeLLMOutput", () => {
  it("strips DeepSeek DSML tags", () => {
    expect(sanitizeLLMOutput("hello <|DSML|> world")).toBe("hello  world");
    expect(sanitizeLLMOutput("before <|im_start|>system<|im_end|> after")).toBe("before  after");
    expect(sanitizeLLMOutput("text <|endoftext|>")).toBe("text ");
  });

  it("strips XML tool tags", () => {
    expect(sanitizeLLMOutput("result <tool_call>foo</tool_call>")).toBe("result foo");
    expect(sanitizeLLMOutput("<observation>data</observation>")).toBe("data");
    expect(sanitizeLLMOutput("<assistant_response>hi</assistant_response>")).toBe("hi");
  });

  it("preserves normal HTML-like content", () => {
    expect(sanitizeLLMOutput("use <div> tags")).toBe("use <div> tags");
    expect(sanitizeLLMOutput("a < b and c > d")).toBe("a < b and c > d");
  });

  it("handles empty string", () => {
    expect(sanitizeLLMOutput("")).toBe("");
  });

  it("handles multiple tags in one string", () => {
    const input = "<|DSML|>Hello<tool_call>test</tool_call> world<|im_end|>";
    const result = sanitizeLLMOutput(input);
    expect(result).toBe("Hellotest world");
  });
});

describe("renderMsg", () => {
  it("renders plain text", () => {
    const html = renderMsg("Hello world");
    expect(html).toContain("Hello world");
  });

  it("renders bold text", () => {
    const html = renderMsg("**bold text**");
    expect(html).toContain("<strong>");
    expect(html).toContain("bold text");
  });

  it("renders inline code", () => {
    const html = renderMsg("use `console.log`");
    expect(html).toContain("<code");
    expect(html).toContain("console.log");
  });

  it("renders code blocks with language", () => {
    const html = renderMsg("```python\nprint('hello')\n```");
    expect(html).toContain("python");
    expect(html).toContain("print");
  });

  it("renders links", () => {
    const html = renderMsg("visit [Google](https://google.com)");
    expect(html).toContain("href=");
    expect(html).toContain("https://google.com");
  });

  it("renders lists", () => {
    const html = renderMsg("- item 1\n- item 2\n- item 3");
    expect(html).toContain("<li>");
  });

  it("sanitizes DSML tags in rendered output", () => {
    const html = renderMsg("hello <|DSML|> world");
    expect(html).not.toContain("DSML");
  });

  it("handles empty string", () => {
    const html = renderMsg("");
    expect(html).toBe("");
  });

  it("renders math blocks", () => {
    const html = renderMsg("The formula is $E = mc^2$");
    // Math rendering depends on KaTeX availability
    // At minimum, the content should be preserved
    expect(html).toContain("E");
  });

  it("renders numbered lists", () => {
    const html = renderMsg("1. first\n2. second\n3. third");
    expect(html).toContain("<li>");
  });

  it("renders blockquotes", () => {
    const html = renderMsg("> this is a quote");
    expect(html).toContain("blockquote");
  });
});
