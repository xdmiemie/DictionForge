# 实施进度

最近更新：2026-11-18（Asia/Hong_Kong）。每阶段单独验收、一次提交。BLOCKED 表示对应真实验收未通过；可执行部分继续推进，禁止以夹具代替真实模型验收。

## P00 — DONE

- 初始工作区 E:\DictionForge 仅有用户提供的计划文件，无 Git 历史或已有代码改动；用户澄清：AGENTS.md 指的就是 DictionForge_Codex_Project_Plan.md，已完整读取。
- 保留原文件，逐字归档到 docs/PROJECT_PLAN.md。
- origin：https://github.com/xdmiemie/DictionForge.git；远端 ls-remote 成功返回空引用，GitHub API 核对 default_branch=main、size=0，初始化本地 main。
- 官方 20250422v4 标签解析为 6a1ece89928f6e5042a3ae0e092c2f47bdbae596。
- 实机：Windows、Python 3.10.11、RTX 5080 16303 MiB、驱动 621.01；nvidia-smi 可用。
- 网络：沙箱内 DNS 不可用，经已授权网络执行成功。
- TimbreForge 作为只读功能基线，模型、数据与虚拟环境不作修改。

## P11–P12 — NOT_STARTED

2026-11-18 用户已要求「继续」，并授权后续测试所需相关命令。已从P09停止点恢复P10，后续依次推进P11/P12；待P12完成本地提交后按原计划尝试推送并核对远端哈希。历史停止点见 `docs/CODEX_HANDOFF.md`。

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

## P03 — IN_PROGRESS（实现与数据验收完成；P07已解除浏览器技术阻塞，完整浏览器流程待P12补验）

录音扫描/试听ID/排除、静音与长度切分、离线批量ASR、revision校对、发布不可变版本、从旧版修改标注均已接通。发布版本的音频ID指向正式目录，原版本与原录音保持哈希不变。仅开放已实测的 zh 普通话。

验收：全套27 passed；真实HTTP数据集6片52.72秒，5train/1validation。新版本 `76b5a08af4ca471f9c6fa9c903347e69`，dataset `09e42df26bf30b98cea3880e`。真实ASR、校对保留、父版保护、正式媒体Range206见 docs/evidence/P03_DATASET*.json、P03_ASR.md。前端生产构建通过。CUA浏览器provider不可用，P03_UI.md如实标记浏览器检查BLOCKED，留待P12复验；继续可执行阶段。

## P04 — DONE（完整浏览器流程待P12补验）

正在实现三段特征缓存、逐项hash核对及真实S1/S2加载器预检。新增扫描同名.lab文本快照与hash，防止扫描后源标注变化未被检测。浏览器访问阻塞仍存在。

P04验收：三段真实GPU特征通过，5片103.343秒；删除BERT后3.703秒从缓存补回；子版改1条文本仅重建1条文本，SSL/语义5条全复用。真实S1/S2各5独立样本、5批（batch1），去掉上游100条扩容；零批次拒绝。预检显存2058240字节只代表加载器张量，完整训练显存待P05。HTTP特征任务bfc3264d12e34c2e8b36b9fecc289dbe、预检a1d244cb3127420b865c76a64a9fa80e均ready。40项回归通过；前端构建通过。证据见docs/evidence/P04_*。新增.gitattributes保护补丁字节哈希、源.lab快照以及ASR重试保护未确认的手工文本。

## P05 — DONE（完整浏览器流程待P12补验）

已接预检绑定的训练受理、冻结初始化、排队后再校验、运行快照与双阶段UI。真实训练worker正在实现与实机短训。S1默认学习率调整为显式恒定0.002，累计周期/尾组按应用协议处理，实际值需由optimizer读取记录。

P05验收：主HTTP运行7a00f578a57f4c8895e481e5ba67413e完成S2/S1各2次真实更新，无跳步；参数SHA均改变。S2实际LR1e-4、3.36秒、峰值3818834944字节；S1恒定LR0.002、0.703秒、峰值2323796992字节。独立真训覆盖整轮S2 5更新/S1 4+1累积2更新、单S1轮内step1，以及真实FP16跳过9次不增计数。完整checkpoint与推理tensor逐项CPU复读通过。58项全套回归通过，后续有效配置字段格式/Scaler合同4项通过，前端build与SSR进度1项通过。文档P05_TRAINING.md/P05_HTTP.json记录实际配置、失败修复与局限。源码/补丁实际内容在预检与启动只读校验。

