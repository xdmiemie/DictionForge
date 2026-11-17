# 架构

FastAPI 同源服务 React 中文 UI；SQLite WAL 保存任务、单调事件、幂等键及版本记录。独立 worker 使用进程身份和文件租约串行处理 GPU 工作；CPU 可读扫描不占 GPU。

上游固定到 v4 tag，训练与推理通过适配层运行。资产、原始录音副本和产物不进入 Git。任务提交冻结配置；数据与模型版本不可变；只允许清理数据根目录内受管理、无引用的文件。

所有真实模型能力以 docs/ACCEPTANCE_REPORT.md 中实际证据为准。
