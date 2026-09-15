# HashMM 桌面端覆盖包 V249（打在 V248 完整源码上）

## 怎么打
    unzip -o HashMM-桌面端覆盖包-V249.zip -d <你的源码根>
只覆盖下列文件，其余一概不动；之后按你原来的方式用 installer-native 重新打包/重启即可。
**前端有改动**（frontend-next 两个文件 + 新增一个 lib），重打包时会随构建生效。

## 本包内容（13 个文件 + 2 个文档）
### 修复
- `hashmm/api/supabase_auth.py` —— App 端管理员身份丢失根因修复（_verify_remote 透传
  app/user_metadata；admin 判定三选一：邮箱白名单 / app_metadata.role / user_metadata.role）。
  这就是"定时任务显示要管理员"的病根。
- `frontend-next/lib/canvasBridge.ts`（新）+ `frontend-next/components/ArtifactPanel.tsx`
  —— 画布工具条 主色/字号 点了没反应的根因修复：AI 生成的画布 HTML 里没有 canvas.js，
  wc:theme 等消息无人接收。现在塞 srcDoc 前自动注入运行时协议桥（主题/编辑/立即保存/
  答案插入全部在 AI 画布上复活）；官方模板零注入互不打架。
- `skills/packs/work-canvas/assets/canvas.js` —— 字号 body 内联双写（AI 页 body 定死 px
  时只改 :root 会被盖掉）+ --wc-accent 别名。

### 新能力（详见 docs/借鉴设计-V249.md，写明四个开源项目各借了什么）
- `hashmm/llm_failover.py` + `hashmm/api/model_manager.py`（出口一行接入）
  + `hashmm/api/routes/model_route.py` —— 模型容灾链 + 每模型熔断器（OmniRoute 借鉴）。
  配置：PUT /api/admin/model-route/fallbacks {"ids":["模型id",...]}；健康：GET .../health。
  不配置＝零变化。
- `hashmm/memory/hub.py` + `hashmm/api/routes/user_memory.py`（追加 3 端点）
  —— 统一记忆中枢：GET /api/memory/recall 四路联邦召回；执行回写钩在
  `hashmm/api/routes/dispatch.py` 的 /complete（cognee / codebase-memory 借鉴）。
- `hashmm/agent/team.py` + dispatch kind=`team` + `frontend-next/components/desktop/AdvancedView.tsx`
  —— 多智能体协作：并行角色 + 画布控制室四色直播 + 汇总回帖（herdr 借鉴）。
- `hashmm/api/routes/__init__.py` —— 新路由注册。

## 验证
全部 .py 过 py_compile；canvas.js 与注入桥脚本过 node --check；前端两处改动与 V248
原版括号基线一致。容器内无法起完整服务，请在真机回归：画布主色/字号、派 kind=team、
GET /api/memory/recall、配 fallbacks 后拔掉主模型 key 观察自动切换。