## P06 — DONE（A26 新数据权重初始化随 P11 验收；完整浏览器流程待 P12 补验）

两段训练在清梯度的优化器边界保存不可变推理快照、完整训练状态和原子索引；支持暂停、整服务重启续训、阶段交接恢复、历史点派生和子运行自身点再续训。恢复核对配置、真实特征内容、数据、源码、底模、参数键、批序与游标。周期点默认每100次实际更新，最近3点保留；控制点、最终点及引用点保护。派生首个自有点之前中断可显式从冻结父起点续训。

真实HTTP/CUDA运行 `be4045700e60491b95efb7101aaf411a`：S2第3步暂停、S1第3步暂停，两次完整服务退出/重启，另注入阶段交接和S1轮中worker退出，最终各20步。子运行 `a1e76ef07ae34bedb9dbb45590216c2f` 从较早S2点派生，暂停后从自身点恢复并各20步完成。父权重、检查点、run_meta与stage_state保持不变。实测暴露Windows读句柄阻碍状态原子替换，有限重试修复后从最后有效点恢复；真实Windows句柄测试通过。WDDM显存查询为N/A，GPU释放根据训练子进程退出、受控进程树回收与租约交接核实，不编造空闲显存数值。

验收：主链路PASS；87项全套回归通过、1项CPU环境跳过，后续变更针对性回归另列证据。检查点/故障I/O专测20项通过，包含真实Torch序列化ENOSPC注入和Windows junction越界；前端build和SSR通过。A21–A25 PASS，A26需P07模型注册及P11新数据来源模型短训后补齐。证据见 `docs/evidence/P06_*`。

## P07 — DONE

已接独立CPU模型导出队列、训练中导出并继续、中止导出、完成自动发布、不可变快照及失败重试；外部可信权重对导入、结构校验、依赖清单和模型库；数据/模型精确删除预览及活跃任务、完整恢复点、标注草稿、默认参考和派生引用保护。Windows删除恢复意图在服务启动时协调。

真实训练 `278c506666fa4876bff2ec0bf0d7d053` 在S2 u3导出且继续至S2 40/S1 4更新，CPU/GPU重叠有HTTP轨迹；`cca9eda7641b4436b238c936a9b23ffd` 在S2 u3中止并导出。三种模型均真实载入并合成原生48k非静音WAV，生成分别21.079/7.876/6.609秒，Torch峰值2318460416字节。真实Windows目录句柄导致发布失败，解除后同snapshot/hash/model版本幂等重试通过；导入/删除副本不改来源文件，训练数据引用删除被409拒绝。

111项全套回归通过、6项API环境跳过（另有worker CPU实际循环6项+纯合同11项通过）；模型服务独立15项通过；前端build/2项组件回归通过。A27–A32证据见 `docs/evidence/P07_*`。浏览器实测与后续发现记录单独附证据，P03全操作补验仍需P12完成。

P07浏览器补验：本机Playwright 1.62.1 + Edge157独立headless上下文9项PASS，模型卡、发布记录、删除预览和引用保护、音频实际解码/playing事件、390px布局均通过，无console/page/HTTP/request错误。修复窄屏网格和长路径溢出，标明模型卡进度为导出时快照。未提交新训练/合成/删除，未将静音播放事件称为主观听音。实际浏览器技术阻塞已解除；详见 `P07_BROWSER.md/.json`。最后CPU真实包内配置负例补验后，模型服务专项16项通过，正常包重新校验PASS。

## P08 — DONE

已接管理参考导入/本地上传、不可变48k单声道副本、默认参考选择与替换、逻辑启用/停用、固定条件自训/官方比较及历史结果。实际GPU驻留由worker模型设备检查和活动进程报告，与逻辑启用和整卡空闲显存分开显示。

真实比较A `d5f42268-4077-4e74-a360-cd73501bebfa` 排队后把默认参考由8.78秒A改成10秒B，两个结果仍冻结A，B状态保持NOT_RUN；比较B `e2426a04-7e35-40d3-a034-91c413a1df19` 再单独通过。四次实际TTS原生48k，峰值2318460416字节。恢复训练与试听互斥事件时序通过；232次状态观测、57次真实驻留。独立512MiB占用期间比较成功并正常释放；独立单次4倍显存分配捕获真实OOM，没有降参重试，该探针不冒充TTS模型OOM。

