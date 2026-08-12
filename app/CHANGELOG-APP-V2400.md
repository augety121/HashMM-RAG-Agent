# HashMM App V2400 / 9.2.0

- 远程控制升级到 `hashmm.remote.v4`，使用 V4 bootstrap、一次性 viewer ticket 与关联 WSS。
- 新增 `WAITING_FIRST_FRAME`：收到 VideoTrack 不再提前显示成功，只有 `VideoSink.onFrame` 或兼容 JPEG 解码成功才进入投屏。
- 兼容预览使用 Authorization header；8 秒无首帧结束为稳定错误，不再吞异常并无限轮询。
- 鼠标移动与滚动走无序零重传 DataChannel；点击、键盘和命令保持可靠通道。
- App 版本 `9.2.0`，versionCode `240`。真实公网直连、TURN 和 24 小时稳定性仍需发布环境双设备验收。
