/**
 * Tests for lib/contentParser.ts
 *
 * Run with: npx vitest run __tests__/contentParser.test.ts
 */
import { describe, it, expect } from "vitest";
import { parseContentBlocks } from "../lib/contentParser";

describe("parseContentBlocks", () => {
  it("parses plain text as a single text block", () => {
    const blocks = parseContentBlocks("Hello world\nSecond line");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("text");
    expect(blocks[0].content).toBe("Hello world\nSecond line");
  });

  it("parses code blocks with language", () => {
    const input = "before\n```python\nprint('hello')\nx = 1\n```\nafter";
    const blocks = parseContentBlocks(input);
    expect(blocks).toHaveLength(3);
    expect(blocks[0].type).toBe("text");
    expect(blocks[0].content).toBe("before");
    expect(blocks[1].type).toBe("code");
    expect(blocks[1].content).toBe("print('hello')\nx = 1");
    expect(blocks[1].meta?.language).toBe("python");
    expect(blocks[1].meta?.lineCount).toBe(2);
    expect(blocks[2].type).toBe("text");
    expect(blocks[2].content).toBe("after");
  });

  it("parses mermaid blocks", () => {
    const input = "```mermaid\ngraph TD\nA-->B\n```";
    const blocks = parseContentBlocks(input);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("mermaid");
    expect(blocks[0].content).toContain("graph TD");
  });

  it("parses diff blocks", () => {
    const input = "```diff\n-old line\n+new line\n```";
    const blocks = parseContentBlocks(input);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("diff");
    expect(blocks[0].content).toContain("-old line");
  });

  it("parses block math", () => {
    const input = "$$\nE = mc^2\n$$";
    const blocks = parseContentBlocks(input);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("math_block");
    expect(blocks[0].content).toContain("E = mc^2");
  });

  it("parses tables", () => {
    const input = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |";
    const blocks = parseContentBlocks(input);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("table");
    expect(blocks[0].content).toContain("| A | B |");
  });

  it("handles mixed content", () => {
    const input = [
      "Here is some text.",
      "",
      "```python",
      "x = 1",
      "```",
      "",
      "| Col1 | Col2 |",
      "| --- | --- |",
      "| a | b |",
      "",
      "More text.",
    ].join("\n");

    const blocks = parseContentBlocks(input);
    const types = blocks.map(b => b.type);
    expect(types).toContain("text");
    expect(types).toContain("code");
    expect(types).toContain("table");
  });

  it("handles empty input", () => {
    const blocks = parseContentBlocks("");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("text");
    expect(blocks[0].content).toBe("");
  });

  it("handles code block without language", () => {
    const input = "```\nsome code\n```";
    const blocks = parseContentBlocks(input);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type).toBe("code");
    expect(blocks[0].meta?.language).toBe("text");
  });

  it("extracts filename from code comments", () => {
    const input = "```python\n# filename: utils.py\ndef hello(): pass\n```";
    const blocks = parseContentBlocks(input);
    expect(blocks[0].meta?.filename).toBe("utils.py");
  });

  it("handles multiple code blocks", () => {
    const input = "```js\na=1\n```\ntext\n```python\nb=2\n```";
    const blocks = parseContentBlocks(input);
    expect(blocks).toHaveLength(3);
    expect(blocks[0].type).toBe("code");
    expect(blocks[0].meta?.language).toBe("js");
    expect(blocks[1].type).toBe("text");
    expect(blocks[2].type).toBe("code");
    expect(blocks[2].meta?.language).toBe("python");
  });
});
