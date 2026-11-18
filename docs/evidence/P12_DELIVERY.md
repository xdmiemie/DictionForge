# P12 Windows 实机交付验收

2026-11-18，Windows 11 build 26200，RTX 5080 16GB / driver 621.01。P11 唯一提交 `8301f03bdfe8d63f2af9648cbe28757531272a79` 后实施。

## 独立环境、资产与 Windows 入口

项目内 `vendor/tools/` 包含 Python 3.12.10 官方 NuGet、uv 0.11.32、Node 24.22.3、pnpm 10.11.0、MinGit 2.55.0.windows.2、FFmpeg 8.1.2 full build。六份包都有 URL、大小和 SHA256；Node/FFmpeg 与发布方校验值比对，pnpm 同时核对 registry SHA512。下载中的截断包被拒绝，最终完整包验证后才解压。FFmpeg/ffprobe 二进制哈希与 P02 完全一致。

两套原环境移至项目内 `.venv-*-backup-*`，从空目录重建 `.venv-api` / `.venv-worker`。二者 `sys.base_prefix` 和 pyvenv.cfg 都指向 `E:\DictionForge\vendor\tools\python`，不再指向 astrbot 或 TimbreForge。API 安装 27 项、worker 安装 157 项，各自 `uv pip check` 通过；前端原 node_modules 备份至 data/environment-backups，21 项依赖按 frozen lock 重新安装并构建成功。

这是目标机器上的全新 Python/依赖环境测试，不是重装 Windows。24 项模型资产复用本项目内已校验文件，再实际执行 prepare/map/check。33 个同版本 wheel 从 TimbreForge 已下载的 rift-wheelhouse **只读复制**到本项目，并逐文件核对；其中 2.7 GiB Torch wheel 另与 PyTorch 官方索引 SHA256 `c97dc47a1f64745d439dd9471a96d216b728d528011029b4f9ae780e985529e0` 比对。运行不依赖原 wheelhouse 或其虚拟环境。其余依赖来自固定包缓存/官方分发。

最终命令 `scripts/setup.ps1 -Worker -Offline -Wheelhouse E:\DictionForge\data\wheelhouse` 离线重放通过，日志 `data/p12-setup-final.log`；六个工具、27/157项环境、前端构建和三份上游补丁均通过。独立空白测试目录 `data/p12-bootstrap-check` 又实际执行官方 pnpm 下载、SHA256、解压与文件清单生成，证明首次下载分支可用。

实测安装修复：uv pip sync 改用位置参数；CUDA 源的 TorchCodec 无 Windows 0.8.1+cu128 wheel，`worker.sources.txt` 固定同版本 PyPI Windows wheel 的 URL/SHA256，其他 PyTorch 包仍选 cu128。版本没有静默替换。固定 pnpm 的 esbuild 安装脚本提示忽略，但平台二进制依赖已装好，实际 TypeScript/Vite 构建通过。

从 `C:\Windows\Temp` 调用中文启动入口和 doctor：诊断真实 CUDA 张量与全部资产通过。第一次启动在第 40 步训练前的服务 PID 为 41224。旧 Start-Process 方式导致 cmd 被子控制台牵住，改用关闭继承句柄的 detached Python launcher 后入口正常返回。暂停时 stop.ps1 正常停止 PID41224；新实例 PID42512 接续，重复启动两次均 reused=true。默认 Edge 窗口实际标题为“声句工坊 · DictionForge”。实际 RUNNING 训练期间 stop.ps1 返回拒绝，服务 PID 不变。停止脚本只请求所属工作区的安全接口，不强杀进程。

新 worker 的独立 ASR 探针真实识别 8.932 秒景元参考，ASR 段耗时 1.109 秒、进程总耗时 22.766 秒、峰值 2057700864B；专名同音误识别仍明确保留。

## 页面业务全链路

可重放入口 `scripts/validate_p12_browser.cjs prepare|correct|train_pause|resume|finish|probe|restore`。业务写操作均由真实 Edge DOM 触发，HTTP 用于检查状态。数据整理在独立环境下载等待期间先执行；依赖重建后，从其冻结数据、特征预检开始完成全部训练、恢复和合成步骤，另执行新环境 ASR 探针。没有伪称所有步骤都在重装后从零执行。

