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

## P07–P12 — NOT_STARTED

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

## P03 — BLOCKED（浏览器补验；实现与数据验收完成）

录音扫描/试听ID/排除、静音与长度切分、离线批量ASR、revision校对、发布不可变版本、从旧版修改标注均已接通。发布版本的音频ID指向正式目录，原版本与原录音保持哈希不变。仅开放已实测的 zh 普通话。

验收：全套27 passed；真实HTTP数据集6片52.72秒，5train/1validation。新版本 `76b5a08af4ca471f9c6fa9c903347e69`，dataset `09e42df26bf30b98cea3880e`。真实ASR、校对保留、父版保护、正式媒体Range206见 docs/evidence/P03_DATASET*.json、P03_ASR.md。前端生产构建通过。CUA浏览器provider不可用，P03_UI.md如实标记浏览器检查BLOCKED，留待P12复验；继续可执行阶段。

## P04 — DONE（浏览器补验沿用P03阻塞）

正在实现三段特征缓存、逐项hash核对及真实S1/S2加载器预检。新增扫描同名.lab文本快照与hash，防止扫描后源标注变化未被检测。浏览器访问阻塞仍存在。

P04验收：三段真实GPU特征通过，5片103.343秒；删除BERT后3.703秒从缓存补回；子版改1条文本仅重建1条文本，SSL/语义5条全复用。真实S1/S2各5独立样本、5批（batch1），去掉上游100条扩容；零批次拒绝。预检显存2058240字节只代表加载器张量，完整训练显存待P05。HTTP特征任务bfc3264d12e34c2e8b36b9fecc289dbe、预检a1d244cb3127420b865c76a64a9fa80e均ready。40项回归通过；前端构建通过。证据见docs/evidence/P04_*。新增.gitattributes保护补丁字节哈希、源.lab快照以及ASR重试保护未确认的手工文本。

## P05 — DONE（浏览器补验沿用P03阻塞）

已接预检绑定的训练受理、冻结初始化、排队后再校验、运行快照与双阶段UI。真实训练worker正在实现与实机短训。S1默认学习率调整为显式恒定0.002，累计周期/尾组按应用协议处理，实际值需由optimizer读取记录。

P05验收：主HTTP运行7a00f578a57f4c8895e481e5ba67413e完成S2/S1各2次真实更新，无跳步；参数SHA均改变。S2实际LR1e-4、3.36秒、峰值3818834944字节；S1恒定LR0.002、0.703秒、峰值2323796992字节。独立真训覆盖整轮S2 5更新/S1 4+1累积2更新、单S1轮内step1，以及真实FP16跳过9次不增计数。完整checkpoint与推理tensor逐项CPU复读通过。58项全套回归通过，后续有效配置字段格式/Scaler合同4项通过，前端build与SSR进度1项通过。文档P05_TRAINING.md/P05_HTTP.json记录实际配置、失败修复与局限。源码/补丁实际内容在预检与启动只读校验。

## P06 — DONE（A26 新数据权重初始化随 P11 验收；浏览器补验沿用 P03 阻塞）

两段训练在清梯度的优化器边界保存不可变推理快照、完整训练状态和原子索引；支持暂停、整服务重启续训、阶段交接恢复、历史点派生和子运行自身点再续训。恢复核对配置、真实特征内容、数据、源码、底模、参数键、批序与游标。周期点默认每100次实际更新，最近3点保留；控制点、最终点及引用点保护。派生首个自有点之前中断可显式从冻结父起点续训。

真实HTTP/CUDA运行 `be4045700e60491b95efb7101aaf411a`：S2第3步暂停、S1第3步暂停，两次完整服务退出/重启，另注入阶段交接和S1轮中worker退出，最终各20步。子运行 `a1e76ef07ae34bedb9dbb45590216c2f` 从较早S2点派生，暂停后从自身点恢复并各20步完成。父权重、检查点、run_meta与stage_state保持不变。实测暴露Windows读句柄阻碍状态原子替换，有限重试修复后从最后有效点恢复；真实Windows句柄测试通过。WDDM显存查询为N/A，GPU释放根据训练子进程退出、受控进程树回收与租约交接核实，不编造空闲显存数值。

验收：主链路PASS；87项全套回归通过、1项CPU环境跳过，后续变更针对性回归另列证据。检查点/故障I/O专测20项通过，包含真实Torch序列化ENOSPC注入和Windows junction越界；前端build和SSR通过。A21–A25 PASS，A26需P07模型注册及P11新数据来源模型短训后补齐。证据见 `docs/evidence/P06_*`。
