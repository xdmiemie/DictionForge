# DictionForge 交付与继续维护

2026-11-18：用户已解除此前P09/P10停止要求，P00–P12已全部实施和验收。之后用户要求统一分页、纯语音STT后的逐条/可跳过审核，以及独立数据与模型管理和删除。以上维护改动已实现并验收；不重复创建阶段提交。

## 工作区与历史

- 本项目 E:\DictionForge，main，origin=https://github.com/xdmiemie/DictionForge.git。
- P00–P12恰好各一次本地提交；P12为c991af6c3818668062ccadf830a3ca3e5f0be456，已推送并核对origin/main相同哈希。后续UI维护使用独立UI提交。
- 提交后的13阶段审计与远端核对结果分别在 data/p12-delivery.json、data/P12_PUSH_RESULT.json；不为记录自身hash新增阶段提交。
- 只读TimbreForge最后核对HEAD309adfefc97d41dd5e4e79a64adff79e124362e9，干净。原12份景元wav/lab哈希未变。其他项目及原始录音不得作为清理目标。

```text
4b82b8d P00 archive plan and audit TimbreForge and pinned v4 upstream
75f5a24 P01 build Chinese workbench and persistent supervised jobs
c94b89e P02 lock assets and validate real CUDA ASR and native v4 synthesis
6e5bd48 P03 implement recording preparation review and immutable dataset versions
f0dc3f8 P04 implement verified upstream features and actual S1 S2 preflight
f7286a1 P05 deliver real v4 LoRA and GPT optimizer training with frozen handoff
ce6102d P06 verify full pause resume crash recovery and protected checkpoint lineage
9802384 P07 publish verified model pairs with concurrent export import and protected deletion
da71629 P08 verify reference-bound comparisons model activation and real GPU residency
98f47d3 P09 deliver native 48k sentence synthesis with verified UI playback and handoff
116df75 P10 deliver leased media playback permanent speech library and verified lifecycle
8301f03 P11 deliver sentence feedback and verified source model initialization
c991af6 P12 独立Windows交付、真实完整闭环及人工试听确认
```

## 启动与本机状态

双击根目录「启动DictionForge.cmd」，http://127.0.0.1:7867 。安全停止使用「停止DictionForge.cmd」。P12验收的最后服务PID42512，实际PID请查询/api/health。原来借用的astrbot解释器已移除运行依赖；新API/worker的base_prefix均为vendor/tools/python。

六项工具由configs/tools.lock.json固定，Python包27/157项由api.requirements.txt、worker.lock.txt、worker.sources.txt固定。已有模型资产24项通过prepare/check。源码标签20250422v4、commit6a1ece89928f6e5042a3ae0e092c2f47bdbae596及三补丁保持不变。所有data/vendor/venv与安装备份均保留，不进入Git。

## 用户确认与重要产物

- 已确认默认模型21786e5fe9d652f591a44d3c368ae8fe，参考563ea3259eed48938003d5bb0566d68e（景元10秒）。
- 用户长段试听永久WAV：6d8a0b80a5384cb79f0133e0c47e3613（19.40秒）、7604a45d9eb44897bb1165436a021326（22.52秒）。用户明确确认效果符合预期。
- P10原三件作品bfa6b69242ae406f9b04859837d763e3、7c4fff962a0a4f46b171b16f7c939e41、b2968e66c6294d3d9f654b2f0b2768b9保留，不当作临时验收副本删除。
- P12正确标注版本5da12b91952043f89980eded92f1d9cb，5片44.42秒；训练58a515fdf3554d808d25ac053a9ab581，S2第2步跨重启精确恢复到40步，GPT2步；模型661bdeb20f295a1fa001465fc8447e20。
- P12当前TTS37b1131d876e4084afea19c91ee5f7fa，48k/4秒；永久作品4841aea38d3f4637be47cd90dc9a165f。
- 回流版本1b7b132a1bac4ee4874243d4d32ef857，追加3.8秒；二次训练cefacb08cafd4379be3b4230f134362d，S2/GPT各2步；最终模型64d73b8c3dc35410ac0cb9b60a1cb135，GPU试听520a7ed51a0c458fb6c20311496ac5fc通过。

## 验收与限制

API241 passed/7 skipped；worker18 passed；Windows启停专项5 passed；前端build/5组件通过。全部真实阶段数据、截图路径和配置见evidence/P12_DELIVERY.md及JSON。API环境跳过的Torch合同另由worker覆盖；自动静音浏览器播放只证明解码，人工效果结论来自用户确认。独立环境在本机重新创建，复用了已校验的模型和wheel缓存，没有重装操作系统。

操作手册USER_GUIDE.md，排障TROUBLESHOOTING.md。恢复训练须保留原冻结配置/数据/完整检查点；换数据应新建微调运行。迁移时保留数据库与所有依赖的路径关系，不要只复制模型权重。

## 后续 UI 维护（2026-11-18）

三项新需求已完成：多记录列表每页5项；ReviewWizard提供逐条修正/去掉/通过后保存并自动下一条、跳过全部和中途结束默认通过；新增独立「数据与模型管理」主标签，模型、数据版本、标注草稿均有带引用保护的删除预览。无文字的ASR失败条目需补写、重识别或去掉。原计划保持逐字一致，当前行为以更新的USER_GUIDE为准。

后端249 passed/7 skipped；前端build及5项组件通过；浏览器20项通过。真实纯WAV输入扫描91145e4b5db141f882a48f595076e076，STT/草稿6f1298dc7ed2471ea2e851c76c305b95，8.78秒片段，ASR17.312秒；跳过、修正、发布7bb2f91ac7a6463a989910db72466f7c，再从页面删除本次测试草稿和版本成功。原录音副本、6件作品和默认模型不变。证据docs/evidence/UI_WORKFLOW.json；完整本机报告data/ui-review.json及ui-pagination.json。

