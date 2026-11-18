# P11 完成与 P12 续做信息

2026-11-18 最新「继续」已解除此前 P09/P10 停止要求。P11 完成；以 CODEX_PROGRESS.md 和 evidence/P11_FEEDBACK.md 为准。下面 P09/P10 信息仅保留历史，不是当前停止指令。P12 待独立环境、Windows启停、全链路与主观试听收尾；P12唯一提交后才推送。

当前TTS 5ce84aadf90742daa5b2842641167563（48k/2.84秒）；回流新版本69a4dca81b56499dbcdfcd3e64982d81，来源模型21786e5fe9d652f591a44d3c368ae8fe。另一官方初始化短训导出0093057e5061549b8feb98a726e7ef33。P11来源/官方训练分别d019d2b5d6ed465aae6cae3f31d2ac9d、2038676bb50e402f9d4e7ccc81e980cc。P10三件永久作品仍保留，旧P10临时TTS已按规则回收。

# P09 停止点与续做交接

> 2026-11-18 已按用户「继续」指令恢复，后续测试相关命令已获授权。本文件下文保留 P09 历史停止点；最新实施与验收以 `CODEX_PROGRESS.md` 为准。P10 新任务受理后按计划回收旧临时 TTS 音频，历史记录仍可查询。原 P09 最后音频已另存为独立 WAV/MP3 作品。

P10 当前真实合成 `1ba9920f732a4eef902f6917b6c6a7b9`（3句、5.16秒、48k）可作为P11回流输入。来源模型 `a974c5da69695d0aba3477bff5c7b768`，参考B、数据版本等原归属未变。P09最后单句的独立永久作品：WAV `bfa6b69242ae406f9b04859837d763e3`、MP3 `7c4fff962a0a4f46b171b16f7c939e41`，另保留并发保存作品 `b2968e66c6294d3d9f654b2f0b2768b9`；不得把这些当临时验收副本清理。已退出的旧历史TTS路径可能返回410，原GPU报告和冻结原文仍有效。

## 用户最新指令

用户要求「完成 P9 后停下任务，保存好相关信息方便下次继续」。因此本轮范围收窄为完成 P09 后停止，不实施 P10–P12，不推送远端。恢复工作需用户新的继续指令。原计划保存在 `DictionForge_Codex_Project_Plan.md` 和逐字副本 `docs/PROJECT_PLAN.md`；用户澄清所谓 AGENTS.md 就是该计划。

本文件随唯一的 P09 提交保存。该提交哈希请用 `git log -1 --format=%H` 查看，避免为记录自己的哈希而增加第二次阶段提交。最新验收与停机事实在文末更新。

## 工作区与提交约束

- 工作区：`E:\DictionForge`，分支 `main`。
- origin：`https://github.com/xdmiemie/DictionForge.git`。最初远端空仓库、默认分支 main 已核对。尚未推送，按原计划必须全部 P12 验收及本地提交完成后才尝试推送并核对远端哈希。
- 每阶段恰好一次本地提交，后续不得把 P10 内容混入 P09 或重做既有阶段提交。
- 只读基线：`E:\TimbreForge`，初始 HEAD `e2884c72604766ce6d4193ebdba56f6b4c040785`，当时用户已有删除 ` D DictionForge_Codex_Project_Plan.md`。P09 只读复核时发现外部提交已前进为 `309adfefc97d41dd5e4e79a64adff79e124362e9`（17:00:02 +0800，「清理文件」），工作树干净；本项目没有写入或回滚，保留该外部变化。
- 原始录音：`D:\中文 - Chinese\景元`、`D:\中文 - Chinese\知更鸟`。只读，应用生成物均在 DictionForge。

