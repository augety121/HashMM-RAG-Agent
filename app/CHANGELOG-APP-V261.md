# App V261 —— 构建错误修复（Unresolved reference 'clip'）

- **修复构建失败**：`WorkbenchScreen.kt:185` 报 `Unresolved reference 'clip'`。
  根因：V260 工作台加载页的进度条用了 `Modifier.clip(RoundedCornerShape(3.dp))`，
  但文件没导入该扩展函数。已补 `import androidx.compose.ui.draw.clip`。
- 全 App 扫描同类漏导入（clip / alpha / graphicsLayer / Brush）：仅此一处真实缺失
  （TextureVideoRenderer 的 graphicsLayer 命中为注释文字，非代码）。
- 配套说明：客户端 V269 的「离线模式」全套（后端不启动照常进界面、历史/云端记录
  可看、恢复自动重连）对 App 内嵌工作台同样生效——WebView 装载的是同一份 webui，
  升级服务器端后 App 内工作台即获得离线能力。

versionCode 75→76，versionName 1.10.34→1.10.35。
