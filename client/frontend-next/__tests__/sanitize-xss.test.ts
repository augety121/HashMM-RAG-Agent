/**
 * XSS 净化测试（V308，修 P0-4）—— 针对流式渲染的注入面。
 *
 * Run: npx vitest run __tests__/sanitize-xss.test.ts
 *
 * 重点覆盖审计 P0-4 指出的【跨 token 拆分】场景：逐 token 到达时危险标签会被切成多段，
 * 单 token 清理拦不住拼接后的结果。stripDangerousHtml 现在对"残缺危险标签开头"也做中和。
 */
import { describe, it, expect } from "vitest";
import { stripDangerousHtml } from "../lib/sanitize-html";

const lc = (s: string) => s.toLowerCase();

describe("stripDangerousHtml — 基础 XSS", () => {
  it("移除 <script> 及其内容", () => {
    const out = lc(stripDangerousHtml("<script>alert(1)</script>"));
    expect(out).not.toContain("<script");
    expect(out).not.toContain("alert(1)");
  });
  it("移除 <iframe>", () => {
    expect(lc(stripDangerousHtml('<iframe src="evil"></iframe>'))).not.toContain("<iframe");
  });
  it("移除 <svg onload=>", () => {
    const out = lc(stripDangerousHtml("<svg onload=alert(1)>"));
    expect(out).not.toContain("<svg");
    expect(out).not.toContain("onload");
  });
  it("移除 onerror（空格与斜杠两种分隔）", () => {
    expect(lc(stripDangerousHtml("<img src=x onerror=alert(1)>"))).not.toContain("onerror");
    expect(lc(stripDangerousHtml("<img/onerror=alert(1)>"))).not.toContain("onerror");
  });
  it("中和 javascript: 协议", () => {
    expect(lc(stripDangerousHtml('<a href="javascript:alert(1)">x</a>'))).not.toContain("javascript:alert");
  });
  it("清掉链式事件处理器", () => {
    const out = lc(stripDangerousHtml("<div onclick=x onmouseover=y>"));
    expect(out).not.toContain("onclick");
    expect(out).not.toContain("onmouseover");
  });
});

describe("stripDangerousHtml — 跨 token / 残缺标签（P0-4 核心）", () => {
  it("行尾残缺的 <script 开头被中和", () => {
    expect(lc(stripDangerousHtml("text <script"))).not.toContain("<script");
  });
  it("残缺 <svg onload= 被中和", () => {
    const out = lc(stripDangerousHtml("hi <svg onload="));
    expect(out).not.toContain("<svg");
    expect(out).not.toContain("onload=");
  });
  it("残缺 <img ... onerror= 被中和", () => {
    expect(lc(stripDangerousHtml("x <img src=y onerror="))).not.toContain("onerror=");
  });
  it("大小写混淆 <ScRiPt> 被移除", () => {
    const out = lc(stripDangerousHtml("<ScRiPt>alert(1)</ScRiPt>"));
    expect(out).not.toContain("<script");
    expect(out).not.toContain("alert(1)");
  });
});

describe("stripDangerousHtml — 正常内容保留", () => {
  it("普通 <div> 不被误删", () => {
    expect(stripDangerousHtml("use <div> tag")).toContain("<div>");
  });
  it("数学小于号保留", () => {
    expect(stripDangerousHtml("a < b and c > d")).toContain("a < b");
  });
  it("表格标签保留", () => {
    expect(stripDangerousHtml("<td>cell</td>")).toContain("<td>");
  });
});