| 阶段 | 本地提交 | 内容 |
| --- | --- | --- |
| P00 | 4b82b8d | 计划归档、TimbreForge 与锁定上游审计 |
| P01 | 75f5a24 | 双标签 WebUI、SQLite 持久任务和监督 |
| P02 | c94b89e | 独立环境、资产、真实 CUDA/ASR/v4 48k |
| P03 | 6e5bd48 | 扫描、切分、ASR、校对、不可变数据版本 |
| P04 | f0dc3f8 | 三段真实特征、缓存与真实 S1/S2 加载预检 |
| P05 | f7286a1 | 真实 v4 LoRA/GPT 优化器训练与阶段交接 |
| P06 | ce6102d | 完整暂停续训、崩溃恢复、历史点派生 |
| P07 | 9802384 | 并行 CPU 导出、模型对、导入和引用保护 |
| P08 | da71629 | 默认参考、固定比较、逻辑启用和真实 GPU 驻留 |
| P09 | 本文件所在提交 | 文本与逐句合成、冻结请求和结果、播放验收 |

## 当前机器运行条件

- Windows 11，Python 3.12.10；API `.venv-api\Scripts\python.exe`，模型 `.venv-worker\Scripts\python.exe`。
- 两个 venv 的基础解释器仍为已有 `E:\astrbot-chat\external\Python312\python.exe`，只读借用。最终独立 Python 分发、全新安装与双击启停未完成，属于 P12。
- RTX 5080 16GB、驱动 621.01、Torch/torchaudio 2.9.0+cu128、CUDA 12.8；FFmpeg 8.1.2。锁定信息：`configs/runtime.lock.json`、`api.requirements.txt`、`worker.requirements.txt`、`worker.lock.txt`（157 项 worker 包）、`frontend/pnpm-lock.yaml`。
- 上游源码在被 Git 忽略的 `vendor/gpt-sovits`；官方标签 `20250422v4`，commit `6a1ece89928f6e5042a3ae0e092c2f47bdbae596`。补丁由 `scripts/apply_patches.py` 及源码锁逐字校验，不得手工漂移。
- 24 项资产约 4.44GB，G2PW 解压约 637MB；`configs/assets.lock.json` 固定来源 revision/sha256，`scripts/assets.py` 管理检查、导入、下载和上游映射。
- 依赖安装使用项目内 `data/uv-cache`、`data/tmp`。之前只读借用 `E:\TimbreForge\vendor\python-tools\bin\uv.exe`，不安装或修改 TimbreForge。
- 当前环境联网、localhost socket、GPU/子进程和真实浏览器测试常需授权执行；已在本任务授权范围内。不要把沙箱的 socket 权限错误误判为应用不可用。

现有环境启动：

```powershell
Set-Location E:\DictionForge
$env:PYTHONPATH = 'E:\DictionForge\backend'
& .\.venv-api\Scripts\python.exe -m dictionforge.serve
```

访问 `http://127.0.0.1:7867`。修改前端后在 `frontend` 执行 `pnpm run build`。安全停止：`Invoke-RestMethod -Method Post http://127.0.0.1:7867/api/system/stop`，活动或排队任务存在时返回冲突，先安全暂停或等任务结束。

## 必须保留的本机产物

Git 不包含 `data/`、`vendor/`、`.venv-*`、`frontend/node_modules` 和 `frontend/dist`。本次交接保留所有这些目录。代码提交不等于备份训练和媒体文件；复制备份应在停机后连同 `data/state` 与整个 `data` 一起保留。

- `data/state`：SQLite 任务、事件、记录与锁。模型、数据、媒体 ID 解析都依赖此状态。
- `data/runs/<id>`：实际 worker 日志、训练冻结请求、检查点及导出意图。
- `data/runtime/tts/<id>`：P09 合成冻结请求、逐句音频、整段音频和结果；对应进程日志仍在 `data/runs/<id>`。
- `data/datasets`、`data/features`、`data/models`、`data/references`：受管理的数据版本、缓存、权重对、独立参考。
- `docs/evidence`：可提交的真实验收报告；对应的大音频、权重、截图主要在忽略的 data 目录，报告记载路径。

主要真实数据集是景元 `09e42df26bf30b98cea3880e`，当前版本 `76b5a08af4ca471f9c6fa9c903347e69`，父版 `eabb1d2c8a494054bfcb4eac28f6c31f`。6 片共 52.72 秒，5 train/1 validation。参考 A `76d92e704c6b438e8f5f67024133dd4b`（8.78 秒），B `563ea3259eed48938003d5bb0566d68e`（10 秒）。

