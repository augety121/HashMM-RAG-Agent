# HashMM App V248 —— 原生工作画布（配套桌面端完整源码 V250）

## 原生工作画布（桌面画布体验的手机适配）
- 动态页「最新产物」点开 **.html 文件** → 全屏画布屏（其余类型仍走原预览）：
  - 工具条与桌面画布同一心智、Marvis 面貌：**主色五点**（选中墨黑外环）·
    **字号 A− / A＋**（11–20）· **编辑 chip**（选中＝墨黑实心白字，退出自动落盘）·
    脏态出现墨黑「保存」胶囊；页头右侧 保存中转圈 / 已保存✓ / 失败红字。
  - WebView 注入 App 桥：`--accent`/`--wc-accent` 变量 + html/body 双写字号 +
    contentEditable 就地编辑 + JavascriptInterface 回传整页保存——
    **与桌面画布读写同一后端文件**，手机改完桌面刷新即见（反之亦然）。
- 新增 `data/remote/CanvasRepository.kt`（读 download_url / PUT files 落盘，
  base()/token 同范式）与 `ui/canvas/CanvasScreen.kt`；FeedFile 补 `downloadUrl` 字段。

## 说明
桌面端本轮为 **完整源码包 V250**：智能体接上记忆中枢（上下文注入 + memory_recall 工具）、
共享链接管理成真、图谱一键修复、Pro 假购买按钮清除——详见包内
CHANGELOG-V250-agent-brain-demo-sweep.md。
