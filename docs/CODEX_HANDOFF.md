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
