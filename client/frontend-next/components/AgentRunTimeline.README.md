# AgentRunTimeline 接入说明

新组件 `frontend-next/components/AgentRunTimeline.tsx` —— 把一轮 agent run 的 trace
步骤按"大阶段"(理解/检索/推理/执行/合成)分组，串成 deep-research 风的可回放时间线。

## 它和现有 AgentLog 的区别
- `AgentLog`：平铺时间线（一个个步骤竖排），已在用。
- `AgentRunTimeline`：**按 phase 分组折叠**，每个阶段一个彩色图标头 + 可展开的步骤，
  step 带 `#step_id` 序号，更像 deep-research 的过程回放。

## 数据从哪来
后端 `hashmm/api/run_timeline.py` 在 `HASHMM_AGENT_TIMELINE=1` 时，给每个 trace step
注入了 `phase` 和 `step_id`。前端 SSE 已在 `ChatArea.tsx` 累积 `traceSteps`，这些 step
现在就带着 phase/step_id。组件对没有 phase 的 step 会按 node 兜底推断，**永远能渲染**。

## 怎么挂（二选一）

### 方式 A：并存（最安全，先看效果）
在 `ChatArea.tsx` 现有 `<AgentLog .../>` 附近加一个开关，临时渲染时间线对比效果。

### 方式 B：替换
把 `ChatArea.tsx:498-515` 那段 `<AgentLog steps={[...]} .../>` 换成：
```tsx
import { AgentRunTimeline } from "./AgentRunTimeline";
// ...
{(agentSteps.length > 0 || traceSteps.length > 0) && (
  <AgentRunTimeline
    steps={[
      ...(traceSteps || []).map((t: any, i: number) => ({
        id: `live-trace-${i}`, node: t.node || "classify",
        detail: t.detail || "", status: "done" as const,
        phase: t.phase, step_id: t.step_id,    // ← 后端注入的字段透传过来
      })),
      ...agentSteps.map((s, i) => ({
        id: s.id || `live-step-${i}`, node: "tool",
        detail: `${s.tool}: ${s.detail || ""}`,
        status: s.status as "done" | "running" | "error",
        elapsed_ms: s.duration_ms,
      })),
    ]}
  />
)}
```

## 验证
- `cd frontend-next && npm run build` 应编译通过（组件零新依赖，只用 react/lucide-react）。
- 起后端时加 `HASHMM_AGENT_TIMELINE=1`，发一条会触发多步的查询（如"分析小米2024营收做PPT"），
  即可在前端看到按阶段分组的时间线。不加该 env 时，组件仍按 node 兜底分组，照常显示。

## 不改动什么
没改 `AgentLog.tsx`、没改 `ChatArea.tsx`。这是纯新增组件，挂不挂都不影响现有功能。
