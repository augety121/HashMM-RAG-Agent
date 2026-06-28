# HashMM 本地模型目录

对标 Marvis 的 `models/`。把 onnx 模型文件放这里，HashMM 会按硬件能力（显存/内存）
自动决定本地推理还是回退云端（见 model-manager.js 的 decideExecution，对标 device_match）。

内置 OCR 模型槽位（放入对应文件即启用本地 OCR，否则回退云端）：
- `dbnet.onnx`        文本检测
- `crnn_lite_lstm.onnx`  文本识别

真机打包时，把 onnx 文件放进 resources/models/（onnxruntime 由真机集成）。
沙箱不含这些大文件，model-manager 会优雅降级（ready=false → 云端）。