主要模型：

- `a974c5da69695d0aba3477bff5c7b768`：景元完成模型，S2 40 次/S1 4 次更新；当前逻辑启用，默认参考 B。
- `042ba87417df5c67b75f9ef27777be74`：S2 u3 中途导出，GPT 尚为官方初始权重。
- `1d9b77725c935da2af1a8f7279e0c7be`：S2 u3 提前结束导出。
- 官方逻辑 ID 为 `official`；P08 浏览器留下有效参考 `b4f0cfb9d1b8458595f6499f277ffdf5`。
- P07 训练 `278c506666fa4876bff2ec0bf0d7d053` 已完成 40/4；早停运行 `cca9eda7641b4436b238c936a9b23ffd` 已在 P08 续训完成 40/4，另有新自动导出。请通过 API 获取当前记录，旧脚本不能盲目重跑。
- P06 父运行 `be4045700e60491b95efb7101aaf411a`、派生运行 `a1e76ef07ae34bedb9dbb45590216c2f` 均完成 S2/S1 各 20 更新，完整恢复证据及文件保留。

## 实现约定与后续切入点

`jobs.py` 管幂等提交、事务 on_accept 和 SSE；`supervisor.py` 管进程身份和资源锁。GPU lane 串行运行训练/特征/ASR/推理，CPU lane 仅 `model_export`，可与训练同时运行。Windows Job Object 保证 launcher 退出后子进程树回收。

训练路径：`training.py` / `training_worker.py` / `s1_training.py` / `s2_training.py`。完整恢复包含 optimizer/scheduler/scaler/RNG/样本顺序及游标，最新周期点留 3 个，引用点另保留。恢复失败禁止静默初始化底模；来源模型初始化虽底层有 LoRA 相关能力，应用入口与「只应用一次」实训应在 P11 完成。

模型路径：`models.py` / `exports.py` / `model_worker.py` / `references.py`。导出意图、权重快照、注册与删除有独立持久记录。结构验证与 GPU 试听区分。默认参考变化会使旧条件的试听状态失效。

合成路径：`text_units.py` / `synthesis.py` / `inference_worker.py` / `frontend/src/TextSynthesis.tsx`（由 `Synthesis.tsx` 接入）。`POST /api/tts` 返回 Job；`GET /api/tts/current`、`GET /api/tts/{id}` 返回冻结 request/job/result。原文索引为 Unicode code point 半开区间，音频索引为 48k 样本半开区间，JS 不能直接把 code point offset 当 UTF-16 index。每句独立 WAV，与全段对应区间逐样本一致；实际 seed 为 `(base_seed + index) % 2**32`。

实际推理一任务加载一次模型，顺序复用参考。结束退出释放 GPU，不谎报持久驻留。错误结果带具体 phase/句子；上游失败占位 16k 音频禁止发布；没有静默降采样率或降参数。无效新请求不改变当前指针，旧 request_id 幂等重试也不把 current 倒退。锁定上游 `fragment_interval=.3` 以 32k 配置构建零样本，v4 输出48k实测尾静音约.2秒；应用保存原样并记录实际样本边界，不按参数虚构时长。

P10 应在此基础实施媒体租约、旧临时结果回收、独立 WAV/MP3 保存、作品库及并发/Windows 句柄验证。现在所有任务音频保留，尚无临时回收。`/api/audio/{id}` 现有 FileResponse/Range 不等同于已完成播放租约。

P11 使用 server 冻结的 `tts_job_id` + `sentence_id`，不可相信客户端重传文本/路径。整句标注可复用，拆片必须逐段对齐或 ASR。保持真实留出集、并发版本 CAS、来源模型和音频去重；新增数据训练默认来源模型、允许官方；实测 LoRA 只应用一次。

## 尚未完成的验收

