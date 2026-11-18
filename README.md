# 声句工坊 DictionForge

中文本地 GPT-SoVITS v4 数据整理、微调与语音合成工作台。

默认地址 [本机工作台](http://127.0.0.1:7867)。数据与模型保存在独立的 `data/`，原始录音只读。

实施规格见 [项目计划](docs/PROJECT_PLAN.md)，实际完成情况见 [阶段进度](docs/CODEX_PROGRESS.md)。未通过真实验收的能力不得视为已交付。

首次使用双击 `准备DictionForge.cmd`；准备完成后双击 `启动DictionForge.cmd`，浏览器会自动打开。重复启动复用同一实例；先暂停训练或等待任务结束，再双击 `停止DictionForge.cmd`。准备需要网络和足够磁盘；已准备好的模型可离线运行。

支持 Windows x64、NVIDIA CUDA 显卡；实测 RTX 5080 16GB，driver 621.01、Torch 2.9.0+cu128、Python 3.12.10、FFmpeg 8.1.2。Python、uv、Node、pnpm、Git、FFmpeg 都装在项目的 `vendor/tools/`，不借用 TimbreForge 环境。依赖、上游源码、补丁、模型资产和工具包由 `configs/` 与前端锁文件固定。

使用操作见 [使用手册](docs/USER_GUIDE.md)，问题定位见 [排障](docs/TROUBLESHOOTING.md)，真实结果与边界见 [验收报告](docs/ACCEPTANCE_REPORT.md)，本机状态见 [交接记录](docs/CODEX_HANDOFF.md)。