维护后完整交付审计用：python scripts/validate_delivery.py --phases 13 --allow-maintenance --output data/ui-delivery.json。远端核对结果写data/UI_PUSH_RESULT.json。服务已重启加载草稿删除接口，默认仍为127.0.0.1:7867。

## 删除预览修复（2026-11-18）

已修复「查看删除范围」内容落在页面底部、用户视野外的问题：现在立即弹出原生对话框，加载、错误、重试和确认都在当前视野；取消/Esc可关闭，旧请求不会重新打开或覆盖新对象。前端构建/5项组件/8项专项真实浏览器验证通过；未删除真实业务记录。详见docs/evidence/UI_DELETE_DIALOG.json，最新推送核对写data/UI_DELETE_DIALOG_PUSH_RESULT.json。刷新现有浏览器即可载入，后端无需重启。

## 关联删除维护（2026-11-18）

删除弹窗修复提交7ac47ce之后，用户要求可连同引用删除。管理页现在默认使用?cascade=true冻结关联范围，历史引用、已启用模型、暂停训练与恢复点可一并删除；正在执行的任务和文件传输仍保护。关联对象、文件各5项分页，确认后清理任务/播放器/当前合成等指向。保留用户原录音、共享底模和独立保存的作品；不自动删除模型的上游训练数据。行为以USER_GUIDE的关联范围规则为准，旧计划和历史验收中的保护式删除描述不再代表新版UI。

后端全套262 passed/7 skipped；最后重复确认回执补强后15项专项全部通过；前端build/5组件通过。独立7869数据库中的4项页面实际关联删除通过，CPU夹具不是可推理权重，未进行新的GPU质量验收。正式7867只做预览，19项对象/81文件，未删除业务数据。证据docs/evidence/UI_CASCADE_DELETE.json。正式服务已安全重启加载新后端，刷新浏览器即可使用。最新远端提交以data/UI_CASCADE_PUSH_RESULT.json为准。

复验关联删除：先在API环境运行scripts/prepare_cascade_ui_fixture.py，读取data/cascade-ui-fixture/latest.json的data_root，以scripts/start.ps1 -NoBrowser -Port 7869 -DataRoot该目录启动隔离服务，再运行node scripts/validate_cascade_dialog.cjs（需要含Playwright的测试Node）。脚本硬性核对端口与data_root后才真实DELETE；运行完成后使用相同端口/data_root安全停止。API专项为tests/test_cascade_deletion.py。

维护期间用户在正式页面进行了数据整理与删除，因此上方P12产物ID属于历史验收记录，不保证仍存在。不可为满足旧validate_delivery或依赖固定列表规模的脚本而恢复已删除模型/数据；新的管理流程使用隔离夹具验收。原始录音和独立作品仍须保护。

## 物理 batch 容量（2026-11-18）

用户要求的实机容量测试完成，脚本scripts/benchmark_physical_batch.py和报告docs/evidence/BATCH_CAPACITY.md、JSON可复查。48档真实GPU运行中37通过、11预期OOM；FP16/rank32/累积1/official/最长真实片段重复组成满批次，使用生产模型/loss/优化器，每个上限与建议档均复核12次实际更新。10秒片段S2边界7/8、GPT49/50，建议6/32；当前流萤最长6.86秒S2边界13/14、GPT77/78，建议8/64。前数通过，后数OOM。界面上限仍64，未改默认batch或累积。

源版本：景元76b5a08af4ca471f9c6fa9c903347e69，流萤4a87c1b08a0c411aae29c63e317f3aed。完整本机记录位于data/batch-benchmark/20261118-094659和20261118-095856；脚本可等待应用GPU锁，按当时独立空闲显存减256 MiB限制分配器，最大探测档256。容量会随片段长度、桌面占用、精度和rank变化，不把短训容量当作音质结论。原有流萤特征任务正常完成，音频/底模SHA复核不变，未发布模型，结束后GPU已释放。推送结果另记data/BATCH_CAPACITY_PUSH_RESULT.json。

## 导出队列修复（2026-11-18）

流萤运行11e7b6344fcc41af930651d9c9736076已正常训练完成，原导出07a322f3df85440a9258b5bce1d257af曾在双锁空闲时无人领取。正常CPU调度恢复后已完成，模型8ae85643f5a059f380454c7a3e586687为READY、结构PASS、两权重实读SHA匹配；GPU试听仍NOT_RUN，未重新训练。另一个被用户取消的导出保持取消，不恢复或覆盖用户操作。旧日志缺失，具体触发异常未知；确认并修复的是调度循环缺少异常边界以及吞掉所有OSError的问题。

新服务验收时PID44512、7867端口、data_root为本项目data；运行时以/api/health为准。新增scheduler线程/心跳/错误状态，异常持久化到data/logs/scheduler-errors.jsonl，页面显示自动恢复或未响应。逐队列异常恢复和日志失效等新增6测试通过；全套270 passed/7 skipped，前端build与5组件通过。真实自动重试同一冻结快照保持模型ID/哈希，CPU导出和GPU队列诊断均完成；5浏览器检查通过（健康异常展示为响应夹具，非现场新故障）。

证据docs/evidence/EXPORT_QUEUE_RECOVERY.json和data/scheduler-browser/latest.json。复验脚本scripts/validate_scheduler_recovery.cjs需要显式传入已完成导出ID，且只允许正式7867本项目data：重用不可变快照，不新建模型版本。若用户已删除相关模型/数据，不可为旧脚本恢复；另选仍存在的完成导出。推送记录为data/EXPORT_QUEUE_PUSH_RESULT.json；保留此前网络失败记录及全部本地历史。
