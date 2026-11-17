# 实施进度

日期：2026-11-17（Asia/Hong_Kong）。每阶段单独验收、一次提交。BLOCKED 表示对应真实验收未通过；可执行部分继续推进，禁止以夹具代替真实模型验收。

## P00 — DONE

- 初始工作区 E:\DictionForge 仅有用户提供的计划文件，无 Git 历史或已有代码改动；用户澄清：AGENTS.md 指的就是 DictionForge_Codex_Project_Plan.md，已完整读取。
- 保留原文件，逐字归档到 docs/PROJECT_PLAN.md。
- origin：https://github.com/xdmiemie/DictionForge.git；远端 ls-remote 成功返回空引用，GitHub API 核对 default_branch=main、size=0，初始化本地 main。
- 官方 20250422v4 标签解析为 6a1ece89928f6e5042a3ae0e092c2f47bdbae596。
- 实机：Windows、Python 3.10.11、RTX 5080 16303 MiB、驱动 621.01；nvidia-smi 可用。
- 网络：沙箱内 DNS 不可用，经已授权网络执行成功。
- TimbreForge 作为只读功能基线，模型、数据与虚拟环境不作修改。

## P03–P12 — NOT_STARTED

尚未实施。逐阶段替换此条，验收证据不得预填 PASS。

基线 Git 核对：TimbreForge HEAD e2884c72604766ce6d4193ebdba56f6b4c040785；已有 `D DictionForge_Codex_Project_Plan.md`，予以保留。

P00 验收：计划副本 SHA256 一致；GitHub API 与 ls-remote 核对；固定源码已读取，审计见 UPSTREAM_AUDIT.md、BASELINE_AUDIT.md。A01 PASS。

## P01 — DONE

FastAPI/React 双标签、SQLite WAL 幂等任务与事件、独立 worker、进程身份核对与排他租约、SSE 续读、错误区和诊断取消已接通。

验收：`.venv-api/Scripts/python.exe -m pytest tests/test_jobs.py -q --basetemp=data/pytest-p01-final` 4 passed；`pnpm run build` PASS。浏览器实际诊断任务 `6966480b2359483982d547c60ab1347d` 完成 4/4，刷新仍恢复同一任务、7 条事件。见 docs/evidence/P01_UI.md。沙箱限制使 TestClient 启动挂起，改为授权实机执行通过，挂起进程已终止。

## P02 — DONE

独立 API/worker 环境、资产下载/导入/校验/映射、固定源码补丁、诊断 API 与 UI 已接通。24 项资产校验通过；真实 CUDA 张量和中文路径音频解码通过。真实 FunASR 输出已保留。官方 v4 TTS 通过：原生 48000 Hz、170880 样本（3.56秒）、seed42、sample_steps32、fp16、batch1，生成68.219秒、Torch峰值2329732608字节。SoundFile参考读取与headless解码补丁已锁定。

新增 Windows Job Object 进程树隔离，经实测 launcher 先退出时后代仍被回收；停止服务建立持久 draining gate，避免并发新任务被接受后丢失。

P02 最终验收：16项回归PASS；157项worker依赖锁，pip check与锁dry-run通过；真实ASR、TTS、CUDA与资产证据见 docs/evidence/P02_*。P02环境与UI说明记录实际限制（ASR项目名同音字、尚未做主观音色评价）。

P02 HTTP 实测任务 870efe7da6d54b1093168155e2fa1476 COMPLETED，实际CUDA探针4.782秒。CUA补点诊断按钮时provider消失，补验记NOT_RUN；页面/日志此前已通过，HTTP调用链另有实测证据。