| 步骤 | 真实结果 |
| --- | --- |
| 扫描/排除 | scan `83a3e9b7dab449e08ed9cdf673210dd0`，6份原录音独立副本，逐文件播放验证、排除1份 |
| 整理/ASR | prepare `15c9a3fa2ee54a73bd871de7584fcdb8`，5片；页面重识别 `8d8702fb3a324a4daf4b4b271c07fd02` |
| 校对/修订 | ASR“罗父云奇将军警员”按原录音配套 lab 改为“罗浮云骑将军景元”；通过页面建立修订版，保留原版 |
| 最终训练数据 | `5da12b91952043f89980eded92f1d9cb`，5片 / 44.42秒；特征 `72420886a8b84ad8a33fc217ab892aa0`，预检 `a7ca4318dc1a417ab3fa9fd3b4022aa5` |
| 第一轮训练 | `58a515fdf3554d808d25ac053a9ab581`，official / seed42 / FP16 / batch1 / rank32；S2目标40步，S1目标2步、累积1 |
| 暂停/重启 | 第2步保存 `s2-u2-f3aadcd2f7f5`；整服务停止重启，再从页面继续。恢复参数 SHA 与暂停点逐字一致，恢复计数为2 |
| 阶段交接 | S2达到40步后GPT完成2步，均无跳过更新；S2续训循环49.076秒、峰值4090110976B，S1 2.709秒、峰值2317548544B |
| 自动导出 | job `1040c87adea84b80b003b50d5fdbfeee`，模型 `661bdeb20f295a1fa001465fc8447e20` |
| 页面合成/保存 | TTS `37b1131d876e4084afea19c91ee5f7fa`，原生48k、192000样本/4秒；永久WAV `4841aea38d3f4637be47cd90dc9a165f`，播放器实际解码 |
| 当前句回流 | `b79fbedd6541426586b7a5221297be59` 发布 `1b7b132a1bac4ee4874243d4d32ef857`；仅裁静音后保留3.8秒，原文本复用、ASR0，真实留出不变 |
| 二次微调 | `cefacb08cafd4379be3b4230f134362d` 从661b…初始化，S2/GPT各2步；9.074秒/3812581376B，1.123秒/2322795520B；完整检查点及初始化报告保留 |
| 最终权重对 | 自动导出 `a5d2e801de7b4369b45ac502d735bd02`，模型 `64d73b8c3dc35410ac0cb9b60a1cb135` |
| 最终实际推理 | 页面固定试听 `520a7ed51a0c458fb6c20311496ac5fc`，真实 GPU、原生48k、播放器解码通过 |

时间是对应训练循环指标，不含加载、校验、导出与人工操作等待。所有参数/进度/权重与音频 SHA、浏览器截图目录在 `P12_BROWSER.json`。最终默认模型通过页面切回用户确认过的 `21786e5fe9d652f591a44d3c368ae8fe`，参考 B 保留。

## 人工角色效果 A53

首先展示相同参考B、正文“你好，欢迎来到声句工坊。”、seed42/steps32/speed1 的参考、官方底模和 P11 二次微调样本。用户反馈：“目前可以，但是为什么官方底模的也很像？可以再多提供两段长一点的试听”。说明两边都使用参考音频，官方模型本身具备参考条件合成能力，本次两步二次短训不承诺比底模明显更好。

随后用模型21786…、同一参考B、seed42/steps32生成并永久保存两段：日常叙述19.40秒（作品 `6d8a0b80a5384cb79f0133e0c47e3613`），连续表达22.52秒（`7604a45d9eb44897bb1165436a021326`）。均为原生48k。用户实际试听后明确回复：“效果确认符合预期，可以继续推进”。A53据此 PASS；未编造音色、咬字、漏字、重复、自然度的单项分数，也未把自动静音播放当成人工结论。完整正文、参数、音频哈希与反馈见 `P12_LISTENING.json`。

## 回归和交付边界

- 独立 API 全套：241 passed / 7 skipped，111.76秒。跳过项为 API 无 Torch 的已列明合同；另在新 worker 完成18项。FastAPI/Starlette testclient弃用提示不影响通过。
- 启动修复后 Windows 启停专项5 passed；实际入口的启动/复用/默认浏览器/拒绝活动停机/暂停后重启通过。前端 build和5项组件回归通过。
- `validate_delivery.py --phases 12` 提交前复核：12份原景元wav/lab哈希不变、计划逐字一致、TimbreForge HEAD309adfe且干净、SQLite integrity_check=ok、无活动任务、6件指定永久作品哈希一致、暂停恢复参数与计数一致、最终完整检查点及48k产物校验通过。提交后以 --phases 13 再核对唯一阶段历史。
- 声音、模型、检查点、缓存、备份和日志留在 data/vendor/venv 忽略目录，不进入 Git。短数据/短训练用于验证闭环；用户确认覆盖所展示的特定样本，不能推导为所有文本的量化质量保证。
- A51–A54 PASS。P12唯一提交后才推送。提交后的最终审计及推送结果写 `data/p12-delivery.json` / `data/P12_PUSH_RESULT.json`，避免为记录自身提交哈希额外增加阶段提交。