- P03 实现与数据验收已完成，但最初 CUA 技术阻塞，完整扫描→校对→特征→训练页面操作留 P12 补验，进度仍如实为 IN_PROGRESS。P07 已用实际 Playwright/Edge 解除浏览器技术阻塞。
- A26 来源模型/新数据初始化待 P11；A39–A44 待 P10；A45–A50 待 P11；A51–A54 待 P12。
- A53 主观角色音质未执行。静音 browser playing/currentTime、数值解码和 ASR 文本核对不能冒充人工听感。
- P12 仍需完整 5080 闭环、全新环境、独立 Windows 启停、最终依赖/资产锁核查、文档与提交检查，然后才能尝试 push 并核对远端提交。当前没有失败推送需要重试。

真实浏览器使用本机 CodexPrimaryRuntime 自带 Playwright 1.62.1 与系统 Edge 157，独立 headless context，不操作用户日常浏览器。入口参考 `scripts/validate_p07_browser.cjs` / `validate_p08_browser.cjs` / `validate_p09_browser.cjs`；用对应 runtime 的 Node 运行。原生 CUA GetCursorPos 曾 AccessDenied，不必重复依赖该路径。

## P09 最终验收和停机记录

P09 已完成，A36–A38 PASS。之后未开始 P10–P12、未尝试 push。当前工作保存在本文件所在的唯一 P09 提交；此前 P00–P08 各有一次提交，累计应为 10 个阶段提交。

- 四组 HTTP/GPU：single `d0dcdf7654654c7b8bb4ed514c62728e`（3.60秒）；repeated `6b81ed20c2544475813973878e0bd687`（3句8.32秒）；lines `faac676251e249428b6c3a7f5a5fcefd`（2句8.00秒）；none `1411e75b99634c35a7d4ca357adbd264`（4.64秒）。全部原生48k，逐句PCM/范围验证通过。7句真实ASR已做，6句文字一致、1句仅「句/据」同音字差异；详见 `P09_HTTP.json`、`P09_ASR.json`、`P09_WORKER.md/.json`。
- 有效页面按钮补验创建最后一条 TTS，**最终 current 为 `70f2d6554c8548afa3d8bab86a1effba`**。源文本「你好，今天我们一起去公园散步。」；模型 a974、参考 B 不变；seed2026、speed_factor1.15、sample_steps16、fp16。生成5.21秒、load11.70秒、峰值2318460416字节。
- 最终音频 ID `187e8a863e7646619e45893323aec704`，原生48000 Hz、117120样本/2.44秒；SHA256 `9d7f6a06d7b54bb052438745ea54cc07e41b8d31831882a79e950b72e0aa7186`。实际路径、逐句结果及完整冻结请求见 `docs/evidence/P09_HANDOFF_STATE.json` 的 current 字段。
- 真实浏览器10项+有效按钮补验5项PASS，前端build+4组件测试PASS。截图目录 `data/p09-browser/2026-11-17T11-51-16-441Z` 和 `data/p09-browser-submit/2026-11-17T11-59-32-401Z`。有效提交脚本有持久 guard；普通重跑会拒绝第二次有效POST，`--resume` 只复核已受理任务。
- API全套189 passed/6 skipped，收尾服务专项41 passed、worker合同19 passed。测试范围/跳过/故障注入说明见 P09各证据；未冒充P12主观音质。
- 中途旧服务PID48232因Windows浏览器残留连接停留在等待HTTP关闭；核对无任务/无子进程后只终止该owned进程，另一次抢先启动因app.lock拒绝并退出。已为现有serve增加15秒HTTP关闭上限。**最终服务PID47264通过安全endpoint正常退出，exit0**，最终未触发强杀或超时。
- 停机后实测：活动任务0、DictionForge服务/worker/推理/ASR进程0、7867监听0、SQLite `integrity_check=ok`；最终WAV重新计算哈希一致。机器状态证据 `P09_HANDOFF_STATE.json` 保留。
- 12份原始wav/lab哈希全部一致。TimbreForge最终只读核对为309adfe、工作树干净，保留其外部提交。数据、权重、检查点和所有旧TTS产物均保留，未进行P10回收。

下次先阅读本文件、`PROJECT_PLAN.md`、`CODEX_PROGRESS.md` 和 `git status` / `git log`，确认现有状态后从 **P10** 开始。不要盲目重跑已有验收脚本创建新训练或重复发布，不要重置/清理data目录，不要为已有阶段再补一次提交。每个后续阶段仍只提交一次。
