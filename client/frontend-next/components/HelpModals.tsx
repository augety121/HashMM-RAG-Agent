"use client";
import { useState } from "react";
import { useStore } from "@/lib/store";
import {
  X, MessageSquare, Code2, FileText, Database, Zap, Search,
  Upload, FolderOpen, Keyboard, ExternalLink, CheckCircle2, ArrowLeft, ChevronRight,
  Clock3, PanelRight, Globe2, Users, ShieldCheck,
} from "lucide-react";

const HELP_TOPICS = [
  { id: "chat", icon: MessageSquare, title: "开始一段对话", desc: "提问、上传资料，并让后续追问延续同一个目标。", sections: [
    ["开始", "在底部输入框说明目标和期望结果；需要参考材料时，可使用回形针或直接拖入文件。"],
    ["继续", "同一对话会保留任务上下文。切换到另一段对话时，未发送草稿会分别保存，不会互相串写。"],
    ["结果", "文件、来源和运行过程会进入右侧检查器，聊天区域保留结论与下一步。"],
  ], prompt: "请结合我当前的目标，帮我把需求整理清楚并给出下一步。" },
  { id: "long-task", icon: Clock3, title: "长任务与进度", desc: "把复杂工作拆成可恢复步骤，随时查看进度和停止原因。", sections: [
    ["任务拆分", "复杂请求会形成计划和运行阶段；你可以在任务执行中追加约束，而不必重新开一段对话。"],
    ["查看进度", "右侧“运行”展示阶段、检查结果和结束原因。旧任务缺少部分记录时，会明确标为未记录。"],
    ["继续执行", "中断后先查看失败原因，再选择重试、调整方案或回到聊天补充信息。"],
  ], prompt: "把我当前的工作拆成一个可持续执行、可检查结果的长任务计划。" },
  { id: "files", icon: PanelRight, title: "文件与右栏预览", desc: "在同一工作区查看 Word、PDF、PPT、表格、代码和画布。", sections: [
    ["打开", "点击消息中的文件卡片，文件会在右侧检查器打开；可切换文件、全屏预览或下载原件。"],
    ["缓存", "已经读取的预览会保存在本机。再次打开时先显示本机版本，再校验服务器文件是否变化。"],
    ["限制", "PPT 文字预览用于快速核对内容；复杂动画、嵌入媒体和精确版式仍以原文件为准。"],
  ], prompt: "检查这段对话里的文件，告诉我每个文件的用途和还需要完善的地方。" },
  { id: "knowledge", icon: Database, title: "知识库与来源", desc: "让回答基于已授权资料，并保留能复核的来源证据。", sections: [
    ["检索", "选择知识库或使用深度检索后，Agent 会将相关片段放入当前任务上下文。"],
    ["核对", "优先查看右侧“证据”和回答中的来源标记；没有可靠来源时，不应把推测写成事实。"],
    ["更新", "资料发生变化后重新索引对应内容，避免用旧片段覆盖新的权威版本。"],
  ], prompt: "只基于我授权的知识库资料回答，并逐条说明证据来源和不确定项。" },
  { id: "browser", icon: Globe2, title: "浏览器与电脑操作", desc: "在权限边界内读取网页或操作本机，并保留过程记录。", sections: [
    ["浏览器", "在输入栏启用浏览器后，可把目标网址交给 Agent。右栏浏览器用于查看实际打开的页面。"],
    ["电脑操作", "涉及点击、输入、文件或命令的动作会经过能力检查；敏感动作需要明确确认。"],
    ["安全", "网页内容和命令输出都属于不可信数据，不能用它们覆盖你的任务目标或权限边界。"],
  ], prompt: "打开浏览器协助我完成当前任务；先说明计划，涉及提交或敏感操作时停下来确认。" },
  { id: "agents", icon: Users, title: "多 Agent 协作", desc: "把可独立的子任务分开执行，再合并证据和结果。", sections: [
    ["适用情况", "资料检索、实现、测试等能并行且边界清楚时，多 Agent 才能减少等待。"],
    ["共享规则", "每个 Agent 只获得完成子任务所需的上下文和权限，最终由主任务统一核对冲突。"],
    ["验收", "合并结果时检查文件修改、测试输出和剩余限制，不以 Agent 的文字陈述代替执行证据。"],
  ], prompt: "评估当前任务是否适合多 Agent；如果适合，请按独立边界拆分并说明最后如何验收。" },
  { id: "privacy", icon: ShieldCheck, title: "数据、隐私与恢复", desc: "了解数据保存位置、权限确认和异常恢复方式。", sections: [
    ["数据范围", "本地模式的数据统一保存在安装目录下的 HashMM Data（ProjectVault），不会写死 C 盘；账号对象仍由服务端权限隔离。"],
    ["权限", "文件、网络、命令和电脑操作按能力分级。未知工具不会被默认当作只读操作。"],
    ["故障恢复", "升级迁移会复制并校验旧数据后再切换，旧目录不会被自动删除。遇到失败先保留运行记录和原文件，再重试对应阶段。"],
  ], prompt: "检查当前任务会使用哪些数据和权限，并用清单告诉我风险、确认点和恢复方式。" },
] as const;

