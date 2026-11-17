# P02 环境与资产界面

2026-11-17，Windows 实机 `http://127.0.0.1:7867/`，CUA 内置浏览器。

| 场景 | 结果 | 实际证据 |
| --- | --- | --- |
| 环境侧栏 | PASS | 本地服务可用，端口 7867；展开 GPU 资源状态，显示 `NVIDIA GeForce RTX 5080, 621.01, 16303, 14500`（最后一列可用 MiB 随采样变化） |
| 资产清单 | PASS | 显示 24 项资产及名称，含 `s1v3.ckpt`、`gsv-v4-pretrained/s2Gv4.pth`、`vocoder.pth`、CN-HuBERT、RoBERTa 和 ASR；存在文件如实显示「待哈希验证」，不冒充已经验证 |
| 进程日志 | PASS | 选中任务 `6966480b2359483982d547c60ab1347d`，展开工作进程日志并点击「刷新进程日志」，读取到 `worker.log` 和「日志尚无内容」；64 KiB 说明可见 |
| GPU 诊断按钮 | NOT_RUN | 首轮检查避免与真实 TTS/ASR 争用 GPU。GPU 空闲及服务重启后尝试补验收时，CUA 浏览器清单变为空；原生 Edge 恢复报告 `GetCursorPos failed: 拒绝访问 (0x80070005)`，因此未触发 probe，不能声明按钮端到端通过；API 已接入 `/api/system/probe` |
| 资产准备按钮 | NOT_RUN | 资产已由本阶段环境任务准备，本次不重复下载；API 已接入 `/api/assets/prepare` |

`pnpm run build` 通过 TypeScript 与 Vite 生产构建。GPU 张量、ASR、TTS 实测由阶段环境证据另行记录；这里不重复声明。

补验收恢复尝试：原 IAB `tab.reload()` 返回 browser unavailable，`cua.getState()` 的 `browsers=[]`；根据清单选中 Edge 窗口时提示窗口最小化；通过支持的 `cua.computer.launch_app({app:'MSEdge'})` 恢复时返回上述拒绝访问。没有重复提交 GPU 任务。