130项全套回归通过、6项API环境跳过；参考/模型28项专项、错误结果记录3项通过。真实浏览器7项及前端build/3组件回归通过，主选择恢复且没有新增GPU任务。A33–A35证据见 `docs/evidence/P08_*`，主观音色评价仍待P12。

P08收尾定向：参考服务20项通过，包含坏文件HTTP422及本地MP3/FLAC导入；上传取消等待真实导入线程后再清输入，错误输出也登记FAILED条件，相关4项通过。未将这些CPU/异常合同称为额外GPU成功。

## P09 — DONE（完成后按用户要求停止）

已接中文正文、标点/按行/不分句、语速与采样参数；受理事务冻结原文、模型与参考并更新current，幂等重放不倒退指针。单GPU实例逐句生成原生48k PCM16，独立句WAV与全段逐样本对应，记录Unicode码点范围、稳定句ID、实际seed、规范文本及静音样本边界。页面支持冻结原文、完整和逐句播放、点选定位、播放高亮、刷新恢复；失败直接定位阶段和句子。

真实HTTP四组7句均PASS，总24.56秒；每任务只加载一次模型，峰值2318460416字节。七句真实离线GPU ASR复核，六句去标点逐字一致，末句1字同音差异。七种非法输入HTTP422均保留current。真实浏览器10项PASS后，追加一次有效页面按钮端到端验收5项PASS：job `70f2d6554c8548afa3d8bab86a1effba`，seed2026/speed1.15/sample_steps16，原生48k、117120样本/2.44秒，实际生成5.21秒，成为最终current。没有将ASR或静音浏览器播放称为主观音质评价。

全套189 passed/6 skipped（API环境没有Torch的训练循环合同）；收尾取消/发布竞态与损坏重验修复后服务专项41 passed，worker合同19 passed，前端build及4项组件测试PASS。A36–A38证据在 `docs/evidence/P09_*`。修复Windows浏览器残留媒体连接导致无限等待停机的问题，为已有serve加入15秒HTTP关闭上限；最终服务PID47264经安全endpoint正常exit0，无活动任务/worker/7867监听，SQLite integrity_check=ok，当前音频hash仍一致。

新增 `docs/USER_MANUAL.md`、`docs/CODEX_HANDOFF.md`；保留全部data/vendor/venv和本地历史。原始12份景元wav/lab哈希不变。只读TimbreForge发现外部已提交到309adfe，工作树干净，未改动或回滚。P03完整页面补验、A26及P10–P12仍按既有记录待办。完成唯一P09本地提交后停止，不推送。

## P10 — DONE

2026-11-18 按用户继续及测试授权恢复。已接当前播放器引用、取消、独立WAV/320kbps MP3保存下载、作品库模型/标题/正文筛选与每页最多10条、单件/本页/跨页精确删除确认。持久浏览器/HTTP/任务引用保护临时与永久文件；旧临时结果在有效新请求后退休，Windows占用待清理，重启按移动意图对账；旧文本仍可查看，失效音频HTTP410。

真实HTTP/CUDA：排队保存与新合成竞争时源PCM保持，实际Windows文件句柄阻止清理并在关闭后自动完成；实际取消已加载GPU任务；最后三句合成 `1ba9920f732a4eef902f6917b6c6a7b9` 为48k/5.16秒，生成7.516秒、峰值2318460416字节。原P09单句保存独立WAV/MP3后，跨新任务与整服务重启哈希、解码、Range206/416/HEAD仍通过。12件冻结删除预览后新增第13件及播放引用保护实测通过。

最终全套222 passed/6 skipped（API环境无Torch的既有训练合同）；媒体专项13项全部通过，含真实Windows句柄与junction；随后FLAC媒体头修复及HTTP/引用专项4 passed。前端build及5项组件测试通过。真实浏览器首轮9项PASS；修复后台新作品刷新取消删除确认后，最终10项PASS：页面创建15件独立验收作品，播放/下载/筛选/分页/引用心跳/单件和跨页删除、预览后新保存不进入确认集合、390px布局均通过，原3件作品与current不变，无页面/console/HTTP错误。自动静音播放不称为主观音质评价。

A39–A44 PASS；实际配置、产物、失败修复、测试与浏览器证据见 `docs/evidence/P10_*`。尚无依赖或源码锁变更。完成唯一P10本地提交后进入P11，不推送。