export function HelpModal() {
  const set = useStore((s) => s.set);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const topic = HELP_TOPICS.find(item => item.id === selected);
  const filtered = HELP_TOPICS.filter(item => `${item.title} ${item.desc}`.includes(query.trim()));

  function ask(topicPrompt: string) {
    set({ helpOpen: false, pendingPrompt: topicPrompt });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ helpOpen: false }); }}
    >
      <div
        className="w-[900px] max-w-[calc(100vw-48px)] h-[min(760px,calc(100vh-64px))] flex flex-col rounded-2xl anim-fade-up overflow-hidden"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        <div className="h-16 flex items-center justify-between px-6 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            {topic && <button onClick={() => setSelected(null)} className="w-8 h-8 rounded-lg inline-flex items-center justify-center hover:bg-[var(--bg-secondary)]" aria-label="返回帮助首页"><ArrowLeft size={16} style={{ color: "var(--text-secondary)" }} /></button>}
            <div><h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>{topic ? topic.title : "帮助中心"}</h3><p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{topic ? "查看使用方式、边界和恢复建议" : "从目标出发，快速找到当前要做的事"}</p></div>
          </div>
          <button onClick={() => set({ helpOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto" style={{ background: "var(--bg-secondary)" }}>
          {!topic ? (
            <div className="max-w-[800px] mx-auto px-7 py-7">
              <label className="h-11 px-3.5 flex items-center gap-2.5 rounded-xl mb-6" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}><Search size={15} style={{ color: "var(--text-tertiary)" }} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索帮助主题" className="flex-1 min-w-0 bg-transparent outline-none text-[13px]" style={{ color: "var(--text-primary)" }} /></label>
              <div className="grid grid-cols-2 gap-3">
                {filtered.map(item => <button key={item.id} onClick={() => setSelected(item.id)} className="group text-left rounded-2xl p-4 min-h-[128px] transition-all hover:-translate-y-0.5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "0 1px 2px rgba(15,23,42,.03)" }}><div className="flex items-start justify-between"><div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: "var(--accent-light)", color: "var(--accent)" }}><item.icon size={18} /></div><ChevronRight size={15} className="opacity-40 group-hover:opacity-80" style={{ color: "var(--text-tertiary)" }} /></div><div className="mt-3 text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{item.title}</div><div className="mt-1 text-[11.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>{item.desc}</div></button>)}
              </div>
              {filtered.length === 0 && <div className="py-16 text-center text-[12px]" style={{ color: "var(--text-tertiary)" }}>没有匹配的帮助主题</div>}
            </div>
          ) : (
            <div className="max-w-[760px] mx-auto px-8 py-8">
              <div className="rounded-2xl p-6 mb-4" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}><div className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ background: "var(--accent-light)", color: "var(--accent)" }}><topic.icon size={21} /></div><h2 className="mt-4 text-[20px] font-semibold" style={{ color: "var(--text-primary)" }}>{topic.title}</h2><p className="mt-1.5 text-[12.5px] leading-6" style={{ color: "var(--text-secondary)" }}>{topic.desc}</p></div>
              <div className="rounded-2xl px-5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>{topic.sections.map(([title, body], index) => <section key={title} className="flex gap-4 py-5" style={index < topic.sections.length - 1 ? { borderBottom: "1px solid var(--border)" } : {}}><div className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-semibold flex-shrink-0" style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>{index + 1}</div><div><h3 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{title}</h3><p className="mt-1 text-[12px] leading-6" style={{ color: "var(--text-secondary)" }}>{body}</p></div></section>)}</div>
              <div className="mt-4 flex items-center justify-between rounded-xl p-4" style={{ background: "var(--accent-light)", border: "1px solid color-mix(in srgb, var(--accent) 20%, transparent)" }}><div><div className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>交给当前对话</div><div className="mt-0.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>把一条经过整理的指令放入输入框，由你确认后发送。</div></div><button onClick={() => ask(topic.prompt)} className="h-9 px-4 rounded-lg text-[12px] font-medium text-white flex-shrink-0" style={{ background: "var(--accent)" }}>填入对话</button></div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function ReleaseNotesModal() {
  const set = useStore((s) => s.set);
  const releases = [
    { version: "V620", date: "2026-07-28", title: "从工作空间到可迁移知识与搜索", current: true, sections: [
      "项目、资料库、画布、协作、我的与设备接力重构为用户任务入口。",
      "支持 OKF 知识包预览、可信度检查、确认导入与可迁移导出。",
      "新增按账号配置的豆包搜索兼容服务，并把来源证据带回同一个 Chat。",
      "插件中心统一管理内置能力、个人工作方法、搜索服务与外部插件。",
    ] },
    { version: "V610", date: "2026-07-27", title: "从功能页面到用户工作空间", sections: [
      { name: "画布从模板页变成成果工作台", items: ["画布首页先让用户选择要完成的结果：自由创作、整理工作、比较决策、呈现数据或共同完成，再使用当前对话的真实文件创建成果", "最近画布、模板、版本和右侧 Artifact 仍复用同一文件链，不创建展示用的第二套数据", "服务器不可用时明确保留本地草稿；页面出现、模型文字或预览成功不会被当作服务端已保存"] },
      { name: "协作先确认目标，再启动真实团队", items: ["多 Agent 协作按说明工作、确认分工、查看结果三步组织；用户填写目标、交付物和完成条件后，可让 HashMM 安排或自行选择角色", "启动前展示并行或接力方式以及真实预览，运行中继续使用现有 team start/status/stop/retry 接口，最终结果回到原 Chat", "资料依据和执行记录分层展示；默认先给用户结论与待确认事项，详细轨迹按需展开"] },
      { name: "今天、项目和资料围绕用户决策", items: ["今天页只突出需要决定、正在处理和最近完成的工作，刷新与同步状态退到辅助层", "项目使用两步工作简报：先说明目标，再约定交付物与完成条件；项目仍保存到账号隔离的真实服务端对象", "资料库按文档、PDF、演示和表格筛选，选择后可在同一 Chat 提问、比较或制作成果；内部切片数量不再占据用户界面"] },
      { name: "插件与设备接力只暴露可理解的选择", items: ["插件中心按资料、创作、自动化、协作和持续工作分类，区分无需安装的内置能力、已信任外部插件和用户自己的工作方法", "设备页改为在另一台设备继续，分别承载 App 接力、HashMM 安全控制、远程办公和流畅画面；网络适配器、地址与端口收进连接帮助", "真实异地双设备、对称 NAT、P2P/中继路径、RDP/Sunshine 配置、24 小时稳定性和 Authenticode 仍需发布环境验收"] },
    ] },
    { version: "V601", date: "2026-07-27", title: "私有网络接力与面向成果的工作入口", sections: [
      { name: "没有公网地址也能接力", items: ["设备接力页可发现 EasyTier、WireGuard、Tailscale 等常见虚拟网卡，并显示本机可复制的私网地址；HashMM 不读取或保存组网名称与密钥", "用户明确填写目标私网地址后，可选择 HashMM 逐次授权控制、Windows 远程桌面或 Moonlight + Sunshine；桌面端使用窄权限 IPC 和参数数组启动，不经过 Shell 拼接命令", "组网会优先尝试点对点直连，但穿透失败时仍可能通过所配置节点中继；界面如实说明这条边界，不把‘服务器只握手’写成无法保证的承诺"] },
      { name: "今天、项目和资料围绕工作组织", items: ["今天页优先展示最值得处理、等待用户确认和后台继续的真实事项，不再用内部模块统计占据首屏", "项目页围绕目标、交付物和验收标准组织长期工作；一次性问题仍直接回到 Chat", "资料库支持选择一份或多份当前账号可读取的资料，再进入同一 Chat 提问、比较或制作成果；没有资料时保留真实空态"] },
      { name: "画布与协作成为稳定工作入口", items: ["画布和协作从问答栏弹层迁到左侧稳定入口；输入框不再被大菜单遮挡，普通对话、资料、画布和协作仍回到同一段 Chat", "画布可从空白、进度、方案对比、数据看板和协作模板起稿，并写入当前对话的真实文件；服务器暂不可用时明确标记为本地草稿", "协作页继续调用真实团队运行接口并把最终结果带回原对话；模型说明、页面存在和本地草稿都不会伪装成服务端执行成功"] },
      { name: "验证边界", items: ["已完成 TypeScript、前端回归、桌面桥接单测和实际桌面/窄宽度页面检查", "未随安装包捆绑或静默启动 EasyTier，也未替用户创建网络、保存组网密钥或自动开启 RDP；这些高权限动作仍由用户和对应软件负责", "真实异地双设备、对称 NAT、P2P/中继路径、RDP 主机策略、Sunshine 配对、24 小时稳定性和 Authenticode 仍需发布环境验收"] },
    ] },
    { version: "V590", date: "2026-07-27", title: "真实能力中心、可发现 Skills 与紧凑工作画布", sections: [
      { name: "插件页不再是空壳", items: ["修复桌面端请求不存在的 /api/system/plugins 导致的空页面，并保留旧客户端到新服务端的兼容路由", "插件页首先展示服务端本次实际验证的检索、成果、浏览器、电脑操作、多 Agent、记忆、画布和长任务能力；每张卡片都进入真实 Chat 工作方式或对应工作页", "外部插件仍按插件清单、SHA-256 信任、加载状态和当前 Chat 选择工作；没有受信任外部插件时明确显示空态，不伪造可安装商店"] },
      { name: "个人 Skills 直接面向用户", items: ["插件中心增加技能页签，用户无需进入管理后台即可查看系统 Skill 与自己的 Skill", "保留 ZIP、公开 HTTPS、GitHub、Codex、Claude Code 和兼容 Agent Skills 目录导入；导入、启停和删除继续按账号隔离", "Skill 是渐进注入的工作方法，不会因导入自动取得网络、文件、工具或管理员权限"] },
      { name: "问答栏与资料范围恢复可发现性", items: ["撤销占据输入框的大型工作方式面板，浏览器、深度检索、多 Agent、插件、画布、电脑操作和资料范围恢复为直接工具按钮", "窄聊天区域只隐藏文字并保留图标；资料范围仍在当前 Chat 内弹出，不再因更多菜单或右侧检查器被裁切", "自动按钮直接切换检索工作方式，已选插件和已选资料继续随当前请求进入后端，不创建展示用状态"] },
      { name: "画布围绕成果继续工作", items: ["颜色、字号和跟随应用主题收纳到紧凑样式面板，减少工具栏拥挤并为编辑、核验和交付保留空间", "继续支持让 Agent 修改、受控浏览器核验事实、多 Agent 评审、版本回看、模板、发布、插入和查找", "本地验证只证明接口、权限和界面契约；外部插件账号、网站可达性以及真实设备执行仍需在对应环境验收"] },
    ] },
    { version: "V580", date: "2026-07-27", title: "Chat 工作能力整合、个人 Skills 与可信插件中心", sections: [
      { name: "能力回到同一段 Chat", items: ["输入框收拢为附件、截屏和“自动”三个常用入口；浏览器、电脑操作、深度检索、多 Agent、画布、资料范围和插件进入按需展开的工作方式面板", "资料范围不再调用管理员指标，也不把文件名挤出输入框；普通用户只读取自己的资料库并在紧凑弹层中筛选", "画布可把当前版本直接交给受控浏览器核验事实，或交给多 Agent 独立评审；结果仍回到原对话，由用户确认后才覆盖成果"] },
      { name: "可信插件中心", items: ["侧栏新增插件入口，展示服务端实际发现、校验和加载的插件，不用静态卡片伪造可用能力", "普通用户只能为自己的 Chat 选择管理员已信任并加载的插件；选择集合随请求进入 AgentLoop，不能借此安装插件或扩大权限", "管理员信任操作必须匹配插件清单的精确 SHA-256；能力、工具、权限和加载错误都可检查，撤销后立即退出运行工具集"] },
      { name: "用户自己的 Skills", items: ["设置新增 Skills，支持 ZIP、HTTPS 网站、GitHub，以及从 Codex、Claude Code 或 Agent Skills 兼容目录导入", "个人 Skill 按账号隔离并渐进加载到 Chat；导入内容只作为指令数据，不执行附带脚本，也不会自动获得文件、网络或工具权限", "压缩包限制文件数、单文件与总解压体积并拒绝加密项；网站导入只接受经过逐跳校验的公开 HTTPS 地址"] },
      { name: "验证边界", items: ["新增个人 Skill 越权隔离、插件请求级过滤、压缩包资源限制、普通用户资料范围与画布联动回归", "插件中心只展示当前服务器真实清单，本版不虚构第三方商店安装或 OAuth 已接通", "外部服务账号权限、公开网络稳定性、代码签名和安装包信誉仍需对应发布环境验收，不能由本地测试代替"] },
    ] },
    { version: "V570", date: "2026-07-27", title: "统一模型运行时、主流 API 能力契约与 Agent 成本收敛", sections: [
      { name: "主流模型不再各走各的旁路", items: ["OpenAI、Azure OpenAI、Anthropic、DeepSeek、Gemini、百炼、智谱、Moonshot、火山方舟、百度千帆、腾讯混元、MiniMax、Mistral、xAI、Cohere、Groq、OpenRouter、SiliconFlow、Together、NVIDIA NIM 与本地 OpenAI-compatible 服务进入同一 Provider 契约", "OpenAI 新配置优先使用 Responses；历史 OpenAI 配置继续保持 Chat Completions，升级不会静默改变既有请求协议", "模型 ID 来自管理员填写或当前账号接口读取；服务商预设不代表账号拥有该模型，也不根据模型名称猜测推理、视觉或并行工具能力"] },
      { name: "Chat、RAG 与长任务共用一次运行准入", items: ["Fast、Auto、Deep 统一决定直接回答、资料检索、单 Agent、工作流或受限多 Agent，不再让每一层重复放大任务", "规划、并行 Agent、循环次数、工具调用次数、执行次数和工具 Schema 大小使用同一请求级预算；简单问答不会因为打开自动模式就携带完整 Agent 成本", "用户自有模型只有能力契约满足时才优先；候选池严格限制为本人模型与系统默认模型，不能读取或使用其他用户的加密 API Key"] },
      { name: "推理、缓存和用量按协议真实适配", items: ["Responses 推理强度与回答详细度、DeepSeek 推理强度、Anthropic 显式思考预算均由具体模型配置启用；未知厂商参数默认不发送", "用量归一化保留输入、输出、推理、缓存命中和缓存写入明细，明细不会重复计入总 Token", "模型目录使用短时服务端缓存并在新增、修改、删除或切换默认模型时立即失效，减少每轮重复读取数据库"] },
      { name: "管理与验证边界", items: ["管理员可声明快速、均衡、深度路由角色、上下文上限和模型能力，并可从支持的账号接口读取精确模型 ID", "普通用户也能选择完整服务商与协议，不需要把 OpenAI-compatible 地址当作唯一入口", "本地回归只证明适配器、能力路由、所有者隔离和界面契约；真实账号的模型权限、费率、限流与公网稳定性仍需用对应服务商账号验收"] },
    ] },
    { version: "V550", date: "2026-07-26", title: "统一工作空间、跨端事实同步与面向用户的工作入口", sections: [
      { name: "一个工作域，不再让用户理解内部模块", items: ["新增 hashmm.workspace.v2 与 hashmm.workspace-run.v2 用户投影；Chat、项目、长任务、浏览器、电脑操作、多智能体和成果继续复用同一 WorkRuntime，不创建第二套执行引擎", "桌面侧栏收敛为今天、项目、资料库和我的设备；普通输入默认只显示自动入口，高级方式仍可按需展开，管理员能力继续保留在管理界面", "项目以目标、交付物、成功条件和权限方式组织工作；个人工作空间聚合本人所有项目，具体项目仍是严格的 owner 边界"] },
      { name: "状态、证据和完成语义统一", items: ["提供方和执行器状态映射为统一生命周期；未知状态失败关闭为受阻，导入的历史记录保持已观察，不能升级为已完成", "成果、来源、主张和验证只投影已有因果边；模型文字不能创建执行回执，也不能把 HTTP 成功或页面存在当作任务完成", "用户验收、退回、暂停、继续、取消和重试继续使用命令幂等键与 expected_revision，陈旧页面不能覆盖新状态"] },
      { name: "桌面端与 App 使用同一事实源", items: ["桌面端使用带 ETag 的私有工作空间快照、Bearer SSE 增量通知和定期条件校验；访问令牌不进入 URL", "已验证快照进入账号隔离的加密本地缓存，离线时可恢复最近状态；schema、账号边界或信任字段不匹配会拒绝缓存", "App 优先读取同一 v2 工作空间，把需要处理和最近工作投影为移动端控制面；只有服务端明确不支持 v2 时才回退旧接口，其他错误不会伪装成功"] },
      { name: "可验证边界", items: ["新增统一状态机、上下文隔离、证据投影、项目归属、个人聚合、ETag/304、不可枚举 404、桌面加密缓存和跨端 UI 契约回归", "上下文编译明确分隔外部页面、上传文件、检索结果等不可信数据；持久清单只保存摘要、边界和哈希，不保存来源正文或密钥", "真实公网多设备、对称 NAT、自有 TURN、24 小时稳定性、Windows Authenticode 与 Android 商店签名仍需真实发布环境验收，本地测试不能替代这些结论"] },
    ] },
    { version: "V518", date: "2026-07-26", title: "面向用户的项目工作区、运行中补充与跨端例行工作", sections: [
      { name: "项目不再只是分类标签", items: ["项目现在包含用户明确填写的目标、交付物、成功条件和工作权限；这些内容进入对应 Chat 的上下文，但不能扩大文件、网络或工具权限", "项目对象按账号隔离并带修订号；桌面端可从统一搜索直接打开项目，已打开的工作台会同步切换，不再只改本地存储", "工作台冷启动使用同一用户快照取得项目、工作记录、待处理事项、例行任务、设备与信任状态，后续仍以服务端工作账本为准"] },
      { name: "运行中的对话可以补充真实附件", items: ["模型生成期间可继续发送文字或已上传文件；服务端只接受当前账号、当前对话工作区内的安全文件名，并重新计算 SHA-256", "附件变更、路径越界、摘要不一致或数量超限会被拒绝；外部文件内容继续作为不可信数据处理", "运行中补充会写入同一会话消息和 AgentLoop 转向队列，不再生成与主任务割裂的临时演示状态"] },
      { name: "用户例行任务与 App 权限分层", items: ["普通用户通过自己的例行工作接口创建、运行、停用和删除资料摘要、知识检查或每日工作简报，不再错误调用管理员定时任务接口", "每项例行工作绑定账号；即使管理员使用普通用户入口，也不能把自己的例行任务绑定到另一账号的对话", "App 根据签名登录令牌中的角色决定是否显示管理能力；普通用户看到工作与控制入口，管理员仍保留模型、质量、团队、用量和高级治理页面"] },
      { name: "检索、证据和发布边界", items: ["侧栏统一搜索覆盖当前账号的对话、项目和工作记录，不返回其他账号的数据", "浏览器证据、画布关联、内容变化传播、成果版本和完成凭据继续使用同一 WorkRuntime 事实链；本轮回归验证未把页面存在当成功能完成", "本轮没有宣称真实公网多设备、对称 NAT、24 小时稳定性或 Authenticode 已通过；这些仍须在发布环境单独验收"] },
    ] },
    { version: "V498", date: "2026-07-26", title: "统一工作契约、租户证据边界与跨端工作方式", sections: [
      { name: "Chat 与长任务使用同一份工作契约", items: ["自动、浏览器和电脑操作从输入框进入同一个 WorkRun，不再由孤立页面维护第二套状态", "路由、上下文预算、恢复、证据、产物、协作与完成条件由服务端按运行事实编译，模型文字不能自行扩权", "契约修订进入桌面工作画布和 App 工作画布；缓存契约被篡改或缺字段时降级为需要重新准备"] },
      { name: "RAG 与 Graph Engineering 按账号隔离", items: ["向量、BM25、查询扩展、图谱回接和最终上下文在交给模型前执行同一 owner 边界", "历史无归属切片不会自动成为所有用户共享资料；管理员视图与普通用户检索使用不同投影", "知识库缓存键包含权限作用域，权限异常按拒绝处理，不用空列表伪装成功"] },
      { name: "浏览器、电脑操作与多 Agent 形成证据闭环", items: ["浏览器选区和页面读取可进入证据图并关联画布；外部页面始终作为不可信数据处理", "Computer Use 只记录真实设备上报，未取得验证回执时不会显示为已经核验", "多 Agent 统一经过所有者、能力、预算、隔离和验证准入；未知 MCP 工具与关键 Hook 异常默认拒绝"] },
      { name: "桌面端与 App 分工清晰", items: ["桌面端负责本机浏览器、文件、终端和电脑操作；App 负责查看、确认、接力与远程控制，不复制桌面诊断后台", "两端读取同一工作投影、修订和完成状态，App 的工作方式会随 Chat 请求进入服务端", "能力不可用时显示真实缺口，不生成演示数据，也不把页面存在当成功能已经接通"] },
    ] },
    { version: "V389", date: "2026-07-22", title: "管理操作真实回执与 App 动态来源隔离", sections: [
      { name: "模板不再用删除模拟编辑", items: ["修复模板新建调用不存在数据库方法的问题，新建成功必须返回模板 ID 和对象回执", "编辑使用原地 PATCH，保留模板 ID、创建时间和使用次数；更新失败时原模板保持不变", "模板名称、分类、变量和对象 ID 经过有界校验，缺失对象不会显示成删除或保存成功"] },
      { name: "工具、技能与 MCP 不再假成功", items: ["工具分类开关逐项等待服务器结果，只更新成功项，失败项保持原状态并显示成功/失败范围", "技能、自定义 API 工具和 MCP 连接保留最近一次已验证列表，读取失败不会被渲染成真实空态", "保存、反馈、刷新和删除都有忙碌状态与明确回执；网络错误不会自动关闭编辑内容"] },
      { name: "App 动态页按来源独立恢复", items: ["实时工作、用量、动态流和能力状态使用 supervisor 并行刷新，一个来源异常不会取消其他来源或让首屏一直加载", "零请求、零 token 是合法的已验证用量，不再因为数值为零隐藏整块信息", "刷新失败保留上次成功结果，并将实时订阅、用量、动态流和能力错误合并成可重试的部分验证提示"] },
    ] },
    { version: "V388", date: "2026-07-22", title: "运行能力实证、文档安全回执与跨服务缓存隔离", sections: [
      { name: "能力状态来自当前运行服务", items: ["画布和长任务只有在依赖、开关与对应 API 路由均已挂载时才显示可用，不再把代码存在误报为运行中", "管理总览按四个数据源分别显示本次已验证数量；权限、网络或服务错误不会被零值覆盖", "App 区分本次验证、最近缓存和离线旧状态，最近可用不等于当前服务已经接通"] },
      { name: "文档工坊使用受限上传与内容回执", items: ["上传采用流式大小限制、安全文件名、扩展名白名单、临时文件清理和原子落盘，空文件或超限文件不会进入知识库", "上传成功返回实际字节数与 SHA-256；批量重解析在未收到服务端回执时明确标记结果未知，不虚构后台仍在处理", "文档列表不向界面发布服务器绝对路径，删除、查看与重解析的文档 ID 都先拒绝路径穿越和控制字符"] },
      { name: "缓存与刷新不会跨服务串写", items: ["App 能力快照同时绑定账号和后端地址指纹，切换服务器后不会复用上一套服务的 ETag 或可用状态", "工作台合并并发刷新并取消过期请求，较慢的旧响应不能覆盖较新的强制刷新结果", "桌面管理页和文档页保留最后一次成功数据并明确其时间，失败后提供重试，而不是把旧数据包装成本次成功"] },
    ] },
    { version: "V387", date: "2026-07-22", title: "远程质量事实、Office 修订回执与增量文件缓存", sections: [
      { name: "远程连接不再只有在线状态", items: ["桌面端和 App 从真实 WebRTC 统计中展示延迟、丢包、可用带宽与帧率，并区分稳定、波动、受限和未测得", "桌面端只接收受信任远程窗口上报的有界指标，不跨 IPC 暴露候选地址、SDP 或原始 RTCStats", "质量快照可显式交给 Chat 诊断；Chat 只基于已测指标给出建议，不把未知原因说成事实"] },
      { name: "Office 保存形成可验证修订", items: ["通过系统文件关联与已安装的 Microsoft Office、WPS、LibreOffice 或 vivo 办公套件协作，不调用闭源私有协议", "本地读取、服务器提交和修订确认使用同一 SHA-256 回执；读取后再次保存会报告冲突，不能误标为已同步", "Office 结构验证通过后才覆盖当前会话文件，成功同步后 Chat 下一轮读取同一任务中的新修订"] },
      { name: "文件只在变化时传输新数据", items: ["会话文件列表使用账号作用域持久缓存秒开，并通过 ETag 条件请求校验；未变化返回 304，不重复传输列表正文", "服务端文件列表不再泄露本机绝对路径，只返回用户界面需要的名称、大小、时间与下载地址", "文件预览继续使用已有内容缓存；Office 同步后只失效对应会话和文件的预览"] },
    ] },
    { version: "V376", date: "2026-07-22", title: "文档产物闭环与 App 逐页真实状态", sections: [
      { name: "文档工坊产物真正进入画布", items: ["生成文件使用安全叶文件名并在会话工作区原子写入，不再因动作名含路径分隔符落盘失败", "保存失败明确返回错误；保存成功返回结构化 artifact，桌面端直接打开右侧画布，不把整段 HTML 当普通回答铺开", "App 的设计海报、幻灯片和信息图从真实需求启动，不再强制上传无关文件，结果回到同一会话继续处理"] },
      { name: "App 页面不再把失败伪装成空数据", items: ["智能体成员库、文档动作、持续任务和自动规则区分加载、真实空态、权限错误、旧服务和网络失败", "知识图谱把尚未构建实体与读取失败分开；失败可重试，规则写失败会回滚显示状态", "工作总览保留最后一次成功状态，并按活跃、空闲、离线自适应同步"] },
      { name: "缓存、轮询与画布信任边界", items: ["手机照片请求长期空闲时逐步退避，出现请求后立即恢复短间隔，减少日志中的无效高频读取", "画布只允许同一 HashMM 服务来源的产物进入带原生保存桥的 WebView，外站 scheme、host 或 port 变化在加载前拒绝", "逐页能力审计记录页面事实源、真实动作、缓存方式和明确限制，不以页面存在代替功能完成"] },
    ] },
    { version: "V375", date: "2026-07-22", title: "同账号工作接力、Office 产物检查与 RAG 诊断", sections: [
      { name: "手机与桌面使用同一运行事实", items: ["桌面运行器按账号和设备登记在线状态，App 不再把远程控制主机数误当作桌面任务执行器", "登录令牌更新后桌面立即重新登记；长任务心跳同时刷新运行器在线状态，App 在前台按增量节奏校验", "历史对话只投影为已观察记录，不伪造执行步骤、验证结果或完成状态"] },
      { name: "Office 能力进入 Chat 和文档工坊", items: ["Word、PPT、Excel 使用应用内结构解析器检查文件，不要求用户安装或操作命令行工具", "桌面端可通过系统文件关联交给已安装的办公应用，检测修改后由用户显式同步回当前会话；同步前再次验证文件结构", "App 通过 Android 标准 MIME 交接到 vivo 办公套件等已注册本机应用，不依赖闭源包名或私有协议"] },
      { name: "可核验的质量与 RAG 诊断", items: ["设计产物增加离线可渲染、标题、语言、视口、图片替代文本和表单标签检查；不根据静态源码虚构视觉审美结论", "运行清单新增只基于观测证据的 RAG 风险诊断，明确区分已观察问题和当前不可评估项", "新工具、诊断、运行器和跨账号边界均加入回归测试，并继续经过前端、后端、App 与原生发布门"] },
      { name: "远程连接的生产边界", items: ["信令断开按有界退避重连，旧连接代次不会重复调度；认证失败等待桌面登录令牌刷新后恢复", "隐私状态以桌面主进程观测值为准，只承诺远端内容保护，不把视频遮罩误称为物理屏幕熄灭", "安装包不再携带第三方公共 TURN 账号；复杂 NAT 或企业网络需要管理员配置自己的 TURN 服务"] },
    ] },
    { version: "V374", date: "2026-07-22", title: "可恢复长任务控制与副作用边界", sections: [
      { name: "长任务不再只有状态展示", items: ["统一工作运行时新增 hashmm.work-control.v1 与 hashmm.work-command.v1，暂停、继续、停止和重新派发由服务端真实执行器决定", "每次控制包含命令幂等键和 expected_revision，陈旧页面不能覆盖新状态，网络重试不能重复执行同一个副作用", "桌面右侧运行面板与 App 运行轨迹读取同一可用动作，不支持的动作不会显示按钮"] },
      { name: "按执行器保留真实边界", items: ["Loop 保存检查点后协作式暂停并可恢复；多 Agent 等当前模型调用返回后停止，不宣称硬杀模型调用", "Browser Use、Computer Use 和跨端文件任务只允许在桌面端领取前原子取消，领取后明确拒绝伪取消", "多 Agent 重新运行会创建有链接的新运行，原任务和事件完整保留用于审计"] },
      { name: "断线安全与跨端缓存", items: ["命令先持久化再分派；进程在外部副作用后中断时标记为结果待确认，系统不会自行重放", "命令、事件与结果只保存有界状态和关联 ID，不保存原始工具参数、文件正文、凭证或模型思考", "App 将控制后的运行与事件写回账号隔离缓存，桌面端和 App 均在 revision 变化后重新校验"] },
    ] },
    { version: "V373", date: "2026-07-22", title: "统一工作运行时与跨端任务账本", sections: [
      { name: "Chat 与长任务共用一个事实源", items: ["普通问答、持久长任务、多 Agent、Browser Use、Computer Use 和跨端文件任务进入 hashmm.work-run.v1", "任务生命周期以单调游标和事件序列增量同步，不再由桌面端和 App 分别猜测状态", "运行账本只保存有界阶段、证据摘要和交付状态，消息正文仍由会话系统负责"] },
      { name: "真实跨设备闭环", items: ["App 下发电脑或浏览器任务后立即生成统一运行记录，桌面端领取、完成或失败会推进同一条记录", "App 工作台、运行与桌面右侧上下文读取同一服务端运行状态，并使用账号隔离的加密本地快照", "离线快照会明确标识为缓存；数据变化后才通过游标和 ETag 增量拉取"] },
      { name: "隔离、证据与恢复", items: ["任务和事件按账号所有权查询，缺失与越权对象使用同一不可枚举响应", "原始工具参数、凭证、文件正文、token 流和思考文本不进入统一账本", "旧长任务可回填为可观察运行记录，但恢复不会重放工具、副作用或自动扩大权限"] },
    ] },
    { version: "V372", date: "2026-07-22", title: "证据门控完成与真实用户验收", sections: [
      { name: "完成不再靠模型自评", items: ["任务契约、确定性检查、证据图、下一工作集、工具轨迹和 Agent 轨迹共同形成 hashmm.completion-gate.v1", "只有全部必需条件闭环且运行已终止时才允许显示已验证；语言流畅、模型高分或 HTTP 200 都不能替代证据", "失败工具、未结束调用、失败 Agent、重复调用、开放工作项和权限缺口会形成明确失败模式与最小下一动作"] },
      { name: "用户验收闭环", items: ["用户写下的验收标准只能由任务所有者本人接受或退回，独立评估模型不能代签", "桌面端与 App 对同一持久任务保存同一验收记录，并重新计算任务图、下一工作集和完成状态", "接受或退回不会恢复任务、执行工具或扩大文件、网络、会话和子 Agent 权限"] },
      { name: "Chat、长任务与多 Agent", items: ["Chat 统一上下文把完成门放在任务图与运行轨迹之前，明确区分运行检查通过和最终完成", "长任务和多 Agent 共用同一协议；多 Agent 以角色真实交付为标准，不虚构工具要求", "质量看板统计已验证、带限制交付、未闭环、受阻、重复调用和不安全完成声明"] },
    ] },
    { version: "V371", date: "2026-07-22", title: "图驱动执行前沿与下一工作集", sections: [
      { name: "下一工作集", items: ["任务图的每个真实阻塞都会生成期望状态、观察状态、有界运行邻域和最小动作", "路线只由服务端工具注册表、持久执行范围与网络策略计算，模型选择路线计数固定为零", "本地知识检索优先于开放网络；没有权限或工具时如实显示等待输入或范围受限"] },
      { name: "长任务与多 Agent", items: ["持久长任务每轮重新调和阻塞项并记录新增、关闭与收敛状态，不盲目重复原始提示", "失败分工只能在原权限内重试或由主 Agent 接管，不能借助重新分工获得更多工具", "Chat、长任务和团队状态共用 hashmm.execution-frontier.v1，不再维护彼此割裂的演示数据"] },
      { name: "桌面端、App 与审计", items: ["右侧统一上下文、任务与进度、Chat 完成卡、智能体工坊和总控中枢显示真实下一工作集", "质量看板区分路线覆盖、权限受限和完整性异常，不把可行路线当成正确率或执行证据", "新增 GitHub 参考审计，记录压缩包哈希、许可证、实际借鉴位置和明确未实现的边界"] },
    ] },
    { version: "V370", date: "2026-07-21", title: "任务证据图与跨端闭环", sections: [
      { name: "任务证据图", items: ["目标、完成标准、权限范围、来源、主张、工具、Agent、产物和校验进入同一份确定性协议", "只根据真实运行清单连接边，不允许模型补写执行证据或扩大权限", "缺少来源、失败工具、未交付产物和未通过校验都会形成阻塞项与下一步"] },
      { name: "长对话与长任务", items: ["短句继续会依据上一轮目标和未清阻塞项补充检索，但新的具体问题始终以用户本轮输入为准", "持久循环每轮重建证据图，并把阻塞项作为下一轮修复输入", "旧任务恢复时只读取已有状态，不重放工具或制造完成记录"] },
      { name: "多 Agent 与跨端", items: ["团队角色、共享来源、主张、画布产物和验收状态汇入同一任务图并写回 Chat", "桌面端上下文、长任务页和质量看板读取真实服务端协议", "App 的对话完成卡、工作台、多 Agent 和质量页读取同一图，不再维护演示状态"] },
    ] },
    { version: "V369", date: "2026-07-21", title: "持久任务范围与可审计检索契约", sections: [
      { name: "长任务", items: ["创建时保存目标、完成标准、会话边界、工具、联网与子 Agent 范围", "重启恢复沿用原任务范围，不重新扩大权限", "计划未闭合、工具失败或交付缺失时不会只凭模型自述判定完成"] },
      { name: "多 Agent 与权限", items: ["子 Agent 继承主任务的用户、会话和更窄工具集合", "子 Agent 同样经过参数绑定权限检查，不再用角色白名单替代运行时授权", "联网默认关闭，可按任务允许公开网络或指定站点"] },
      { name: "RAG 证据", items: ["检索返回候选数、证据数、可引用数、来源方法、重排方法与图扩展统计", "Chat 工具和运行轨迹显示同一份检索契约", "契约只报告可观测证据，不让模型给自己的答案打通过分"] },
    ] },
    { version: "V368", date: "2026-07-21", title: "能力契约与真实工作流接线", sections: [
      { name: "Agent 与 Chat", items: ["主 Agent 和后台 Worker 共用经过模块开关过滤的真实工具清单", "浏览器、文档、画布、长任务、MCP、Hook 与多 Agent 状态由后端统一判定", "缺少执行器或配置时明确标为待配置、部分可用或未接通"] },
      { name: "数据与安全", items: ["图片检索进入 Chat 工具链并按账号隔离", "未登录上传在读取文件内容前拒绝", "能力接口支持私有缓存、ETag 增量校验且不返回密钥"] },
      { name: "跨端体验", items: ["桌面高级能力页显示实际进入工作流的工具与缺口", "App 动态和工作台依据同一能力契约与电脑在线状态开放操作", "手机端保留加密能力快照，短时复用并在变更时重新校验"] },
    ] },
    { version: "V367", date: "2026-07-21", title: "图工程证据链与桌面启动可靠性", sections: [
      { name: "RAG 与 Chat", items: ["知识图谱命中的实体和关系按 chunk_id 回接原始文档切片", "图关联证据执行数量上限、文档权限过滤与来源审计", "桌面端和 App 的来源卡可识别真实图关联证据"] },
      { name: "模型服务", items: ["兼容旧服务的数组响应与新服务的 models、providers 包装响应", "异常或缺字段响应安全归一为空列表，不再导致 AI 服务页面崩溃", "快速模型列表与管理页面共用同一数据边界"] },
      { name: "桌面体验", items: ["新进程默认关闭统一检查器，浏览器、文件、画布和上下文操作仍可按任务打开", "图工程状态展示真实后端就绪状态、证据上限和回接方式"] },
    ] },
    { version: "V362", date: "2026-07-19", title: "文档工作流与本地优先同步", sections: [
      { name: "文档工坊", items: ["修复会话文件上传地址与工作区目录导入错误", "上传、处理、画布产物与当前对话使用同一个会话空间", "处理结果写回 Chat，桌面端和 App 可继续追问"] },
      { name: "跨端同步", items: ["桌面会话同步改用账号级增量游标", "App 动态与工作台共享加密本地快照、短时请求合并和 ETag 校验", "云端已变更的标题、置顶和归档状态可覆盖本地缓存"] },
      { name: "界面", items: ["设置页移除厚重头部卡片，改为紧凑标题区", "浏览器助手窗口统一为主界面的浅色标题栏和控件风格"] },
    ] },
    { version: "V361", date: "2026-07-19", title: "文件预览与会话可靠性", sections: [
      { name: "文件", items: ["Word、PDF、PPT 右栏预览按会话隔离", "已读取预览持久缓存，文件未变化时复用本机内容", "PPT 和运行记录缺字段时不再导致整页崩溃"] },
      { name: "对话", items: ["输入草稿按账号和会话分别保存", "长任务运行记录补齐兼容保护和停止原因展示"] },
    ] },
    { version: "V360", date: "2026-07-19", title: "浏览器、画布与多 Agent 工作区", sections: [
      { name: "工作区", items: ["链接可在右栏受控浏览器打开", "画布、文件、运行证据共用右侧检查器", "多 Agent 成员保留稳定线程身份和父任务关系"] },
    ] },
  ];
  const [selected, setSelected] = useState(releases[0].version);
  const active = releases.find(r => r.version === selected) || releases[0];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ releaseNotesOpen: false }); }}
    >
      <div
        className="w-[780px] max-w-[calc(100vw-32px)] h-[610px] max-h-[86vh] flex flex-col rounded-2xl anim-fade-up overflow-hidden"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        <div className="flex items-center justify-between px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div><h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>发行说明</h3><p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>只记录已经接入并通过验证的用户可见变化</p></div>
          <button onClick={() => set({ releaseNotesOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        <div className="flex flex-1 min-h-0">
          <aside className="w-[218px] flex-shrink-0 p-3 overflow-y-auto" style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
            {releases.map(r => <button key={r.version} onClick={() => setSelected(r.version)} className="w-full text-left px-3 py-3 rounded-xl mb-1.5 transition-colors" style={{ background: selected === r.version ? "var(--bg-primary)" : "transparent", border: selected === r.version ? "1px solid var(--border)" : "1px solid transparent" }}><div className="flex items-center gap-2"><span className="text-[12.5px] font-semibold" style={{ color: selected === r.version ? "var(--accent)" : "var(--text-primary)" }}>{r.version}</span>{r.current && <span className="text-[9.5px] px-1.5 py-0.5 rounded" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>当前</span>}</div><div className="mt-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{r.date}</div><div className="mt-1 text-[11px] leading-4" style={{ color: "var(--text-secondary)" }}>{r.title}</div></button>)}
          </aside>
          <div className="flex-1 overflow-y-auto px-7 py-6">
            <div className="flex items-start justify-between gap-4 pb-5" style={{ borderBottom: "1px solid var(--border)" }}><div><div className="text-[20px] font-semibold" style={{ color: "var(--text-primary)" }}>{active.version}</div><div className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>{active.title}</div></div><span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{active.date}</span></div>
            <div className="mt-5 space-y-6">{active.sections.map(section => <section key={section.name}><h4 className="text-[12px] font-semibold mb-2.5" style={{ color: "var(--text-primary)" }}>{section.name}</h4><div className="rounded-xl px-4" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>{section.items.map((item, index) => <div key={item} className="flex items-start gap-3 py-3 text-[12px] leading-5" style={index < section.items.length - 1 ? { borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" } : { color: "var(--text-secondary)" }}><CheckCircle2 size={14} className="mt-0.5 flex-shrink-0" style={{ color: "var(--accent)" }} /><span>{item}</span></div>)}</div></section>)}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function BugReportModal() {
  const set = useStore((s) => s.set);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [submitted, setSubmitted] = useState(false);

  function submit() {
    if (!title.trim()) return;
    // Save to localStorage as a simple bug report log
    const reports = JSON.parse(localStorage.getItem("hmm_bug_reports") || "[]");
    reports.push({ title, desc, ts: Date.now() });
    localStorage.setItem("hmm_bug_reports", JSON.stringify(reports));
    setSubmitted(true);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ bugReportOpen: false }); }}
    >
      <div
        className="w-[480px] rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>报告错误</h3>
          <button onClick={() => set({ bugReportOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {submitted ? (
          <div className="px-6 py-10 text-center">
            <div className="mb-3 flex justify-center"><CheckCircle2 size={36} style={{ color: "#16a34a" }} /></div>
            <div className="text-[14px] font-medium" style={{ color: "var(--text-primary)" }}>感谢反馈！</div>
            <div className="text-[12px] mt-1" style={{ color: "var(--text-tertiary)" }}>我们会尽快处理</div>
            <button onClick={() => set({ bugReportOpen: false })}
              className="mt-4 px-5 py-2 rounded-xl text-[13px] font-medium text-white" style={{ background: "var(--accent)" }}>
              关闭
            </button>
          </div>
        ) : (
          <div className="px-6 py-4 space-y-3">
            <div>
              <label className="block text-[12px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>问题标题 *</label>
              <input value={title} onChange={(e) => setTitle(e.target.value)}
                placeholder="简要描述你遇到的问题"
                className="w-full h-10 px-3 rounded-xl text-[13px] outline-none"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
            <div>
              <label className="block text-[12px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>详细描述</label>
              <textarea value={desc} onChange={(e) => setDesc(e.target.value)}
                placeholder="复现步骤、截图说明、期望行为..."
                rows={5} className="w-full px-3 py-2.5 rounded-xl text-[13px] outline-none resize-none leading-relaxed"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => set({ bugReportOpen: false })}
                className="px-4 py-2 rounded-xl text-[13px]" style={{ color: "var(--text-secondary)" }}>取消</button>
              <button onClick={submit} disabled={!title.trim()}
                className="px-5 py-2 rounded-xl text-[13px] font-medium text-white disabled:opacity-50"
                style={{ background: "var(--accent)" }}>提交</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
