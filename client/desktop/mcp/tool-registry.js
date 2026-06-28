/**
 * desktop/mcp/tool-registry.js — MCP 工具注册表（V100，对标 Marvis 的 McpToolRegistry）。
 *
 * 工具 = { name, description, inputSchema(JSON Schema), handler(args)->result }。
 * 提供注册、列出（给 tools/list 的 schema，剥掉 handler）、基础入参校验（required +
 * type 粗校验）、调用。纯逻辑，不碰 IO，可单测。
 */
"use strict";

class ToolRegistry {
  constructor() { this._tools = new Map(); }

  /** 注册一个工具。重复名覆盖（后注册胜出）。 */
  register(name, def) {
    if (!name || typeof name !== "string") throw new Error("工具名必须是非空字符串");
    if (!def || typeof def.handler !== "function") throw new Error(`工具 ${name} 缺少 handler`);
    this._tools.set(name, {
      name,
      description: def.description || "",
      inputSchema: def.inputSchema || { type: "object", properties: {} },
      handler: def.handler,
    });
    return this;
  }

  has(name) { return this._tools.has(name); }
  get(name) { return this._tools.get(name); }
  size() { return this._tools.size; }

  /** tools/list 用：只暴露 name/description/inputSchema，不暴露 handler。 */
  list() {
    return Array.from(this._tools.values()).map((t) => ({
      name: t.name, description: t.description, inputSchema: t.inputSchema,
    }));
  }

  /**
   * 基础入参校验（不引第三方 JSON Schema 库）：必填字段在不在、声明类型对不对。
   * @returns {{ok:boolean, error?:string}}
   */
  validate(name, args) {
    const t = this._tools.get(name);
    if (!t) return { ok: false, error: `未知工具：${name}` };
    const schema = t.inputSchema || {};
    const props = schema.properties || {};
    const required = schema.required || [];
    const a = args || {};
    for (const key of required) {
      if (!(key in a)) return { ok: false, error: `缺少必填参数：${key}` };
    }
    for (const [key, val] of Object.entries(a)) {
      const spec = props[key];
      if (!spec || !spec.type) continue;
      if (!_typeOk(val, spec.type)) return { ok: false, error: `参数 ${key} 类型应为 ${spec.type}` };
    }
    return { ok: true };
  }

  /** 校验并调用工具 handler。返回 handler 的结果（可为 Promise）。 */
  async call(name, args) {
    const v = this.validate(name, args);
    if (!v.ok) throw new Error(v.error);
    return this._tools.get(name).handler(args || {});
  }
}

function _typeOk(val, type) {
  switch (type) {
    case "string": return typeof val === "string";
    case "number": return typeof val === "number";
    case "integer": return typeof val === "number" && Number.isInteger(val);
    case "boolean": return typeof val === "boolean";
    case "array": return Array.isArray(val);
    case "object": return val !== null && typeof val === "object" && !Array.isArray(val);
    default: return true; // 未知类型不拦
  }
}

module.exports = { ToolRegistry };
